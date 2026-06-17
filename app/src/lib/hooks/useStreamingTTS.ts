import { useCallback, useRef, useState } from 'react';
import { useServerStore } from '@/stores/serverStore';

/**
 * Low-latency streaming TTS playback via the gateway's
 * `POST /v1/audio/speech` (stream:true). The browser can't play a streamed WAV
 * through an <audio> element (MSE has no audio/wav), so we decode the streamed
 * 16-bit PCM ourselves and schedule chunks into a Web Audio AudioContext as
 * they arrive — playback starts within ~milliseconds instead of waiting for the
 * whole clip. Preview-only: nothing is persisted to history.
 */

const SAMPLE_RATE = 24000; // VoiceStudio gateway streams 24kHz mono 16-bit
const WAV_HEADER_BYTES = 44;
const LEAD_IN_S = 0.12; // small buffer so the first chunks don't underrun

interface StreamForProfileOpts {
  engine: string;
  voiceId: string;
  input: string;
  language?: string;
  /** Called once the full clip has streamed in, with a WAV object URL — used
   *  to load the result into the player bar for replay/scrubbing. */
  onComplete?: (wavUrl: string) => void;
}

function writeAscii(view: DataView, offset: number, text: string) {
  for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
}

/** Assemble streamed 16-bit PCM chunks into a complete WAV blob URL. */
function pcmChunksToWavUrl(chunks: Uint8Array[], sampleRate: number): string {
  let dataLen = 0;
  for (const c of chunks) dataLen += c.length;
  const pcm = new Uint8Array(dataLen);
  let off = 0;
  for (const c of chunks) {
    pcm.set(c, off);
    off += c.length;
  }
  const header = new ArrayBuffer(44);
  const dv = new DataView(header);
  const channels = 1;
  const bits = 16;
  const blockAlign = (channels * bits) / 8;
  writeAscii(dv, 0, 'RIFF');
  dv.setUint32(4, 36 + dataLen, true);
  writeAscii(dv, 8, 'WAVE');
  writeAscii(dv, 12, 'fmt ');
  dv.setUint32(16, 16, true); // fmt chunk size
  dv.setUint16(20, 1, true); // PCM
  dv.setUint16(22, channels, true);
  dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, sampleRate * blockAlign, true);
  dv.setUint16(32, blockAlign, true);
  dv.setUint16(34, bits, true);
  writeAscii(dv, 36, 'data');
  dv.setUint32(40, dataLen, true);
  return URL.createObjectURL(new Blob([header, pcm], { type: 'audio/wav' }));
}

// Module-level cache of the voice catalog so we resolve a voice's `model`
// (local "voicebox-*" vs provider "<provider>/<model>") without refetching.
let voicesCachePromise: Promise<{
  voices: { id: string; engine: string; model: string }[];
}> | null = null;

async function resolveModelVoice(
  baseUrl: string,
  engine: string,
  voiceId: string,
): Promise<{ model: string; voice: string } | null> {
  if (!voicesCachePromise) {
    voicesCachePromise = fetch(`${baseUrl}/v1/audio/voices`).then((r) => r.json());
  }
  try {
    const { voices } = await voicesCachePromise;
    const match =
      voices.find((v) => v.id === voiceId && v.engine === engine) ??
      voices.find((v) => v.id === voiceId);
    return match ? { model: match.model, voice: match.id } : null;
  } catch {
    voicesCachePromise = null; // allow retry next time
    return null;
  }
}

