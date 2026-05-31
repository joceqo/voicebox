# Live (streaming) STT with Voxtral Mini Realtime — implementation plan

Status: **planned**. Builds on the batch MVP committed in
`feat(stt): local Voxtral Mini Realtime backend (batch MVP)`.

Goal: words appear **while you speak** (incremental partial transcripts),
instead of "record → wait ~11 s → full text". The Voxtral Mini 4B Realtime
model is natively streaming (causal audio encoder, `--delay` lookahead knob),
and the repo's **browser/WASM demo already streams live** — so the model and
decode loop exist. What's missing is exposing that loop as a persistent server
and wiring a real-time transport up to the UI.

---

## Why the batch MVP can't stream

The integration today calls the **one-shot** `voxtral transcribe` CLI per
request: it loads the ~2.4 GB model, decodes the whole clip, prints the
transcript, exits. Three things block "live words":

1. **No persistent streaming process** — the CLI exits after each clip; the
   model is reloaded every call (~11 s warm). There is no mode that ingests
   audio continuously.
2. **No real-time transport** — voicebox STT is HTTP request/response
   (`POST /transcribe`). No channel to push partials as they're produced.
3. **UI waits for the final response** — `SttTab` / capture flow renders only
   the final text.

---

## Target architecture

Keep the Python backend as the single front door (auth, model mgmt). The Rust
daemon is an **internal long-lived subprocess** the backend owns. The UI talks
to the backend over WebSocket; the backend bridges to the daemon over a simple
framed stdin/stdout protocol.

```
 Browser/Tauri UI                 Python backend (FastAPI)             Rust daemon (voxtral stream)
 ────────────────                 ────────────────────────             ────────────────────────────
 mic → 16 kHz mono PCM   ─WS audio→  /ws/transcribe                ─stdin frames→  model loaded ONCE
 (Web Audio API)                     VoxtralStreamSession            (causal streaming decode loop)
 incremental text view  ←WS partial─ bridges both directions       ←stdout JSON──  partial/final tokens
```

Rationale for stdin/stdout (not a WS server inside Rust): keeps the HTTP/WS
surface in one place (Python), avoids a second auth/CORS story, and matches the
existing subprocess pattern in `voxtral_stt_backend.py`. The daemon stays a
"dumb" audio-in / text-out pipe.

---

## Phase 1 — Rust: `voxtral stream` subcommand (persistent daemon)

Repo: `~/Desktop/coding/voxtral-mini-realtime-rs` (separate from voicebox).

Add a `stream` subcommand alongside `transcribe`/`speak`.

- **Files to touch:**
  - `src/bin/voxtral/main.rs` — add the `Stream` clap subcommand (`--gguf`,
    `--tokenizer`, `--delay`, optional `--sample-rate 16000`).
  - Reuse the streaming decode loop from `src/web/` (the WASM path —
    `VoxtralQ4` + async decode loop — is the streaming reference
    implementation) and `src/audio/` (mel, chunking, padding). Factor the
    streaming logic out of the WASM-only module into a shared module if it's
    currently gated behind `#[cfg(feature = "wasm")]`.
  - `src/audio/pad.rs` — note the Q4 left-padding workaround (76 tokens) from
    the README; the streaming prefix must keep that behavior.

- **Behavior:** load model once on startup; then loop:
  1. Read length-prefixed audio frames from stdin (see protocol below).
  2. Append samples to a rolling buffer; run the causal encoder + decoder with
     the configured `--delay` lookahead.
  3. Emit a `partial` message whenever new tokens are decoded; emit `final`
     when a frame marked `eos` arrives (flush remaining lookahead first — this
     fixes the "last word cut off" behavior seen in batch on abrupt endings).

- **Startup readiness:** print a single `{"type":"ready"}` line to stdout once
  the model + GPU pipeline are initialized, so the backend knows when to accept
  audio.

### Wire protocol (backend ↔ daemon)

stdin (backend → daemon), little-endian framing:
```
[u8  tag]                  # 0x01 = audio chunk, 0x02 = end-of-utterance, 0x03 = reset/flush
[u32 len]                  # byte length of payload (0 for tag 0x02/0x03)
[len bytes payload]        # tag 0x01: 16 kHz mono f32 (or s16le) PCM samples
```

stdout (daemon → backend), line-delimited JSON (one object per line):
```json
{"type":"ready"}
{"type":"partial","text":"the quick brown","t_ms":820}
{"type":"final","text":"the quick brown fox jumps over the lazy dog","t_ms":2510}
{"type":"error","message":"..."}
```

Keep it boring and line-delimited so the Python side can parse with `readline`.

**Verify Phase 1 standalone:** pipe a WAV's PCM into the daemon in 100 ms
chunks via a tiny script and confirm partials stream out before EOF. Target:
first partial < ~1 s after speech starts; warm RTF well under 1.0.

---

## Phase 2 — Python backend: WebSocket endpoint + daemon manager

- **New file `backend/backends/voxtral_stream_daemon.py`** — a
  `VoxtralStreamDaemon` singleton that:
  - Spawns `voxtral stream …` once (reuse path resolution from
    `voxtral_stt_backend.py`: `VOXTRAL_STT_BIN/_GGUF/_TOKENIZER/_DELAY`).
  - Manages the subprocess with `asyncio.create_subprocess_exec` (async stdin
    write / stdout readline). Waits for `{"type":"ready"}`.
  - Exposes `async def stream_session()` returning an object with
    `feed(pcm_bytes)`, `end()`, and an `async for partial in ...` iterator.
  - Serializes access: the single daemon handles one utterance at a time
    (send `0x03 reset` between sessions). For concurrency later, run a small
    pool of daemons — out of scope for v1.
  - Lifecycle hooks in `backend/app.py` (lifespan): lazy-spawn on first WS
    connect; terminate on shutdown. Do **not** preload at startup (heavy).

