"""
Voxtral live-STT streaming daemon manager.

The heavy STT model is loaded **once** inside a long-lived subprocess (the
"daemon") that does audio-in -> text-out. The FastAPI backend stays the entry
point: the UI streams microphone audio over a WebSocket (``/ws/transcribe``),
and this manager bridges that WebSocket to the daemon over a small, framed
``stdin``/``stdout`` protocol.

    UI (mic -> PCM 16k) --WS audio-->  /ws/transcribe  --stdin frames-->  daemon
    UI (live text)      <--WS partial--    bridge       <--stdout JSON--   (model 1x)

In production the daemon is the Rust ``voxtral stream`` binary. Until that
binary is wired up, the command defaults to a pure-Python mock
(:mod:`backend.services.voxtral_stream_mock`) that speaks the *exact* same
protocol, so the whole WebSocket + bridge + UI path is real and testable today
and the Rust binary becomes a drop-in swap (point ``VOICEBOX_VOXTRAL_STREAM_CMD``
at it).

Wire protocol (v1)
------------------
**Host -> daemon (the daemon's stdin):** a stream of length-prefixed frames.
Each frame is a little-endian ``uint32`` length ``N`` followed by ``N`` bytes:

* ``N > 0``            -- a chunk of PCM audio: 16 kHz, mono, signed 16-bit
                         little-endian (``s16le``).
* ``N == 0``           -- ``FLUSH``: finalize the current utterance now (the
                         daemon emits a ``final``) and begin a fresh utterance.
* ``N == 0xFFFFFFFF``  -- ``RESET``: drop all buffered audio and reset the
                         utterance counter (used between sessions). No frame
                         body follows.
* stdin EOF            -- flush any pending utterance, then exit.

``s16le`` (not f32) is used on the wire: half the bytes, trivially produced from
the browser's ``Float32`` capture, and it matches the Int16 convention already
used by the streaming-TTS player. The Rust binary must read the same format.

**Daemon -> host (the daemon's stdout):** newline-delimited JSON, one object
per line, each with a ``type``:

* ``{"type": "ready", "protocol": 1, "engine": ..., "sample_rate": 16000}``
  -- emitted once, after the model has loaded. The host waits for this before
  forwarding audio.
* ``{"type": "partial", "utterance": <int>, "text": <str>}`` -- interim
  hypothesis for the in-progress utterance.
* ``{"type": "final", "utterance": <int>, "text": <str>}`` -- finalized text
  for an utterance (after a ``FLUSH`` / endpoint).
* ``{"type": "error", "message": <str>}`` -- recoverable error.

stderr is human-readable logging and is inherited by the server process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import struct
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

logger = logging.getLogger(__name__)

# --- Protocol constants (shared with the mock daemon and the Rust binary) -----

PROTOCOL_VERSION = 1
SAMPLE_RATE = 16000  # Hz, mono, s16le on the wire

_LEN = struct.Struct("<I")  # 4-byte little-endian frame length prefix
FRAME_FLUSH = 0
FRAME_RESET = 0xFFFFFFFF
# Reject absurd frame sizes (protects the daemon from a desync / bad sender).
MAX_FRAME_BYTES = 16 * 1024 * 1024

# Name of the env var holding the real Rust binary command (shlex-split) --
# typically the voxtral binary invoked with its streaming subcommand. When it is
# unset we fall back to the bundled Python mock daemon.
_CMD_ENV = "VOICEBOX_VOXTRAL_STREAM_CMD"


def encode_frame(payload: bytes) -> bytes:
    """Length-prefix an audio payload for the daemon's stdin."""
    return _LEN.pack(len(payload)) + payload


def encode_flush() -> bytes:
    """A zero-length control frame: finalize the current utterance."""
    return _LEN.pack(FRAME_FLUSH)


def encode_reset() -> bytes:
    """A control frame that resets the daemon's session state."""
    return _LEN.pack(FRAME_RESET)


def default_command() -> list[str]:
    """Resolve the daemon command.

    Prefers ``$VOICEBOX_VOXTRAL_STREAM_CMD`` (the real Rust binary); otherwise
    runs the bundled Python mock so the streaming path works out of the box.
    """
    override = os.environ.get(_CMD_ENV)
    if override:
        return shlex.split(override)
    return [sys.executable, "-m", "backend.services.voxtral_stream_mock"]


class SessionBusyError(RuntimeError):
    """Raised when a transcription session is already active.

    The daemon hosts a single continuous audio stream (one resident model), so
    only one live session runs at a time -- the natural model for a local,
    single-user desktop app.
    """


class DaemonNotRunningError(RuntimeError):
    """Raised when audio is fed but the daemon process is not alive."""


