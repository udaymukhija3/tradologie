export interface RealtimeAppContext {
  buyer: { id: string; name: string; company: string; market?: string };
  current_page: string;
  selected_distributor_id: string | null;
}

interface RealtimeServerEvent {
  type?: string;
  delta?: string;
  transcript?: string;
  call_id?: string;
  name?: string;
  arguments?: string;
  response?: {
    output?: Array<{
      type?: string;
      name?: string;
      call_id?: string;
      arguments?: string;
      content?: Array<{ transcript?: string }>;
    }>;
  };
  error?: { message?: string; code?: string };
}

interface ToolResultEnvelope {
  request_id: string;
  duration_ms: number;
  result: { status?: string; enquiry_id?: string; [key: string]: unknown };
}

export interface RealtimeCallbacks {
  onReady: (sessionId: string) => void;
  onStatus: (status: string) => void;
  onTrace: (message: string) => void;
  onUserTranscript: (transcript: string) => void;
  onAgentTranscript: (transcript: string) => void;
  onToolCompleted: (name: string, envelope: ToolResultEnvelope) => void;
  onError: (message: string) => void;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { detail?: string };
    return body.detail || fallback;
  } catch {
    return fallback;
  }
}

export class OpenAIRealtimeClient {
  private peerConnection: RTCPeerConnection | null = null;
  private dataChannel: RTCDataChannel | null = null;
  private localStream: MediaStream | null = null;
  private remoteAudio: HTMLAudioElement | null = null;
  private sessionId: string | null = null;
  private callbacks: RealtimeCallbacks;
  private accessToken: string;
  private closed = false;
  private microphoneMuted = false;
  private assistantTranscript = '';
  private emittedAssistantTranscript = '';
  private processedToolCalls = new Set<string>();
  private transcriptObservation = Promise.resolve();
  private turnStartedAt: number | null = null;

  constructor(callbacks: RealtimeCallbacks, accessToken: string) {
    this.callbacks = callbacks;
    this.accessToken = accessToken;
  }

  private jsonHeaders(): Record<string, string> {
    return { 'Content-Type': 'application/json', Authorization: `Bearer ${this.accessToken}` };
  }

  static isSupported(): boolean {
    return typeof navigator.mediaDevices?.getUserMedia === 'function'
      && typeof RTCPeerConnection !== 'undefined';
  }

  async connect(context: RealtimeAppContext): Promise<void> {
    if (!OpenAIRealtimeClient.isSupported()) {
      throw new Error('This browser does not support WebRTC microphone sessions.');
    }
    this.closed = false;
    this.callbacks.onStatus('Requesting microphone');
    this.callbacks.onTrace('microphone_permission_requested');

    try {
      this.localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
    } catch (error) {
      this.callbacks.onTrace('microphone_permission_failed');
      throw new Error(`Microphone access failed: ${String(error)}`);
    }

    this.callbacks.onTrace(`microphone_started tracks=${this.localStream.getAudioTracks().length}`);
    this.callbacks.onStatus('Connecting');

    const peerConnection = new RTCPeerConnection();
    this.peerConnection = peerConnection;

    this.remoteAudio = new Audio();
    this.remoteAudio.autoplay = true;
    peerConnection.ontrack = (event) => {
      if (!this.remoteAudio) return;
      this.remoteAudio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      void this.remoteAudio.play().then(
        () => this.callbacks.onTrace('remote_audio_track_playing'),
        () => this.callbacks.onTrace('remote_audio_autoplay_blocked'),
      );
    };

    for (const track of this.localStream.getAudioTracks()) {
      peerConnection.addTrack(track, this.localStream);
    }

    peerConnection.onconnectionstatechange = () => {
      this.callbacks.onTrace(`peer_connection_state ${peerConnection.connectionState}`);
      if (peerConnection.connectionState === 'failed') {
        this.callbacks.onError('The realtime media connection failed.');
      }
    };

    const dataChannel = peerConnection.createDataChannel('oai-events');
    this.dataChannel = dataChannel;
    dataChannel.onopen = () => {
      this.callbacks.onTrace('realtime_data_channel_open');
      this.callbacks.onStatus('Ready');
    };
    dataChannel.onclose = () => this.callbacks.onTrace('realtime_data_channel_closed');
    dataChannel.onerror = () => this.callbacks.onError('The realtime event channel failed.');
    dataChannel.onmessage = (event) => this.handleDataChannelEvent(event.data);

    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    if (!offer.sdp) throw new Error('The browser did not produce a WebRTC offer.');

    const response = await fetch('/api/realtime/call', {
      method: 'POST',
      headers: this.jsonHeaders(),
      body: JSON.stringify({ sdp: offer.sdp, context }),
    });
    if (!response.ok) {
      throw new Error(await responseError(response, `Realtime setup failed (${response.status}).`));
    }

    const sessionId = response.headers.get('x-tradevoice-session-id');
    if (!sessionId) throw new Error('The backend did not return a realtime session ID.');
    this.sessionId = sessionId;
    const answerSdp = await response.text();
    await peerConnection.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    this.callbacks.onTrace('webrtc_answer_applied');
    this.callbacks.onReady(sessionId);
  }

