"""Voxtral Mini Realtime STT backend (local Rust binary).

Wraps the pure-Rust Voxtral Mini 4B Realtime implementation
(``TrevorS/voxtral-mini-realtime-rs``), which runs the Q4 GGUF model on
Apple Silicon via the Burn/wgpu Metal backend. Unlike the Whisper and Parakeet
backends — which load a model in-process and keep it resident — the Rust
project ships a one-shot ``voxtral transcribe`` CLI with no daemon mode, so
each request spawns the binary, which reloads the ~2.4 GB model and re-inits
the GPU (~11 s warm, dominated by model load). This is an MVP integration to
validate the pipeline; a persistent-server mode in the Rust binary is the
planned follow-up for low-latency / streaming use.

Paths are resolved from a repo root (default
``~/Desktop/coding/voxtral-mini-realtime-rs``), each overridable by env var:

- ``VOXTRAL_STT_BIN``        — path to the ``voxtral`` binary
- ``VOXTRAL_STT_GGUF``       — path to the Q4 GGUF weights
- ``VOXTRAL_STT_TOKENIZER``  — path to the Tekken tokenizer JSON
- ``VOXTRAL_STT_DELAY``      — streaming lookahead in tokens (1 tok = 80 ms)

The binary expects a 16 kHz mono WAV, so the input is transcoded with ffmpeg
before invocation (callers pass arbitrary sample rates / formats).
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default checkout location (see memory: voxtral-realtime-local). Each path is
# individually overridable via env var so the repo can live anywhere.
_DEFAULT_REPO = Path.home() / "Desktop/coding/voxtral-mini-realtime-rs"

# 13 languages supported by Voxtral Mini Realtime.
VOXTRAL_STT_LANGUAGES = [
    "en", "fr", "es", "de", "it", "pt", "nl", "hi", "ar", "ru", "zh", "ja", "ko",
]

# HF repo backing the GGUF weights (for display / config only).
VOXTRAL_STT_HF_REPO = "TrevorJS/voxtral-mini-realtime-gguf"

_TRANSCRIBE_TIMEOUT = 180  # seconds; generous for cold start + long clips


def _resolve_path(env: str, default: Path) -> Path:
    override = os.environ.get(env)
    return Path(override).expanduser() if override else default


class VoxtralSTTBackend:
    """Local Voxtral Mini Realtime STT via the one-shot Rust CLI.

    Implements the ``STTBackend`` protocol. There is no resident model, so
    ``load_model`` only validates that the binary and weights exist;
    ``transcribe`` does the work by spawning the CLI in a worker thread.
    """

    def __init__(self):
        self.model_size = "realtime"
        self._binary = _resolve_path("VOXTRAL_STT_BIN", _DEFAULT_REPO / "target/release/voxtral")
        self._gguf = _resolve_path("VOXTRAL_STT_GGUF", _DEFAULT_REPO / "models/voxtral-q4.gguf")
        self._tokenizer = _resolve_path("VOXTRAL_STT_TOKENIZER", _DEFAULT_REPO / "models/tekken.json")
        self._validated = False

    # ── STTBackend protocol ──────────────────────────────────────────

    def is_loaded(self) -> bool:
        # No resident model; "loaded" means the binary + weights are present.
        return self._validated or self._files_present()

    def _files_present(self) -> bool:
        return self._binary.is_file() and self._gguf.is_file() and self._tokenizer.is_file()

    def _is_model_cached(self, model_size: str = "realtime") -> bool:
        """Whether the GGUF weights are on disk (mirrors the HF-cache probe
        other backends expose so callers can skip a doomed first call)."""
        return self._gguf.is_file()

    async def load_model(self, model_size: Optional[str] = None) -> None:
        """Validate the local install. Raises with an actionable message if
        the binary or weights are missing (there is nothing to download —
        the user builds the Rust project and fetches the GGUF manually)."""
        missing = [
            str(p)
            for p in (self._binary, self._gguf, self._tokenizer)
            if not p.is_file()
        ]
        if missing:
            raise RuntimeError(
                "Voxtral STT is not installed. Missing: "
                + ", ".join(missing)
                + ". Build the binary (cargo build --release --features 'wgpu,cli,hub') "
                "and download the GGUF weights in the voxtral-mini-realtime-rs repo, "
                "or set VOXTRAL_STT_BIN / VOXTRAL_STT_GGUF / VOXTRAL_STT_TOKENIZER."
            )
        self._validated = True

    def unload_model(self) -> None:
        # Nothing resident to free; the subprocess exits per request.
        self._validated = False

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        model_size: Optional[str] = None,
    ) -> str:
        """Transcribe a WAV file via the Voxtral CLI.

        ``language`` is accepted for interface parity; the CLI has no language
        flag (the model is multilingual and infers it), so it is ignored.
        """
        await self.load_model(model_size)
        wav_16k = await asyncio.to_thread(self._to_16k_mono_wav, audio_path)
        try:
            return await asyncio.to_thread(self._run_cli, wav_16k)
        finally:
            if wav_16k != audio_path:
                Path(wav_16k).unlink(missing_ok=True)

    # ── internals ────────────────────────────────────────────────────

    def _to_16k_mono_wav(self, src: str) -> str:
        """Transcode any input to 16 kHz mono 16-bit PCM WAV via ffmpeg.

        Voxtral expects 16 kHz mono; callers may hand us 24 kHz or other
        formats. Returns ``src`` unchanged only if ffmpeg is unavailable
        (best-effort: a correctly-formatted input still works)."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("ffmpeg not found; passing audio to Voxtral unconverted")
            return src
        fd, out_path = tempfile.mkstemp(suffix=".voxtral16k.wav")
        os.close(fd)
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", src, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            Path(out_path).unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg transcode for Voxtral failed: {proc.stderr.strip()}")
        return out_path

    def _run_cli(self, wav_path: str) -> str:
        cmd = [
            str(self._binary), "transcribe",
            "--audio", wav_path,
            "--gguf", str(self._gguf),
            "--tokenizer", str(self._tokenizer),
        ]
        delay = os.environ.get("VOXTRAL_STT_DELAY")
        if delay:
            cmd += ["--delay", delay]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_TRANSCRIBE_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Voxtral transcription timed out after {_TRANSCRIBE_TIMEOUT}s"
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Voxtral CLI failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )
        # The CLI prints only the transcript to stdout; logs go to stderr.
        return proc.stdout.strip()
