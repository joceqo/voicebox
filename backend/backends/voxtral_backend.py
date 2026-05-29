"""
Mistral Voxtral TTS backend (MLX, Apple Silicon).

Wraps ``mistralai/Voxtral-4B-TTS-2603`` via the MLX 4-bit community
quantization (``mlx-community/Voxtral-4B-TTS-2603-mlx-4bit``, ~2.5GB) run
through Blaizzy's ``mlx-audio`` library. Voxtral is a ~4.1B-param multilingual
TTS (3.4B Mistral decoder + ~390M flow-matching acoustic head + ~300M codec)
covering 9 languages — English, French, Spanish, German, Italian, Portuguese,
Dutch, Arabic, and Hindi. On Apple Silicon the 4-bit variant runs faster than
realtime (RTF ~0.74-0.97).

Voxtral is a PRESET-voice engine: it ships 20 built-in speaker embeddings
(``casual_male``, ``fr_female``, …) loaded from the model's ``voice_embedding/``
directory. It does NOT clone from arbitrary user audio, so this backend mirrors
the supertonic/kyutai_pocket preset pattern. Each preset voice is tied to a
language (the model picks the language from the chosen voice + the text), so
the voice catalog records the ISO language per voice.

The model is loaded with ``mlx_audio.tts.utils.load(repo)`` and synthesizes via
``model.generate(text=..., voice=...)``, which yields a single non-streaming
``GenerationResult`` with ``.audio`` (a 24kHz ``mx.array``) and ``.sample_rate``.
There is no explicit ``language`` argument — language is implied by the voice
and text.

LICENSE: the Voxtral TTS weights are released under CC BY-NC 4.0
(non-commercial use only). See https://huggingface.co/mistralai/Voxtral-4B-TTS-2603.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar, Optional
import threading

import numpy as np

from . import TTSBackend
from .base import (
    combine_voice_prompts as _combine_voice_prompts,
    model_load_progress,
)

logger = logging.getLogger(__name__)

# MLX bf16 (~8GB RAM/disk). Downloaded on first use. The "garbled initial
# frames" / onset artifacts are MORE pronounced on quantized variants
# (4bit/6bit), so bf16 is used for cleaner output. Trade-off: ~8GB resident +
# slower. See mlx-community/Voxtral-4B-TTS-2603-mlx-{4bit,6bit,bf16}.
VOXTRAL_HF_REPO = "mlx-community/Voxtral-4B-TTS-2603-mlx-bf16"
VOXTRAL_SAMPLE_RATE = 24000
VOXTRAL_DEFAULT_VOICE = "casual_male"

# 9 languages supported by Voxtral.
VOXTRAL_LANGUAGES = ["en", "fr", "es", "de", "it", "pt", "nl", "ar", "hi"]

# Each tuple = (voice_id, display_name, gender, iso_language).
# These are the 20 built-in preset speakers shipped in the model's
# voice_embedding/ directory. Voice id is what model.generate(voice=...) takes.
# Unlike supertonic/kyutai (language-agnostic "multi" voices), each Voxtral
# voice is pinned to a language, so we record the ISO code.
VOXTRAL_VOICES = [
    ("casual_male", "Casual Male", "male", "en"),
    ("casual_female", "Casual Female", "female", "en"),
    ("cheerful_female", "Cheerful Female", "female", "en"),
    ("neutral_male", "Neutral Male", "male", "en"),
    ("neutral_female", "Neutral Female", "female", "en"),
    ("fr_male", "French Male", "male", "fr"),
    ("fr_female", "French Female", "female", "fr"),
    ("es_male", "Spanish Male", "male", "es"),
    ("es_female", "Spanish Female", "female", "es"),
    ("de_male", "German Male", "male", "de"),
    ("de_female", "German Female", "female", "de"),
    ("it_male", "Italian Male", "male", "it"),
    ("it_female", "Italian Female", "female", "it"),
    ("pt_male", "Portuguese Male", "male", "pt"),
    ("pt_female", "Portuguese Female", "female", "pt"),
    ("nl_male", "Dutch Male", "male", "nl"),
    ("nl_female", "Dutch Female", "female", "nl"),
    ("ar_male", "Arabic Male", "male", "ar"),
    ("hi_male", "Hindi Male", "male", "hi"),
    ("hi_female", "Hindi Female", "female", "hi"),
]


class VoxtralTTSBackend:
    """Mistral Voxtral TTS — MLX 4-bit, Apple Silicon, 9 langs, 20 preset voices.

    Preset-voice engine (no zero-shot cloning). CC BY-NC 4.0 weights.
    """

    _load_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self):
        self._model = None
        self.model_size = "default"
        # MLX uses a thread-local GPU stream: GPU ops must run on a thread that
        # has the Metal stream established. asyncio.to_thread() dispatches onto
        # an arbitrary pool thread, so in the adaptive pipeline (background
        # tasks) Voxtral's MLX generate() crashes with
        #   "RuntimeError: There is no Stream(gpu, 0) in current thread".
        # Fix: own a SINGLE dedicated long-lived worker thread and run BOTH the
        # model load and every generate() on it. The Metal default stream that
        # gets established on that thread during load then persists for every
        # subsequent op, regardless of which asyncio task submitted the work.
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="voxtral-mlx"
        )

    def is_loaded(self) -> bool:
        return self._model is not None

    async def _run_on_worker(self, fn):
        """Run ``fn`` on the dedicated single MLX worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    def _get_model_path(self, model_size: str = "default") -> str:
        return VOXTRAL_HF_REPO

    def _is_model_cached(self, model_size: str = "default") -> bool:
        """Voxtral MLX weights cache under the HuggingFace Hub cache."""
        from .base import is_model_cached

        return is_model_cached(VOXTRAL_HF_REPO, weight_extensions=(".safetensors",))

    async def load_model(self, model_size: str = "default") -> None:
        if self._model is not None:
            return
        # Load on the dedicated worker so the MLX GPU stream/device context is
        # established on the same thread that will later run generate().
        await self._run_on_worker(self._load_model_sync)

    def _load_model_sync(self):
        if self._model is not None:
            return

        from mlx_audio.tts.utils import load

        # Establish the MLX default GPU device/stream on THIS (worker) thread so
        # all subsequent generate() ops have a valid Stream(gpu, 0) here.
        self._ensure_mlx_stream()

        is_cached = self._is_model_cached()
        with model_load_progress("voxtral-tts", is_cached):
            logger.info("Loading Voxtral TTS (MLX 4-bit, Apple Silicon)...")
            with self._load_lock:
                if self._model is None:
                    self._model = load(VOXTRAL_HF_REPO)

        logger.info("Voxtral TTS loaded successfully")

    @staticmethod
    def _ensure_mlx_stream():
        """Establish/activate the MLX default GPU stream on the current thread.

        MLX GPU ops require a Stream(gpu) on the calling thread. Setting the
        default device to GPU and touching the default stream here makes that
        stream exist for this worker thread.
        """
        import mlx.core as mx

        try:
            mx.set_default_device(mx.gpu)
        except Exception:
            # CPU-only / non-Metal builds: fall back silently.
            return
        try:
            mx.set_default_stream(mx.default_stream(mx.gpu))
        except Exception:
            pass

    def unload_model(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            try:
                import mlx.core as mx

                mx.clear_cache()
            except Exception:
                pass
            logger.info("Voxtral TTS unloaded")

    async def create_voice_prompt(
        self,
        audio_path: str,
        reference_text: str,
        use_cache: bool = True,
    ) -> tuple[dict, bool]:
        """
        Voxtral does not support zero-shot cloning from arbitrary audio. Voice
        prompts are always preset references; the cloned-profile path falls
        back to the default voice.
        """
        return {
            "voice_type": "preset",
            "preset_engine": "voxtral",
            "preset_voice_id": VOXTRAL_DEFAULT_VOICE,
        }, False

    async def combine_voice_prompts(
        self,
        audio_paths: list[str],
        reference_texts: list[str],
    ) -> tuple[np.ndarray, str]:
        return await _combine_voice_prompts(
            audio_paths, reference_texts, sample_rate=VOXTRAL_SAMPLE_RATE
        )

    async def generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str = "en",
        seed: Optional[int] = None,
        instruct: Optional[str] = None,
    ) -> tuple[np.ndarray, int]:
        await self.load_model()

        voice = (
            voice_prompt.get("preset_voice_id")
            or voice_prompt.get("voxtral_voice")
            or VOXTRAL_DEFAULT_VOICE
        )
        # Voxtral has no explicit language argument — the chosen voice (and the
        # text) determine the language. `language` is accepted for protocol
        # compatibility but not forwarded; the OpenAI single-engine path already
        # resolves a language-appropriate voice via _get_preset_voice_language.

        def _generate_sync():
            import mlx.core as mx

            # Re-assert the GPU stream on this worker thread (idempotent; cheap)
            # so generate() always sees a valid Stream(gpu, 0) even if this
            # closure is the first MLX op to run after a long idle period.
            self._ensure_mlx_stream()

            if seed is not None:
                mx.random.seed(seed)

            # Non-streaming generate() yields a single GenerationResult.
            audio_chunks = []
            sample_rate = VOXTRAL_SAMPLE_RATE
            for result in self._model.generate(text=text, voice=voice):
                raw = result.audio
                # Fully materialize the lazy MLX array ON THIS thread before it
                # crosses back to the asyncio thread — mx.eval forces the
                # computation here (where the GPU stream is valid), then
                # np.asarray copies it into host memory.
                if isinstance(raw, mx.array):
                    mx.eval(raw)
                chunk = np.asarray(raw, dtype=np.float32)
                if chunk.ndim > 1:
                    chunk = chunk.squeeze()
                if chunk.size > 0:
                    audio_chunks.append(chunk)
                sr = getattr(result, "sample_rate", None)
                if isinstance(sr, (int, float)) and sr > 0:
                    sample_rate = int(sr)

            if not audio_chunks:
                return np.zeros(1, dtype=np.float32), sample_rate

            audio = np.concatenate(audio_chunks) if len(audio_chunks) > 1 else audio_chunks[0]
            return audio, sample_rate

        # Run on the dedicated single MLX worker thread (NOT asyncio.to_thread's
        # shared pool) so the Metal stream context persists across calls.
        return await self._run_on_worker(_generate_sync)