export function useStreamingTTS() {
  const [isStreaming, setIsStreaming] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const ctxRef = useRef<AudioContext | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
    setIsStreaming(false);
    setIsPaused(false);
  }, []);

  // Pause/resume the live stream playback (suspends the audio clock; queued
  // chunks resume where they left off). Seeking/rewind during the stream isn't
  // supported — the player bar takes over for that once the clip completes.
  const pause = useCallback(() => {
    ctxRef.current?.suspend().then(() => setIsPaused(true)).catch(() => {});
  }, []);
  const resume = useCallback(() => {
    ctxRef.current?.resume().then(() => setIsPaused(false)).catch(() => {});
  }, []);

  const streamForProfile = useCallback(
    async (opts: StreamForProfileOpts) => {
      stop(); // cancel any in-flight stream

      const baseUrl = useServerStore.getState().serverUrl;
      const resolved = await resolveModelVoice(baseUrl, opts.engine, opts.voiceId);
      if (!resolved) throw new Error(`No streaming voice for ${opts.engine}/${opts.voiceId}`);

      const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      ctxRef.current = ctx;
      await ctx.resume();
      const abort = new AbortController();
      abortRef.current = abort;
      setIsStreaming(true);
      setIsPaused(false);

      let nextTime = ctx.currentTime + LEAD_IN_S;
      let headerSkipped = 0;
      let leftover: Uint8Array = new Uint8Array(0);
      let activeSources = 0;
      let streamDone = false;
      const pcmChunks: Uint8Array[] = []; // accumulate for the replayable WAV

      const settleIfDone = () => {
        if (streamDone && activeSources === 0 && ctxRef.current === ctx) {
          setIsStreaming(false);
          setIsPaused(false);
        }
      };

      try {
        const resp = await fetch(`${baseUrl}/v1/audio/speech`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: resolved.model,
            voice: resolved.voice,
            input: opts.input,
            language: opts.language,
            response_format: 'wav',
            stream: true,
          }),
          signal: abort.signal,
        });
        if (!resp.ok || !resp.body) throw new Error(`Stream failed (HTTP ${resp.status})`);

        const reader = resp.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          if (!value || value.length === 0) continue;

          // Prepend any leftover (odd) byte from the previous chunk.
          let bytes: Uint8Array = value;
          if (leftover.length) {
            const merged = new Uint8Array(leftover.length + value.length);
            merged.set(leftover);
            merged.set(value, leftover.length);
            bytes = merged;
            leftover = new Uint8Array(0);
          }

          // Skip the 44-byte WAV header once (it may span chunks).
          if (headerSkipped < WAV_HEADER_BYTES) {
            const need = WAV_HEADER_BYTES - headerSkipped;
            if (bytes.length <= need) {
              headerSkipped += bytes.length;
              continue;
            }
            bytes = bytes.subarray(need);
            headerSkipped = WAV_HEADER_BYTES;
          }

          // Int16 needs an even byte count; stash any trailing odd byte.
          const usable = bytes.length - (bytes.length % 2);
          if (usable < bytes.length) leftover = bytes.subarray(usable);
          if (usable === 0) continue;

          // Copy to a fresh buffer so the Int16Array is 2-byte aligned.
          const aligned = bytes.slice(0, usable);
          pcmChunks.push(aligned); // keep raw PCM for the replayable WAV
          const pcm = new Int16Array(aligned.buffer);
          const f32 = new Float32Array(pcm.length);
          for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;

          const buffer = ctx.createBuffer(1, f32.length, SAMPLE_RATE);
          buffer.copyToChannel(f32, 0);
          const src = ctx.createBufferSource();
          src.buffer = buffer;
          src.connect(ctx.destination);
          const startAt = Math.max(nextTime, ctx.currentTime);
          src.start(startAt);
          nextTime = startAt + buffer.duration;
          activeSources++;
          src.onended = () => {
            activeSources--;
            settleIfDone();
          };
        }
        streamDone = true;
        // Hand the fully-streamed clip to the player bar (replay/scrub).
        if (opts.onComplete && pcmChunks.length) {
          opts.onComplete(pcmChunksToWavUrl(pcmChunks, SAMPLE_RATE));
        }
        settleIfDone();
      } catch (e) {
        if ((e as { name?: string })?.name === 'AbortError') return;
        setIsStreaming(false);
        throw e;
      }
    },
    [stop],
  );

  return { isStreaming, isPaused, streamForProfile, pause, resume, stop };
}
