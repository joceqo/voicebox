"""
NVIDIA Parakeet TDT 0.6B v3 STT backend implementation.

Wraps the Parakeet TDT 0.6B v3 model via the ``onnx-asr`` package, which
loads the ONNX export (istupakov/parakeet-tdt-0.6b-v3-onnx) and handles the
HuggingFace download on first use. The model runs entirely on CPU (~5x
realtime), supports 25 European languages (incl. FR + EN) with automatic
language detection, and reaches ~6.34% WER.

Unlike Whisper, Parakeet auto-detects the spoken language, so the ``language``
hint passed to :meth:`transcribe` is accepted for interface compatibility but
ignored by the model.

API confirmed against:
- https://pypi.org/project/onnx-asr/
- https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx
    import onnx_asr
    model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")
    print(model.recognize("test.wav"))

The ``onnx-asr`` package downloads the ONNX weights from the HuggingFace Hub
on first :func:`onnx_asr.load_model` call (no separate cache layout to probe
the way Whisper / Supertonic do), so we accept first-call download latency and
load lazily inside a worker thread.
"""

import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# onnx-asr model id for the Parakeet TDT 0.6B v3 ONNX export.
PARAKEET_ONNX_MODEL_ID = "nemo-parakeet-tdt-0.6b-v3"
# HuggingFace repo backing the ONNX export (for display / config only).
PARAKEET_HF_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"

PARAKEET_LANGUAGES = [
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de",
    "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk",
    "sl", "es", "sv", "ru", "uk",
]


class ParakeetSTTBackend:
    """Parakeet TDT 0.6B v3 STT backend — ONNX, CPU, 25 langs, auto lang detect.

    Implements the ``STTBackend`` protocol. The loaded onnx-asr model is held
    on the instance (the module-level singleton in ``backends.__init__`` makes
    it a process-wide singleton, mirroring the Whisper backend getter).
    """

    def __init__(self):
        self._model = None
        # Parakeet has a single variant; expose a stable identifier so the
        # dispatch / unload helpers can compare against the config model_size.
        self.model_size = "v3"
        self._load_lock = threading.Lock()

    def is_loaded(self) -> bool:
        return self._model is not None

    def _is_model_cached(self, model_size: str = "v3") -> bool:
        """Best-effort cache probe.

        onnx-asr stores the ONNX weights in the standard HuggingFace Hub
        cache. We reuse the shared HF cache check against the backing repo so
        callers can decide whether the first call will stall on a download.
        Returns False if the check can't be performed.
        """
        try:
            from .base import is_model_cached

            return is_model_cached(PARAKEET_HF_REPO)
        except Exception:
            return False

    async def load_model_async(self, model_size: Optional[str] = None) -> None:
        """Lazily load the Parakeet ONNX model (downloads on first call)."""
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_model_sync)

    # Alias for STTBackend protocol compatibility.
    load_model = load_model_async

    def _load_model_sync(self):
        # Guard against concurrent loads racing the heavy onnx-asr import.
        with self._load_lock:
            if self._model is not None:
                return

            from .base import model_load_progress

            is_cached = self._is_model_cached()
            with model_load_progress("parakeet-v3", is_cached):
                import onnx_asr

                logger.info(
                    "Loading Parakeet TDT 0.6B v3 (ONNX, CPU) via onnx-asr (%s)...",
                    PARAKEET_ONNX_MODEL_ID,
                )
                # int8 is required: the full-precision export stores weights as
                # external data that onnx-asr loads from bytes, which trips
                # onnxruntime ("model_path must not be empty"). The int8 variant
                # ships self-contained files and loads cleanly on CPU.
                self._model = onnx_asr.load_model(
                    PARAKEET_ONNX_MODEL_ID, quantization="int8"
                )

            logger.info("Parakeet TDT 0.6B v3 loaded successfully")

    def unload_model(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            logger.info("Parakeet TDT 0.6B v3 unloaded")

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        model_size: Optional[str] = None,
    ) -> str:
        """Transcribe audio to text.

        Parakeet auto-detects the spoken language, so ``language`` is accepted
        for interface parity with Whisper but not forwarded to the model.
        ONNX inference is CPU-bound, so it runs in a worker thread.
        """
        await self.load_model_async(model_size)

        def _transcribe_sync() -> str:
            # onnx-asr's file reader does NOT resample reliably — feeding it a
            # non-16kHz WAV (e.g. 24kHz TTS output) triggers a bogus reshape
            # ("cannot reshape array ... into shape (1073741823, 1)"). So we
            # load + resample to 16kHz mono float32 ourselves and pass the array
            # with an explicit sample_rate. recognize() returns the transcript.
            import librosa

            waveform, _ = librosa.load(audio_path, sr=16000, mono=True)
            result = self._model.recognize(waveform, sample_rate=16000)
            if isinstance(result, str):
                return result.strip()
            # Defensive: some onnx-asr versions may return a richer object.
            text = getattr(result, "text", None)
            if isinstance(text, str):
                return text.strip()
            return str(result).strip()

        return await asyncio.to_thread(_transcribe_sync)
