import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { OpenAIRealtimeClient, type RealtimeAppContext } from '../voice/openaiRealtime';
import { BrowserSpeechAdapter, describeSelectedVoice, primeVoices, speakText, stopSpeech } from '../voice/speech';

interface Props {
  context: AppContext;
  accessToken: string;
  onClose: () => void;
  onEnquiryCreated: () => void;
}

interface AppContext extends RealtimeAppContext {}

interface Message {
  id: number;
  sender: 'you' | 'agent';
  text: string;
}

interface TraceEvent {
  id: number;
  time: string;
  text: string;
}

interface RuntimeConfig {
  voice_mode: 'mock' | 'openai' | string;
  engine_label: string;
  speech_transport: string;
  model?: string;
  configured?: boolean;
}

interface ServerEvent {
  type: string;
  name?: string;
  text?: string;
  message?: string;
  engine?: string;
  request_id?: string;
  duration_ms?: number;
  latency_ms?: number;
  selected_distributor_id?: string | null;
  result?: { status?: string; enquiry_id?: string };
}

const MAX_MESSAGES = 100;
const MAX_TRACES = 250;
const EXAMPLE_PROMPTS = [
  'Tell me about this distributor.',
  'Find me a basmati rice supplier in Punjab.',
  "What's happening with ENQ-1001?",
  'I need 50 tonnes of basmati rice for Dubai.',
  'I want to speak to someone.',
];

function voiceWebSocketUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/voice`;
}

export const VoicePanel = ({ context, accessToken, onClose, onEnquiryCreated }: Props) => {
  const [status, setStatus] = useState('Idle');
  const [messages, setMessages] = useState<Message[]>([]);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [textInput, setTextInput] = useState('');
  const [interimText, setInterimText] = useState('');
  const [isConnected, setIsConnected] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [runtimeLoaded, setRuntimeLoaded] = useState(false);
  const [lastError, setLastError] = useState('');
  const [speechSupported, setSpeechSupported] = useState(() => BrowserSpeechAdapter.isSupported());
  const [runtime, setRuntime] = useState<RuntimeConfig>({
    voice_mode: 'mock',
    engine_label: 'Local Demo Engine',
    speech_transport: 'browser_speech',
  });

  const wsRef = useRef<WebSocket | null>(null);
  const realtimeRef = useRef<OpenAIRealtimeClient | null>(null);
  const speechRef = useRef<BrowserSpeechAdapter | null>(null);
  // Wall-clock start of the current turn, used to report the only latency
  // number that matters to a caller: input committed -> first audible audio.
  const turnStartRef = useRef<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const nextIdRef = useRef(1);
  const voiceSubmittedRef = useRef(false);
  const isRealtime = runtime.voice_mode === 'openai';

  const addTrace = useCallback((text: string) => {
    const time = new Date().toISOString().split('T')[1].slice(0, 12);
    const id = nextIdRef.current++;
    setTraces((previous) => [...previous, { id, time, text }].slice(-MAX_TRACES));
  }, []);

  const addMessage = useCallback((sender: 'you' | 'agent', text: string) => {
    const id = nextIdRef.current++;
    setMessages((previous) => [...previous, { id, sender, text }].slice(-MAX_MESSAGES));
  }, []);

  const releaseMockMedia = useCallback(() => {
    speechRef.current?.close();
    speechRef.current = null;
    stopSpeech();
    setIsListening(false);
    setInterimText('');
  }, []);

  const sendUserText = useCallback((text: string, channel: 'text' | 'voice' = 'text') => {
    const cleanText = text.trim();
    if (!cleanText) return;
    turnStartRef.current = performance.now();
    if (realtimeRef.current) {
      stopSpeech();
      addMessage('you', cleanText);
      addTrace(`input_submitted channel=${channel} transport=realtime_data_channel`);
      realtimeRef.current.sendText(cleanText);
      setTextInput('');
      setInterimText('');
      setStatus('Processing');
      return;
    }
    const websocket = wsRef.current;
    if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
    stopSpeech();
    addMessage('you', cleanText);
    addTrace(`input_submitted channel=${channel}`);
    websocket.send(JSON.stringify({ type: 'text', text: cleanText }));
    setTextInput('');
    setInterimText('');
    setStatus('Processing');
  }, [addMessage, addTrace]);

  useEffect(() => {
    primeVoices();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/runtime', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Runtime request failed: ${response.status}`);
        return response.json() as Promise<RuntimeConfig>;
      })
      .then((config) => {
        setRuntime(config);
        setSpeechSupported(config.voice_mode === 'openai'
          ? OpenAIRealtimeClient.isSupported()
          : BrowserSpeechAdapter.isSupported());
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        addTrace('runtime_config_unavailable using_local_defaults');
      })
      .finally(() => setRuntimeLoaded(true));
    return () => controller.abort();
  }, [addTrace]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, interimText]);

  useEffect(() => {
    if (!isConnected) return;
    if (realtimeRef.current) {
      void realtimeRef.current.updateContext(context);
      return;
    }
    const websocket = wsRef.current;
    if (websocket?.readyState === WebSocket.OPEN) {
      websocket.send(JSON.stringify({ type: 'context_update', context }));
      addTrace(`context_update_submitted selected=${context.selected_distributor_id ?? 'none'}`);
    }
  }, [addTrace, context, isConnected]);

  useEffect(() => {
    return () => {
      const websocket = wsRef.current;
      if (websocket) {
        websocket.onopen = null;
        websocket.onmessage = null;
        websocket.onerror = null;
        websocket.onclose = null;
        websocket.close(1000, 'Panel unmounted');
      }
      wsRef.current = null;
      void realtimeRef.current?.close();
      realtimeRef.current = null;
      speechRef.current?.close();
      speechRef.current = null;
      stopSpeech();
    };
  }, []);

  const startRealtimeSession = async () => {
    if (realtimeRef.current) return;
    setStatus('Connecting');
    setLastError('');
    addTrace('session_connecting transport=webrtc');
    const client = new OpenAIRealtimeClient({
      onReady: (sessionId) => {
        setIsConnected(true);
        setStatus('Ready');
        addTrace(`session_started engine=openai_realtime session=${sessionId.slice(0, 8)}`);
      },
      onStatus: setStatus,
      onTrace: addTrace,
      onUserTranscript: (transcript) => addMessage('you', transcript),
      onAgentTranscript: (transcript) => addMessage('agent', transcript),
      onToolCompleted: (name, envelope) => {
        if (name === 'create_enquiry' && envelope.result.status === 'success') onEnquiryCreated();
      },
      onError: (message) => {
        setLastError(message);
        setStatus('Error');
        addTrace(`realtime_error ${message}`);
      },
    }, accessToken);
    realtimeRef.current = client;
    try {
      await client.connect(context);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setLastError(message);
      setStatus('Error');
      addTrace(`session_start_failed ${message}`);
      await client.close();
      if (realtimeRef.current === client) realtimeRef.current = null;
      setIsConnected(false);
    }
  };

  const startMockSession = () => {
    if (wsRef.current) return;
    setStatus('Connecting');
    setLastError('');
    addTrace('session_connecting');
    const websocket = new WebSocket(voiceWebSocketUrl());
    wsRef.current = websocket;
    websocket.onopen = () => {
      addTrace('websocket_connected');
      websocket.send(JSON.stringify({ context, access_token: accessToken }));
    };
    websocket.onmessage = (event) => {
      if (typeof event.data !== 'string') {
        addTrace('binary_response_received');
        return;
      }
      let data: ServerEvent;
      try {
        data = JSON.parse(event.data) as ServerEvent;
      } catch {
        addTrace('invalid_server_event');
        setStatus('Error');
        return;
      }
      if (data.type === 'session_started') {
        setIsConnected(true);
        setStatus('Ready');
        addTrace(`session_started engine=${data.engine ?? 'unknown'}`);
      } else if (data.type === 'agent_response' && data.text) {
        addMessage('agent', data.text);
        const canSpeak = speakText(data.text, {
          onStart: () => {
            setStatus('Speaking');
            const startedAt = turnStartRef.current;
            const firstAudioMs = startedAt === null
              ? null
              : Math.round(performance.now() - startedAt);
            turnStartRef.current = null;
            addTrace(
              firstAudioMs === null
                ? 'audio_playback_started browser_tts'
                : `audio_playback_started browser_tts first_audio=${firstAudioMs}ms`,
            );
          },
          onEnd: () => {
            setStatus('Ready');
            addTrace('audio_playback_completed');
          },
          onError: (reason) => {
            setStatus('Ready');
            const eventName = reason === 'interrupted' || reason === 'canceled'
              ? 'audio_playback_interrupted'
              : `audio_playback_failed reason=${reason}`;
            addTrace(eventName);
          },
        });
        if (!canSpeak) {
          setStatus('Ready');
          addTrace('audio_playback_unavailable');
        }
      } else if (data.type === 'tool_requested') {
        addTrace(`tool_requested ${data.name} request=${data.request_id}`);
        setStatus('Using Tool');
      } else if (data.type === 'tool_started') {
        addTrace(`tool_started ${data.name}`);
      } else if (data.type === 'tool_completed') {
        addTrace(`tool_completed ${data.name} ${data.duration_ms ?? 0}ms status=${data.result?.status ?? 'unknown'}`);
        if (data.name === 'create_enquiry' && data.result?.status === 'success') onEnquiryCreated();
      } else if (data.type === 'confirmation_required') {
        addTrace('confirmation_required create_enquiry');
      } else if (data.type === 'response_started') {
        addTrace(`response_started ${data.latency_ms ?? 0}ms`);
      } else if (data.type === 'context_updated') {
        addTrace(`context_updated selected=${data.selected_distributor_id ?? 'none'}`);
      } else if (data.type === 'media_ignored') {
        addTrace('media_ignored local_demo_engine');
      } else if (data.type === 'error') {
        const message = data.message ?? 'Unknown server error';
        setLastError(message);
        addTrace(`error ${message}`);
        setStatus('Error');
      }
    };
    websocket.onerror = () => {
      setLastError('The local WebSocket connection failed.');
      addTrace('websocket_error');
      setStatus('Error');
    };
    websocket.onclose = (event) => {
      if (wsRef.current !== websocket) return;
      wsRef.current = null;
      releaseMockMedia();
      setIsConnected(false);
      setStatus(event.code === 1000 ? 'Ended' : 'Disconnected');
      addTrace(`websocket_closed code=${event.code}`);
    };
  };

  const startSession = () => {
    if (isRealtime) void startRealtimeSession();
    else startMockSession();
  };

  const endSession = async () => {
    const realtime = realtimeRef.current;
    realtimeRef.current = null;
    if (realtime) await realtime.close();
    const websocket = wsRef.current;
    wsRef.current = null;
    if (websocket && websocket.readyState < WebSocket.CLOSING) {
      websocket.close(1000, 'User ended conversation');
    }
    releaseMockMedia();
    setIsConnected(false);
    setIsMuted(false);
    setStatus('Ended');
    addTrace('session_ended_by_user');
  };

  const startVoiceInput = () => {
    if (!isConnected || isListening) return;
    if (!BrowserSpeechAdapter.isSupported()) {
      setSpeechSupported(false);
      setStatus('Voice unavailable');
      addTrace('speech_recognition_unavailable');
      return;
    }
    stopSpeech();
    voiceSubmittedRef.current = false;
    speechRef.current?.close();
    const adapter = new BrowserSpeechAdapter({
      onStart: () => {
        setIsListening(true);
        setStatus('Listening');
        addTrace('microphone_started browser_speech');
      },
      onSpeechStart: () => {
        stopSpeech();
        addTrace('speech_started playback_interrupted');
      },
      onInterim: setInterimText,
      onFinal: (transcript) => {
        voiceSubmittedRef.current = true;
        addTrace('speech_recognized final');
        sendUserText(transcript, 'voice');
      },
      onError: (message) => {
        setStatus('Voice error');
        addTrace(`speech_recognition_error ${message}`);
      },
      onEnd: () => {
        setIsListening(false);
        setInterimText('');
        addTrace('microphone_stopped');
        if (!voiceSubmittedRef.current) setStatus('Ready');
      },
    });
    speechRef.current = adapter;
    try {
      adapter.start();
    } catch (error) {
      setStatus('Voice error');
      addTrace(`speech_recognition_error ${String(error)}`);
    }
  };

  const toggleRealtimeMicrophone = () => {
    const client = realtimeRef.current;
    if (!client) return;
    const nextMuted = !client.isMicrophoneMuted();
    client.setMicrophoneMuted(nextMuted);
    setIsMuted(nextMuted);
  };

  const submitForm = (event: FormEvent) => {
    event.preventDefault();
    sendUserText(textInput);
  };

  const closePanel = () => {
    if (wsRef.current || realtimeRef.current || isConnected) void endSession();
    onClose();
  };

  const modeLabel = isRealtime ? 'REAL AI · OPENAI WEBRTC' : 'LOCAL DEMO · BROWSER SPEECH';
  const cannotStart = !runtimeLoaded || (isRealtime && runtime.configured === false);

  return (
    <aside className="voice-panel-container" aria-label="Voice support panel">
      <div className="voice-panel-header">
        <div>
          <h2>Talk to Support</h2>
          <span className="mode-badge">{modeLabel}</span>
        </div>
        <button className="icon-button" onClick={closePanel} aria-label="Close support panel">✕</button>
      </div>
      <div className="runtime-strip">
        <span>{runtime.engine_label}{runtime.model ? ` · ${runtime.model}` : ''}</span>
        <span>{isRealtime ? 'Live microphone audio' : 'Dummy marketplace data'}</span>
      </div>
      <div className="controls">
        {!isConnected ? (
          <button className="btn btn-primary" onClick={startSession} disabled={cannotStart}>Start Conversation</button>
        ) : (
          <>
            {isRealtime ? (
              <button className={`btn ${isMuted ? 'btn-primary' : 'btn-listening'}`} onClick={toggleRealtimeMicrophone}>
                {isMuted ? 'Unmute Microphone' : 'Mute Microphone'}
              </button>
            ) : (
              <button
                className={`btn ${isListening ? 'btn-listening' : 'btn-primary'}`}
                onClick={isListening ? () => speechRef.current?.stop() : startVoiceInput}
                disabled={!speechSupported}
              >
                {isListening ? 'Stop Listening' : 'Push to Talk'}
              </button>
            )}
            <button className="btn btn-danger" onClick={() => void endSession()}>End</button>
          </>
        )}
      </div>
      <div className="status-row" aria-live="polite">
        <span className={`status-badge ${status.toLowerCase().split(' ')[0]}`}>{status}</span>
        {!speechSupported && <span className="status-help">This browser cannot start the selected voice mode. Use text in mock mode.</span>}
        {isRealtime && runtime.configured === false && (
          <span className="status-help">Set OPENAI_API_KEY in backend/.env, then restart the backend.</span>
        )}
        {lastError && <span className="status-help">{lastError}</span>}
      </div>
      <div className="example-prompts" aria-label="Demo prompts">
        {EXAMPLE_PROMPTS.map((prompt) => (
          <button key={prompt} disabled={!isConnected} onClick={() => sendUserText(prompt)}>{prompt}</button>
        ))}
      </div>
      <div className="transcript" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty-transcript">
            {isRealtime
              ? 'Start the conversation and speak naturally, or choose a demo prompt.'
              : 'Start the conversation, then push to talk or choose a demo prompt.'}
          </div>
        )}
        {messages.map((message) => (
          <div key={message.id} className={`message ${message.sender}`}>
            <div className="message-label">{message.sender === 'you' ? 'YOU' : 'TRADEVOICE'}</div>
            <div>{message.text}</div>
          </div>
        ))}
        {interimText && (
          <div className="message you interim">
            <div className="message-label">LISTENING</div>
            <div>{interimText}</div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <form className="input-area" onSubmit={submitForm}>
        <label className="sr-only" htmlFor="fallback-message">Fallback message</label>
        <input
          id="fallback-message"
          type="text"
          placeholder="Type a fallback message..."
          value={textInput}
          onChange={(event) => setTextInput(event.target.value)}
          disabled={!isConnected}
          maxLength={4096}
        />
        <button type="submit" disabled={!isConnected || !textInput.trim()}>Send</button>
      </form>
      <div className="dev-trace">
        <h3>Developer Trace</h3>
        <div>Context: {context.selected_distributor_id || 'None selected'}</div>
        <div>Engine: {runtime.voice_mode}</div>
        <div>Transport: {runtime.speech_transport}</div>
        {runtime.speech_transport === 'browser_speech' && (
          <div>Voice: {describeSelectedVoice()}</div>
        )}
        <hr />
        {traces.map((trace) => (
          <div key={trace.id} className="trace-line">
            <span className="trace-time">{trace.time}</span>
            <span>{trace.text}</span>
          </div>
        ))}
      </div>
    </aside>
  );
};
