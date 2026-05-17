"""
Kyutai Pocket TTS backend.

Wraps kyutai/pocket-tts — a ~100M-param PyTorch TTS that runs on CPU at
~10x realtime. Ships per-language models for English, French, Spanish,
German, Italian, and Portuguese. The PyPI package downloads each language
on demand the first time it's requested.

Voice catalog: 26 built-in voices (cosette, marius, alba, etc.) loaded via
get_state_for_audio_prompt(voice_name). Cloning from arbitrary user audio
is gated by HuggingFace terms acceptance on kyutai/pocket-tts and is not
exposed by this backend; users who want cloning can accept the terms and
run `hf auth login` locally, then extend create_voice_prompt() to pass
the local audio path instead of a preset voice id.
"""

import asyncio
import logging
import threading
from typing import ClassVar, Optional

import numpy as np

from . import TTSBackend
from .base import (
    combine_voice_prompts as _combine_voice_prompts,
    model_load_progress,
)

logger = logging.getLogger(__name__)

KYUTAI_HF_REPO = "kyutai/pocket-tts"
KYUTAI_SAMPLE_RATE = 24000
KYUTAI_DEFAULT_VOICE = "alba"

# Voicebox ISO codes -> pocket-tts language names accepted by TTSModel.load_model().
# pocket-tts publishes a base model for most languages plus an opt-in "_24l"
# variant (bigger, slower, higher quality). French only ships as 24l upstream.
KYUTAI_LANG_MAP = {
    "en": "english",
    "fr": "french_24l",  # base "french" not published by upstream
    "es": "spanish",
    "de": "german",
    "it": "italian",
    "pt": "portuguese",
}

# Each tuple = (voice_id, display_name, gender, language).
# pocket-tts ships these 26 voices in its bundled tts-voices catalog. The
# voice id is what get_state_for_audio_prompt(name) consumes.
KYUTAI_VOICES = [
    ("cosette", "Cosette", "female", "multi"),
    ("marius", "Marius", "male", "multi"),
    ("javert", "Javert", "male", "multi"),
    ("alba", "Alba", "female", "multi"),
    ("jean", "Jean", "male", "multi"),
    ("anna", "Anna", "female", "multi"),
    ("vera", "Vera", "female", "multi"),
    ("fantine", "Fantine", "female", "multi"),
    ("charles", "Charles", "male", "multi"),
    ("paul", "Paul", "male", "multi"),
    ("eponine", "Éponine", "female", "multi"),
    ("azelma", "Azelma", "female", "multi"),
    ("george", "George", "male", "multi"),
    ("mary", "Mary", "female", "multi"),
    ("jane", "Jane", "female", "multi"),
    ("michael", "Michael", "male", "multi"),
    ("eve", "Eve", "female", "multi"),
    ("bill_boerst", "Bill Boerst", "male", "multi"),
    ("peter_yearsley", "Peter Yearsley", "male", "multi"),
    ("stuart_bell", "Stuart Bell", "male", "multi"),
    ("caro_davy", "Caro Davy", "female", "multi"),
    ("giovanni", "Giovanni", "male", "multi"),
    ("lola", "Lola", "female", "multi"),
    ("juergen", "Juergen", "male", "multi"),
    ("rafael", "Rafael", "male", "multi"),
    ("estelle", "Estelle", "female", "multi"),
]


class KyutaiPocketTTSBackend:
    """Kyutai Pocket TTS — PyTorch, CPU, preset voices, 6 languages."""

    _load_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self):
        self._models: dict[str, object] = {}
        self._voice_state_cache: dict[tuple[str, str], object] = {}
        self.model_size = "default"

    def is_loaded(self) -> bool:
        return bool(self._models)

    def _get_model_path(self, model_size: str) -> str:
        return KYUTAI_HF_REPO

    def _resolve_lang(self, language: str) -> str:
        return KYUTAI_LANG_MAP.get(language, "english")

    def _is_model_cached(self, model_size: str = "default") -> bool:
        """
        pocket-tts caches its assets under huggingface_hub's cache. Checking
        for ANY pocket-tts safetensors covers the case where at least one
        language has been downloaded; per-language detection happens lazily
        inside load_model.
        """
        from .base import is_model_cached

        return is_model_cached(KYUTAI_HF_REPO, weight_extensions=(".safetensors",))

    async def load_model(self, model_size: str = "default") -> None:
        """Preload English by default; other languages load lazily on demand."""
        if "english" in self._models:
            return
        await asyncio.to_thread(self._load_lang_sync, "english")

    def _load_lang_sync(self, lang_name: str):
        if lang_name in self._models:
            return

        from pocket_tts import TTSModel

        model_name = "kyutai-pocket-tts" if lang_name == "english" else f"kyutai-pocket-tts-{lang_name}"
        is_cached = self._is_model_cached()

        with model_load_progress(model_name, is_cached):
            logger.info(f"Loading Kyutai Pocket TTS [{lang_name}] (PyTorch CPU)...")
            with self._load_lock:
                self._models[lang_name] = TTSModel.load_model(lang_name)

        logger.info(f"Kyutai Pocket TTS [{lang_name}] loaded successfully")

    def unload_model(self) -> None:
        if self._models:
            self._models.clear()
            self._voice_state_cache.clear()
            logger.info("Kyutai Pocket TTS unloaded")

    async def create_voice_prompt(
        self,
        audio_path: str,
        reference_text: str,
        use_cache: bool = True,
    ) -> tuple[dict, bool]:
        """
        Kyutai voice cloning from arbitrary audio requires accepting the
        gated terms on huggingface.co/kyutai/pocket-tts plus a logged-in HF
        token, so this backend treats Kyutai as preset-only. The cloned-
        profile fallback returns the default voice.
        """
        return {
            "voice_type": "preset",
            "preset_engine": "kyutai_pocket",
            "preset_voice_id": KYUTAI_DEFAULT_VOICE,
        }, False

    async def combine_voice_prompts(
        self,
        audio_paths: list[str],
        reference_texts: list[str],
    ) -> tuple[np.ndarray, str]:
        return await _combine_voice_prompts(audio_paths, reference_texts, sample_rate=KYUTAI_SAMPLE_RATE)

    def _get_voice_state(self, lang_name: str, voice_id: str):
        key = (lang_name, voice_id)
        if key in self._voice_state_cache:
            return self._voice_state_cache[key]
        state = self._models[lang_name].get_state_for_audio_prompt(voice_id)
        self._voice_state_cache[key] = state
        return state

    async def generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str = "en",
        seed: Optional[int] = None,
        instruct: Optional[str] = None,
    ) -> tuple[np.ndarray, int]:
        lang_name = self._resolve_lang(language)
        if lang_name not in self._models:
            await asyncio.to_thread(self._load_lang_sync, lang_name)

        voice_id = (
            voice_prompt.get("preset_voice_id")
            or voice_prompt.get("kyutai_voice")
            or KYUTAI_DEFAULT_VOICE
        )

        def _generate_sync():
            import torch

            if seed is not None:
                torch.manual_seed(seed)

            voice_state = self._get_voice_state(lang_name, voice_id)
            audio = self._models[lang_name].generate_audio(voice_state, text)

            if isinstance(audio, torch.Tensor):
                audio = audio.detach().cpu().numpy()
            audio = np.asarray(audio, dtype=np.float32)
            if audio.ndim > 1:
                audio = audio.squeeze()

            return audio, KYUTAI_SAMPLE_RATE

        return await asyncio.to_thread(_generate_sync)
