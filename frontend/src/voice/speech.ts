type RecognitionResult = {
  isFinal: boolean;
  readonly length: number;
  readonly [index: number]: { transcript: string };
};

type RecognitionEvent = Event & {
  resultIndex: number;
  results: {
    readonly length: number;
    readonly [index: number]: RecognitionResult;
  };
};

type RecognitionErrorEvent = Event & { error: string };

interface RecognitionInstance {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onspeechstart: (() => void) | null;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type RecognitionConstructor = new () => RecognitionInstance;

declare global {
  interface Window {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  }
}

export interface SpeechCallbacks {
  onStart: () => void;
  onSpeechStart: () => void;
  onInterim: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (message: string) => void;
  onEnd: () => void;
}

export class BrowserSpeechAdapter {
  private recognition: RecognitionInstance | null = null;
  private listening = false;

  private finalParts: string[] = [];
  private turnTimer: number | null = null;

  /**
   * @param endOfTurnMs silence after the last recognised audio before the turn
   *   is considered finished. The Web Speech API marks a result final at the
   *   first pause it detects, which inside a sentence is a breath rather than
   *   an end of turn, so submitting on that flag alone cuts speakers off.
   */
  private endOfTurnMs: number;

  constructor(callbacks: SpeechCallbacks, language = 'en-IN', endOfTurnMs = 1500) {
    this.endOfTurnMs = endOfTurnMs;

    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition) {
      return;
    }

    this.recognition = new Recognition();
    // Keep the session open across pauses; end-of-turn is decided by the
    // silence timer below, not by the first final result.
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.lang = language;
    this.recognition.onstart = () => {
      this.listening = true;
      callbacks.onStart();
    };
    this.recognition.onspeechstart = callbacks.onSpeechStart;
    this.recognition.onresult = (event) => {
      let interim = '';
      const finalParts: string[] = [];

      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = result[0]?.transcript?.trim() ?? '';
        if (!transcript) continue;
        if (result.isFinal) finalParts.push(transcript);
        else interim += `${transcript} `;
      }

      callbacks.onInterim(interim.trim());
      if (finalParts.length > 0) this.finalParts.push(...finalParts);

      // Any recognised audio, interim or final, restarts the clock.
      if (this.turnTimer !== null) window.clearTimeout(this.turnTimer);
      this.turnTimer = window.setTimeout(() => {
        this.turnTimer = null;
        const spoken = this.finalParts.join(' ').trim();
        this.finalParts = [];
        if (spoken) {
          callbacks.onFinal(spoken);
          this.recognition?.stop();
        }
      }, this.endOfTurnMs);
    };
    this.recognition.onerror = (event) => {
      const friendly = event.error === 'not-allowed'
        ? 'Microphone permission was denied. Text input is still available.'
        : `Speech recognition error: ${event.error}.`;
      callbacks.onError(friendly);
    };
    this.recognition.onend = () => {
      this.listening = false;
      if (this.turnTimer !== null) {
        window.clearTimeout(this.turnTimer);
        this.turnTimer = null;
      }
      // Flush anything captured before the browser closed the session itself.
      const spoken = this.finalParts.join(' ').trim();
      this.finalParts = [];
      if (spoken) callbacks.onFinal(spoken);
      callbacks.onEnd();
    };
  }

  static isSupported(): boolean {
    return Boolean(window.SpeechRecognition ?? window.webkitSpeechRecognition);
  }

  isActive(): boolean {
    return this.listening;
  }

  start(): void {
    if (!this.recognition) {
      throw new Error('Browser speech recognition is not supported.');
    }
    this.recognition.start();
  }

  stop(): void {
    this.recognition?.stop();
  }

  close(): void {
    if (this.turnTimer !== null) {
      window.clearTimeout(this.turnTimer);
      this.turnTimer = null;
    }
    this.finalParts = [];
    this.recognition?.abort();
    this.listening = false;
  }
}

export interface PlaybackCallbacks {
  onStart: () => void;
  onEnd: () => void;
  onError: (reason: string) => void;
}

// Ranked preference list. The browser default is usually the lowest-quality
// voice installed, which is the single biggest reason local TTS sounds robotic.
// Local voices are preferred over network voices: a network voice costs an
// extra round trip before the first audio sample, which we cannot afford.
const PREFERRED_VOICES = [
  'Ava (Premium)',
  'Zoe (Premium)',
  'Evan (Premium)',
  'Ava (Enhanced)',
  'Serena (Premium)',
  'Serena (Enhanced)',
  'Allison',
  'Samantha',
  'Rishi',
  'Google UK English Female',
  'Google US English',
];

let cachedVoice: SpeechSynthesisVoice | null = null;
let cachedVoiceResolved = false;

function scoreVoice(voice: SpeechSynthesisVoice): number {
  const index = PREFERRED_VOICES.indexOf(voice.name);
  if (index >= 0) {
    // Earlier entries win; local voices get a further bonus for latency.
    return 1000 - index * 10 + (voice.localService ? 5 : 0);
  }
  if (!/^en(-|$)/i.test(voice.lang)) return -1;
  let score = 100;
  if (/premium|enhanced|neural/i.test(voice.name)) score += 40;
  if (voice.localService) score += 20;
  if (/^en-IN/i.test(voice.lang)) score += 10;
  return score;
}

export function selectBestVoice(): SpeechSynthesisVoice | null {
  if (cachedVoiceResolved && cachedVoice) return cachedVoice;
  if (!('speechSynthesis' in window)) return null;

  const voices = window.speechSynthesis.getVoices();
  // getVoices() is empty until the engine populates asynchronously.
  if (voices.length === 0) return null;

  let best: SpeechSynthesisVoice | null = null;
  let bestScore = -Infinity;
  for (const voice of voices) {
    const score = scoreVoice(voice);
    if (score > bestScore) {
      bestScore = score;
      best = voice;
    }
  }

  cachedVoice = best;
  cachedVoiceResolved = true;
  return best;
}

export function primeVoices(): void {
  if (!('speechSynthesis' in window)) return;
  // Populate the voice list ahead of the first utterance so voice selection
  // never delays time-to-first-audio.
  selectBestVoice();
  window.speechSynthesis.addEventListener('voiceschanged', () => {
    cachedVoiceResolved = false;
    selectBestVoice();
  }, { once: true });
}

export function describeSelectedVoice(): string {
  const voice = selectBestVoice();
  if (!voice) return 'system default';
  return `${voice.name}${voice.localService ? ' (local)' : ' (network)'}`;
}

export function speakText(text: string, callbacks: PlaybackCallbacks): boolean {
  if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
    return false;
  }

  // Only cancel when something is actually playing. An unconditional cancel()
  // immediately before speak() costs a scheduler tick in Chromium.
  if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
    window.speechSynthesis.cancel();
  }

  const utterance = new SpeechSynthesisUtterance(text);
  const voice = selectBestVoice();
  if (voice) {
    utterance.voice = voice;
    utterance.lang = voice.lang;
  } else {
    utterance.lang = 'en-IN';
  }
  // Slightly above natural pace: shortens each turn without sounding rushed.
  utterance.rate = 1.05;
  utterance.pitch = 1;
  utterance.onstart = callbacks.onStart;
  utterance.onend = callbacks.onEnd;
  utterance.onerror = (event) => callbacks.onError(event.error);
  window.speechSynthesis.speak(utterance);
  return true;
}

export function stopSpeech(): void {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
}