class VoxtralStreamDaemon:
    """Manages the long-lived streaming-STT subprocess and bridges I/O.

    One process, model loaded once, reused across sessions. A ``session()``
    async context manager grants exclusive use; events for the active session
    are delivered through :attr:`events`.
    """

    def __init__(self, command: list[str] | None = None) -> None:
        self._command = command or default_command()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._ready_info: dict[str, Any] = {}
        # Events for the *current* session (partial / final / error / closed).
        # Unbounded so the stdout reader never blocks on a slow consumer.
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    # -- lifecycle -------------------------------------------------------------

    @property
    def ready_info(self) -> dict[str, Any]:
        """The payload of the daemon's ``ready`` event (engine, sample rate…)."""
        return dict(self._ready_info)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def session_busy(self) -> bool:
        return self._session_lock.locked()

    async def start(self, timeout: float = 120.0) -> None:
        """Spawn the daemon (if needed) and wait for its ``ready`` event.

        Idempotent: a no-op if the process is already running. ``timeout``
        covers first-run model download + load, hence the generous default.
        """
        async with self._start_lock:
            if self.is_running():
                return

            logger.info("voxtral-stream: starting daemon: %s", " ".join(self._command))
            self._ready = asyncio.Event()
            self._ready_info = {}
            self._events = asyncio.Queue()
            self._proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # Inherit stderr so the daemon's logs land in the server console.
                stderr=None,
            )
            self._reader_task = asyncio.create_task(self._read_stdout(), name="voxtral-stream-reader")

            try:
                await asyncio.wait_for(self._ready.wait(), timeout)
            except TimeoutError as exc:
                await self.stop()
                raise RuntimeError(f"Voxtral stream daemon did not signal ready within {timeout:.0f}s") from exc

            if not self.is_running():
                await self.stop()
                raise RuntimeError("Voxtral stream daemon exited before becoming ready")

            logger.info("voxtral-stream: daemon ready (%s)", self._ready_info or "?")

    async def stop(self) -> None:
        """Terminate the daemon and tear down the reader task."""
        reader, self._reader_task = self._reader_task, None
        if reader:
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader

        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                logger.warning("voxtral-stream: daemon did not exit, killing")
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            except ProcessLookupError:
                pass

    # -- stdout reader ---------------------------------------------------------

    async def _read_stdout(self) -> None:
        """Parse newline-delimited JSON events from the daemon's stdout."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        stdout = proc.stdout
        while True:
            line = await stdout.readline()
            if not line:
                # Process closed its stdout -> it has exited (or is about to).
                self._events.put_nowait({"type": "closed"})
                self._ready.set()  # unblock start() if it died before ready
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("voxtral-stream: non-JSON line: %r", line[:200])
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "ready":
                # Consumed here (sets the gate); the WS route emits its own
                # ready/status to the client so re-acquired sessions still get
                # a clean handshake without the daemon re-announcing.
                self._ready_info = event
                self._ready.set()
                continue
            self._events.put_nowait(event)

    # -- I/O -------------------------------------------------------------------

    def _require_running(self) -> asyncio.StreamWriter:
        if not self.is_running() or self._proc is None or self._proc.stdin is None:
            raise DaemonNotRunningError("Voxtral stream daemon is not running")
        return self._proc.stdin

    async def feed(self, pcm: bytes) -> None:
        """Forward a chunk of s16le/16k PCM audio to the daemon."""
        if not pcm:
            return
        stdin = self._require_running()
        stdin.write(encode_frame(pcm))
        await stdin.drain()

    async def flush(self) -> None:
        """Ask the daemon to finalize the current utterance."""
        stdin = self._require_running()
        stdin.write(encode_flush())
        await stdin.drain()

    async def reset(self) -> None:
        """Reset the daemon's session state (between sessions)."""
        stdin = self._require_running()
        stdin.write(encode_reset())
        await stdin.drain()

    @property
    def events(self) -> asyncio.Queue[dict[str, Any]]:
        """Queue of events for the active session (``partial``/``final``/…)."""
        return self._events

    def _drain_events(self) -> None:
        """Discard any events left over from a previous session."""
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                return

    # -- sessions --------------------------------------------------------------

    @asynccontextmanager
    async def session(self) -> AsyncIterator[VoxtralStreamDaemon]:
        """Acquire exclusive use of the daemon for one live session.

        Ensures the daemon is started and ready, resets its state, and clears
        stale events on entry; resets again on exit so the next session starts
        clean. Raises :class:`SessionBusyError` immediately if a session is active.
        """
        if self._session_lock.locked():
            raise SessionBusyError("A transcription session is already active")
        await self._session_lock.acquire()
        try:
            await self.start()
            await self.reset()
            self._drain_events()
            yield self
        finally:
            try:
                if self.is_running():
                    await self.reset()
            except Exception:
                logger.debug("voxtral-stream: reset on session exit failed", exc_info=True)
            self._session_lock.release()


# --- module-level singleton ---------------------------------------------------

_daemon: VoxtralStreamDaemon | None = None


def get_voxtral_stream_daemon() -> VoxtralStreamDaemon:
    """Return the process-wide streaming-STT daemon manager (lazy singleton)."""
    global _daemon
    if _daemon is None:
        _daemon = VoxtralStreamDaemon()
    return _daemon


async def shutdown_voxtral_stream_daemon() -> None:
    """Stop the daemon if one was started. Safe to call when none exists."""
    global _daemon
    if _daemon is not None:
        await _daemon.stop()
        _daemon = None
