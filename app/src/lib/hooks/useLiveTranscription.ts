import { useCallback, useEffect, useRef, useState } from 'react';
import { useServerStore } from '@/stores/serverStore';

/**
 * Live speech-to-text over the backend's `/ws/transcribe` WebSocket.
 *
 * Captures the microphone through an AudioWorklet at 16 kHz, converts each
 * block to signed 16-bit little-endian PCM, and streams it to the backend,
 * which bridges to the resident Voxtral streaming-STT daemon. Incoming
 * `partial` events update the interim hypothesis (rendered greyed); `final`
 * events are appended to the committed transcript.
 *
 * Mirrors the Web Audio / Int16 PCM convention used by `useStreamingTTS` (just
 * in the opposite direction — capture instead of playback).
 */

const SAMPLE_RATE = 16000; // must match the daemon's expected input rate
const FRAME_SAMPLES = 2048; // ~128ms per posted block at 16kHz

export type LiveStatus =
  | 'idle'
  | 'connecting'
  | 'loading' // daemon starting / model loading
  | 'listening'
  | 'error';

// AudioWorklet processor: buffers mic input into fixed-size Float32 blocks and
// posts them to the main thread. Loaded from a Blob URL so it needs no separate
// asset file (Tauri enforces no CSP, so blob: worklets are allowed).
const CAPTURE_WORKLET = `
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._chunks = [];
    this._queued = 0;
    this._target = ${FRAME_SAMPLES};
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      this._chunks.push(Float32Array.from(channel)); // copy: input is reused
      this._queued += channel.length;
      while (this._queued >= this._target) {
        const out = new Float32Array(this._target);
        let offset = 0;
        while (offset < this._target) {
          const head = this._chunks[0];
          const need = this._target - offset;
          if (head.length <= need) {
            out.set(head, offset);
            offset += head.length;
            this._chunks.shift();
            this._queued -= head.length;
          } else {
            out.set(head.subarray(0, need), offset);
            this._chunks[0] = head.subarray(need);
            this._queued -= need;
            offset += need;
          }
        }
        this.port.postMessage(out, [out.buffer]);
      }
    }
    return true;
  }
}
registerProcessor('capture-processor', CaptureProcessor);
`;

/** Derive the WebSocket origin (`ws[s]://…`) from the HTTP server URL. */
function toWsUrl(serverUrl: string, path: string): string {
  const base = serverUrl.replace(/^http/, 'ws').replace(/\/$/, '');
  return `${base}${path}`;
}

/** Float32 [-1,1] → signed 16-bit little-endian PCM. */
function floatTo16BitPCM(input: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

interface LiveServerEvent {
  type: 'status' | 'ready' | 'partial' | 'final' | 'error';
  text?: string;
  state?: string;
  message?: string;
  code?: string;
}

export function useLiveTranscription() {
  const [status, setStatus] = useState<LiveStatus>('idle');
  const [interim, setInterim] = useState('');
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  const stoppingRef = useRef(false);
  const closeTimerRef = useRef<number | null>(null);

  // Tear down the mic graph (stops sending audio) but leaves the socket alone.
  const stopCapture = useCallback(() => {
    nodeRef.current?.port.close();
    nodeRef.current?.disconnect();
    nodeRef.current = null;
    streamRef.current?.getTracks().forEach((t) => {
      t.stop();
    });
    streamRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
  }, []);

  // Full teardown: mic graph + socket + timers.
  const teardown = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    stopCapture();
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState <= WebSocket.OPEN) {
      ws.close();
    }
  }, [stopCapture]);

  const beginCapture = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    streamRef.current = stream;

    const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
    ctxRef.current = ctx;
    await ctx.resume();

    const blobUrl = URL.createObjectURL(
      new Blob([CAPTURE_WORKLET], { type: 'application/javascript' }),
    );
    try {
      await ctx.audioWorklet.addModule(blobUrl);
    } finally {
      URL.revokeObjectURL(blobUrl);
    }

    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, 'capture-processor');
    nodeRef.current = node;
    node.port.onmessage = (e: MessageEvent<Float32Array>) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(floatTo16BitPCM(e.data));
      }
    };
    source.connect(node);
    // The worklet emits silence on its output; connecting to the destination
    // keeps the graph pulling without producing audible feedback.
    node.connect(ctx.destination);
  }, []);

  const stop = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || stoppingRef.current) {
      teardown();
      setStatus('idle');
      setInterim('');
      return;
    }
    stoppingRef.current = true;
    // Stop sending audio, ask the daemon to finalize, then close once the final
    // arrives (or after a short fallback so we never hang on a dropped final).
    stopCapture();
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'flush' }));
    }
    closeTimerRef.current = window.setTimeout(() => {
      teardown();
      setStatus('idle');
      setInterim('');
    }, 1200);
  }, [stopCapture, teardown]);

  const start = useCallback(async () => {
    if (status !== 'idle' && status !== 'error') return;
    setError(null);
    setInterim('');
    setTranscript('');
    stoppingRef.current = false;
    setStatus('connecting');

    const serverUrl = useServerStore.getState().serverUrl;
    let ws: WebSocket;
    try {
      ws = new WebSocket(toWsUrl(serverUrl, '/ws/transcribe'));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open connection');
      setStatus('error');
      return;
    }
    wsRef.current = ws;

    ws.onmessage = async (event) => {
      let msg: LiveServerEvent;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      switch (msg.type) {
        case 'status':
          if (msg.state === 'loading') setStatus('loading');
          break;
        case 'ready':
          try {
            await beginCapture();
            setStatus('listening');
          } catch (e) {
            setError(e instanceof Error ? e.message : 'Microphone unavailable');
            setStatus('error');
            teardown();
          }
          break;
        case 'partial':
          setInterim(msg.text ?? '');
          break;
        case 'final':
          if (msg.text) {
            setTranscript((prev) => (prev ? `${prev} ${msg.text}` : (msg.text ?? '')));
          }
          setInterim('');
          // A final arriving after the user pressed stop ends the session.
          if (stoppingRef.current) {
            teardown();
            setStatus('idle');
          }
          break;
        case 'error':
          setError(msg.message ?? 'Transcription error');
          setStatus('error');
          teardown();
          break;
      }
    };

    ws.onerror = () => {
      if (stoppingRef.current) return; // expected during shutdown
      setError('Connection error');
      setStatus('error');
    };

    ws.onclose = () => {
      stopCapture();
      wsRef.current = null;
      // An unexpected close (not user-initiated, not an error we surfaced).
      setStatus((s) => (s === 'error' ? s : 'idle'));
      setInterim('');
    };
  }, [status, beginCapture, stopCapture, teardown]);

  // Clean up on unmount.
  useEffect(() => () => teardown(), [teardown]);

  const isActive = status === 'connecting' || status === 'loading' || status === 'listening';

  return { status, interim, transcript, error, isActive, start, stop };
}
