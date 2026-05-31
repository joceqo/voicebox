# Voxtral live STT — plan & status

Real-time (streaming) speech-to-text: speak into the mic and watch the
transcript build up live, instead of recording a clip and transcribing it in one
shot.

> **Status (this branch).** Phases 2 (backend bridge) and 3 (UI) are
> **implemented and tested** against a **mock daemon** that speaks the exact wire
> protocol below. Phase 1 (the Rust `voxtral stream` engine) is **not in this
> repo** — when it lands, point `VOICEBOX_VOXTRAL_STREAM_CMD` at it and it drops
> straight in. See [Swapping in the Rust binary](#swapping-in-the-rust-binary).

## Architecture

The Python backend stays the entry point. The heavy STT model lives in a
long-lived **daemon** subprocess that loads the model **once** and does
audio-in → text-out. The UI talks to the backend over a WebSocket; the backend
bridges to the daemon over a small framed `stdin`/`stdout` protocol.

```
UI (mic → PCM 16k) ──WS audio──▶  /ws/transcribe  ──stdin frames──▶  daemon
UI (live text)     ◀─WS partial──    bridge        ◀──stdout JSON──   (model 1×)
```

- **Daemon** = the Rust `voxtral stream` binary in production; a bundled Python
  **mock** (`backend/services/voxtral_stream_mock.py`) until then. Either is a
  drop-in: same protocol, selected by `VOICEBOX_VOXTRAL_STREAM_CMD`.
- **One session at a time.** A single resident model = one continuous audio
  stream. A second concurrent WebSocket is rejected (`code: "busy"`). This is
  the natural model for a local, single-user desktop app.

## Wire protocol (v1)

The contract between the bridge and the daemon. **The Rust binary must
implement this exactly** to be a drop-in replacement for the mock.

### Host → daemon (the daemon's stdin)

A stream of length-prefixed frames. Each frame is a little-endian `uint32`
length `N`, followed by `N` payload bytes:

| `N`            | Meaning                                                              |
| -------------- | -------------------------------------------------------------------- |
| `> 0`          | PCM audio chunk: **16 kHz, mono, signed 16-bit little-endian (`s16le`)** |
| `0`            | `FLUSH` — finalize the current utterance now, then start a fresh one |
| `0xFFFFFFFF`   | `RESET` — drop buffered audio + reset the utterance counter (between sessions); no body follows |
| stdin EOF      | flush any pending utterance, then exit                               |

**Why `s16le` and not f32** (a resolved open question): half the bytes on the
wire, trivially produced from the browser's `Float32` capture, and it matches
the Int16 PCM convention already used by the streaming-TTS player
(`useStreamingTTS.ts`).

### Daemon → host (the daemon's stdout)

Newline-delimited JSON, one object per line, each with a `type`:

```jsonc
{"type": "ready",   "protocol": 1, "engine": "...", "sample_rate": 16000}  // once, after model load
{"type": "partial", "utterance": 0, "text": "the quick brown"}             // interim hypothesis
{"type": "final",   "utterance": 0, "text": "The quick brown fox."}        // after FLUSH / endpoint
{"type": "error",   "message": "..."}                                       // recoverable error
```

`stderr` is human-readable logging and is inherited by the server process.

## Phases

### Phase 1 — Rust `voxtral stream` daemon *(not in this repo; spec above)*

Add a `voxtral stream` subcommand to the Rust STT engine that loads the model
once, reads the framed `s16le` PCM stream on stdin, and emits the JSON events on
stdout. Reuse the streaming decode loop from the existing browser demo
(`src/web/`).

Open questions to resolve during the spike (kept for whoever does the Rust
work):

- Is the `src/web/` streaming loop gated behind `#[cfg(target_arch = "wasm32")]`?
  If so, factor the decode loop out of the wasm-only path so the native
  subcommand can share it.
- Endpointing: does the engine emit its own utterance boundaries, or does it
  rely solely on the host's `FLUSH`? The protocol supports host-driven `FLUSH`;
  engine-driven endpoints can additionally emit `final` on their own.

Until this exists, the **mock daemon** below stands in.

### Phase 2 — Backend bridge ✅ *(implemented)*

- `backend/services/voxtral_stream_daemon.py` — singleton manager
  (`get_voxtral_stream_daemon()`): spawns/owns the subprocess, frames audio,
  parses stdout events, exposes an async event queue, and guards a single
  session (`SessionBusy`). Resolves the command from
  `VOICEBOX_VOXTRAL_STREAM_CMD`, else the mock.
- `backend/services/voxtral_stream_mock.py` — the mock daemon (stdlib only):
  speaks the exact protocol, revealing placeholder words proportional to audio
  duration so the partial→final UX is realistic.
- `backend/routes/live_transcription.py` — `WS /ws/transcribe`: accepts the
  session, forwards binary PCM in / control JSON, streams `ready`/`partial`/
  `final`/`error` out. Kept free of torch/STT imports.
- Registered in `backend/routes/__init__.py`; daemon shutdown hooked into
  `backend/app.py`'s lifespan teardown.

### Phase 3 — UI live mode ✅ *(implemented)*

- `app/src/lib/hooks/useLiveTranscription.ts` — opens the WebSocket, captures
  the mic via an AudioWorklet at 16 kHz (Blob-URL module; Tauri sets no CSP),
  converts each block to `s16le`, streams it, and exposes
  `{ status, interim, transcript, error, isActive, start, stop }`.
- `app/src/components/SttTab/SttTab.tsx` — a File / **Live** tab toggle. Live
  mode shows a Start/Stop control and an incrementally rendered transcript
  (committed text in full colour, the interim hypothesis greyed).
- New `stt.*` strings in `app/src/i18n/locales/en/translation.json`.

## Swapping in the Rust binary

No code changes — set the command (and restart the backend):

```bash
export VOICEBOX_VOXTRAL_STREAM_CMD="/path/to/voxtral stream --model voxtral-mini"
```

The value is `shlex`-split. The binary must honour the [wire protocol](#wire-protocol-v1).

## Testing

```bash
# Daemon manager + mock, over the real subprocess (stdlib only):
pytest backend/tests/test_voxtral_stream_daemon.py

# The /ws/transcribe WebSocket end-to-end (in-process TestClient):
pytest backend/tests/test_live_transcription_ws.py
```

Manual: `just dev`, open the STT tab, switch to **Live**, press *Start
listening*, and speak — the mock emits placeholder words; the real binary emits
your transcript.