  sendText(text: string): boolean {
    this.turnStartedAt = performance.now();
    this.transcriptObservation = this.recordUserTranscript(text);
    if (!this.sendEvent({
      type: 'conversation.item.create',
      item: {
        type: 'message',
        role: 'user',
        content: [{ type: 'input_text', text }],
      },
    })) return false;
    return this.sendEvent({ type: 'response.create' });
  }

  setMicrophoneMuted(muted: boolean): void {
    this.microphoneMuted = muted;
    for (const track of this.localStream?.getAudioTracks() ?? []) {
      track.enabled = !muted;
    }
    this.callbacks.onTrace(muted ? 'microphone_muted' : 'microphone_unmuted');
    this.callbacks.onStatus(muted ? 'Muted' : 'Ready');
  }

  isMicrophoneMuted(): boolean {
    return this.microphoneMuted;
  }

  async updateContext(context: RealtimeAppContext): Promise<void> {
    if (!this.sessionId) return;
    const response = await fetch(`/api/realtime/sessions/${this.sessionId}/context`, {
      method: 'PATCH',
      headers: this.jsonHeaders(),
      body: JSON.stringify({ context }),
    });
    if (!response.ok) {
      this.callbacks.onTrace(`context_update_failed status=${response.status}`);
      return;
    }
    this.callbacks.onTrace(`context_updated selected=${context.selected_distributor_id ?? 'none'}`);
    this.sendEvent({
      type: 'conversation.item.create',
      item: {
        type: 'message',
        role: 'user',
        content: [{
          type: 'input_text',
          text: `[Application context changed: selected_distributor_id=${context.selected_distributor_id ?? 'none'}]`,
        }],
      },
    });
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    const sessionId = this.sessionId;
    this.sessionId = null;

    this.dataChannel?.close();
    this.dataChannel = null;
    this.peerConnection?.close();
    this.peerConnection = null;

    for (const track of this.localStream?.getTracks() ?? []) track.stop();
    this.localStream = null;
    if (this.remoteAudio) {
      this.remoteAudio.pause();
      this.remoteAudio.srcObject = null;
      this.remoteAudio.removeAttribute('src');
      this.remoteAudio.load();
    }
    this.remoteAudio = null;
    this.callbacks.onTrace('media_resources_released');

    if (sessionId) {
      try {
        await fetch(`/api/realtime/sessions/${sessionId}`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${this.accessToken}` },
          keepalive: true,
        });
      } catch {
        this.callbacks.onTrace('session_cleanup_request_failed');
      }
    }
  }

  private sendEvent(event: Record<string, unknown>): boolean {
    if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
      this.callbacks.onError('The realtime session is not ready.');
      return false;
    }
    this.dataChannel.send(JSON.stringify(event));
    return true;
  }

  private handleDataChannelEvent(raw: unknown): void {
    if (typeof raw !== 'string') {
      this.callbacks.onTrace('non_text_realtime_event_ignored');
      return;
    }
    let event: RealtimeServerEvent;
    try {
      event = JSON.parse(raw) as RealtimeServerEvent;
    } catch {
      this.callbacks.onTrace('invalid_realtime_event');
      return;
    }
    const type = event.type ?? 'unknown';

    if (type === 'session.created' || type === 'session.updated') {
      this.callbacks.onTrace(type);
    } else if (type === 'input_audio_buffer.speech_started') {
      this.callbacks.onStatus('Listening');
      this.callbacks.onTrace('speech_started server_vad');
    } else if (type === 'input_audio_buffer.speech_stopped') {
      this.turnStartedAt = performance.now();
      this.callbacks.onStatus('Processing');
      this.callbacks.onTrace('speech_stopped server_vad');
    } else if (type === 'response.created') {
      const latency = this.turnStartedAt === null ? null : Math.round(performance.now() - this.turnStartedAt);
      this.callbacks.onTrace(`response_started${latency === null ? '' : ` ${latency}ms`}`);
    } else if (type === 'conversation.item.input_audio_transcription.completed') {
      const transcript = event.transcript?.trim();
      if (transcript) {
        this.callbacks.onUserTranscript(transcript);
        this.transcriptObservation = this.recordUserTranscript(transcript);
      }
    } else if (type === 'response.output_audio_transcript.delta') {
      this.assistantTranscript += event.delta ?? '';
    } else if (type === 'response.output_audio_transcript.done') {
      const transcript = (event.transcript ?? this.assistantTranscript).trim();
      this.emitAgentTranscript(transcript);
      this.assistantTranscript = '';
    } else if (type === 'response.audio_transcript.delta') {
      this.assistantTranscript += event.delta ?? '';
    } else if (type === 'response.audio_transcript.done') {
      const transcript = (event.transcript ?? this.assistantTranscript).trim();
      this.emitAgentTranscript(transcript);
      this.assistantTranscript = '';
    } else if (type === 'response.function_call_arguments.done') {
      void this.handleToolCall(event);
    } else if (type === 'output_audio_buffer.started') {
      this.callbacks.onStatus('Speaking');
      this.callbacks.onTrace('audio_playback_started remote_webrtc');
    } else if (type === 'output_audio_buffer.stopped') {
      this.callbacks.onStatus('Ready');
      this.callbacks.onTrace('audio_playback_completed remote_webrtc');
    } else if (type === 'response.done') {
      this.recoverCompletedResponse(event);
      if (!this.microphoneMuted) this.callbacks.onStatus('Ready');
    } else if (type === 'error') {
      const safeCode = event.error?.code ?? 'realtime_error';
      this.callbacks.onTrace(`openai_error code=${safeCode}`);
      this.callbacks.onError(event.error?.message ?? 'The realtime model returned an error.');
    }
  }

  private recoverCompletedResponse(event: RealtimeServerEvent): void {
    for (const item of event.response?.output ?? []) {
      if (item.type === 'function_call' && item.call_id && item.name && item.arguments) {
        void this.handleToolCall({
          type: 'response.function_call_arguments.done',
          call_id: item.call_id,
          name: item.name,
          arguments: item.arguments,
        });
      }
      const transcript = item.content?.map((content) => content.transcript ?? '').join('').trim();
      if (transcript) this.emitAgentTranscript(transcript);
    }
  }

  private emitAgentTranscript(transcript: string): void {
    if (!transcript || transcript === this.emittedAssistantTranscript) return;
    this.emittedAssistantTranscript = transcript;
    this.callbacks.onAgentTranscript(transcript);
  }

  private async recordUserTranscript(transcript: string): Promise<void> {
    if (!this.sessionId) return;
    try {
      await fetch(`/api/realtime/sessions/${this.sessionId}/events`, {
        method: 'POST',
        headers: this.jsonHeaders(),
        body: JSON.stringify({ type: 'user_transcript', transcript }),
      });
    } catch {
      this.callbacks.onTrace('transcript_observation_failed');
    }
  }

  private async handleToolCall(event: RealtimeServerEvent): Promise<void> {
    if (!this.sessionId || !event.call_id || !event.name) {
      this.callbacks.onTrace('invalid_tool_call_event');
      return;
    }
    if (this.processedToolCalls.has(event.call_id)) return;
    this.processedToolCalls.add(event.call_id);
    await this.transcriptObservation;
    let argumentsObject: Record<string, unknown>;
    try {
      const parsed = JSON.parse(event.arguments ?? '{}') as unknown;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
      argumentsObject = parsed as Record<string, unknown>;
    } catch {
      argumentsObject = {};
    }

    this.callbacks.onStatus('Using Tool');
    this.callbacks.onTrace(`tool_requested ${event.name} call=${event.call_id}`);
    this.callbacks.onTrace(`tool_started ${event.name} backend_dispatch`);
    const requestStartedAt = performance.now();
    try {
      const response = await fetch(`/api/realtime/sessions/${this.sessionId}/tools`, {
        method: 'POST',
        headers: this.jsonHeaders(),
        body: JSON.stringify({
          call_id: event.call_id,
          name: event.name,
          arguments: argumentsObject,
        }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response, `Tool request failed (${response.status}).`));
      }
      const envelope = await response.json() as ToolResultEnvelope;
      const roundTripMs = Math.round(performance.now() - requestStartedAt);
      this.callbacks.onTrace(
        `tool_completed ${event.name} backend=${envelope.duration_ms}ms roundtrip=${roundTripMs}ms status=${envelope.result.status ?? 'unknown'}`,
      );
      this.callbacks.onToolCompleted(event.name, envelope);
      this.sendEvent({
        type: 'conversation.item.create',
        item: {
          type: 'function_call_output',
          call_id: event.call_id,
          output: JSON.stringify(envelope.result),
        },
      });
      this.sendEvent({ type: 'response.create' });
      this.callbacks.onTrace('tool_result_returned_to_model');
    } catch (error) {
      this.callbacks.onError(`Business tool execution failed: ${String(error)}`);
    }
  }
}