- **New route `backend/routes/transcription_ws.py`** —
  `@router.websocket("/ws/transcribe")`:
  - Accept the socket. Read an opening JSON config frame (`language`,
    `sample_rate`); language is informational (model auto-detects).
  - Loop: receive binary audio frames from the client → `session.feed(...)`.
    On a client `{"type":"end"}` text frame → `session.end()`.
  - Concurrently forward daemon partials/finals to the client as JSON text
    frames. Use `asyncio.gather` of a receive-loop and a send-loop.
  - On disconnect/cancel: reset the daemon session, never kill the daemon.
  - Register the router in `backend/app.py` next to the other routers.

- **Precedent to mirror:** streaming TTS in `routes/openai_compat.py`
  (`StreamingResponse`, `WAV_HEADER_24K_MONO_INT16`, float32→int16 chunks) and
  provider `speech_stream` async generators — the STT WS is the bidirectional
  analogue. Audio format conventions: 16 kHz mono; if the client sends 24 kHz
  or s16, resample/convert server-side (reuse `utils/audio.py`).

**Verify Phase 2:** a `websocat`/Python WS client that streams a WAV's PCM in
chunks and prints partials. Confirm partials arrive mid-stream and a final on
end. No model reload between two sequential sessions (daemon stays warm).

---

## Phase 3 — UI: live capture + incremental display

App: `~/Desktop/coding/voicebox/app` (React + Vite, Tauri shell).

- **New hook `app/src/lib/hooks/useLiveTranscription.ts`:**
  - Open `ws://<backend>/ws/transcribe` (base URL from `lib/api/client.ts`
    `getBaseUrl()` — dev backend is `:17493`).
  - Capture mic via `navigator.mediaDevices.getUserMedia({audio:true})` +
    `AudioContext`/`AudioWorklet` (or `ScriptProcessor` fallback). Downsample to
    **16 kHz mono**, convert to the daemon's PCM format, post frames over WS
    (~50–100 ms chunks).
  - Expose `{ partial, final, isRecording, start(), stop(), error }`. On
    `partial` update a live string; on `final` commit it.

- **`app/src/components/SttTab/SttTab.tsx`:**
  - When the selected model is `voxtral-realtime`, show a **"Live" record
    button** that uses `useLiveTranscription` and renders the partial text
    updating in place (distinct visual state for "interim" vs "final", e.g.
    interim greyed/italic). Keep the existing file/clip flow for batch.
  - Optionally surface the `--delay` tradeoff as a latency slider (maps to
    `VOXTRAL_STT_DELAY` / a per-session config field).

- **Dictation/Captures:** once the playground works, wire the same hook into
  the dictation window (`components/DictateWindow/DictateWindow.tsx`) so live
  dictation pastes incrementally.

**Verify Phase 3:** open the STT tab, pick Voxtral, hit Live, speak — words
appear within ~1 s and refine as you continue. Test EN + FR.

---

## Cross-cutting

- **Config / env:** reuse `VOXTRAL_STT_BIN/_GGUF/_TOKENIZER/_DELAY`. Add
  `VOXTRAL_STREAM_ENABLED` (default on if binary present) so the UI can hide
  the Live button when the daemon isn't available.
- **Graceful degradation:** if the daemon fails to spawn or `stream` subcommand
  is missing (old binary), the WS sends `{"type":"error"}` and the UI falls
  back to the batch path. Never crash the backend.
- **Build:** the Rust change requires rebuilding the binary
  (`cargo build --release --features "wgpu,cli,hub"`). Document the minimum
  binary version; have the daemon report it in the `ready` message and the
  backend log a warning on mismatch.
- **Models page:** unrelated pre-existing quirk — Voxtral shows "not
  downloaded" in Settings → Models because weights are `.gguf` in the repo, not
  the HF cache. Don't let the live work depend on that page.

---

## Suggested order & rough sizing

1. **Phase 1 (Rust daemon)** — largest unknown; spike first. Read `src/web/`
   to extract the streaming loop, get `voxtral stream` emitting partials from a
   piped WAV. *(Biggest risk: how cleanly the WASM-gated streaming loop factors
   into a native path.)*
2. **Phase 2 (backend WS + daemon mgr)** — mechanical once the protocol is set.
3. **Phase 3 (UI)** — mic capture + incremental rendering; the AudioWorklet
   downsampling is the fiddly part.

## File checklist

Rust (`voxtral-mini-realtime-rs`):
- [ ] `src/bin/voxtral/main.rs` — `Stream` subcommand
- [ ] shared streaming module factored from `src/web/` (de-`wasm`-gate)
- [ ] stdin framing + stdout line-JSON; `ready` handshake; eos flush

voicebox backend:
- [ ] `backend/backends/voxtral_stream_daemon.py` — daemon manager singleton
- [ ] `backend/routes/transcription_ws.py` — `/ws/transcribe`
- [ ] `backend/app.py` — register WS router + lifespan shutdown of daemon

voicebox app:
- [ ] `app/src/lib/hooks/useLiveTranscription.ts` — WS + mic capture
- [ ] `app/src/components/SttTab/SttTab.tsx` — Live mode for `voxtral-realtime`
- [ ] (later) `app/src/components/DictateWindow/DictateWindow.tsx`

## Open questions to resolve during the spike
- Does the streaming decode loop in `src/web/` depend on WASM-only types
  (`wasm-bindgen`, JS futures)? If so, how much refactor to run it native?
- f32 vs s16le on the wire — pick whichever the decode loop ingests natively to
  avoid an extra conversion in the hot path.
- Single daemon vs pool: one utterance at a time is fine for a single local
  user; revisit only if multi-session is needed.
