"""Adapter that makes an upstream provider look like a local ``TTSBackend``.

This lets cloud providers (Mistral, …) flow through the exact same generation
pipeline as local engines — ``get_tts_backend_for_engine`` returns one of these,
``generate_chunked`` calls ``generate()``, and history/normalization/chunking
all work unchanged. ``generate()`` calls the provider's ``speech()`` (asking for
WAV) and decodes it to the ``(np.ndarray, sample_rate)`` the pipeline expects.
"""

from __future__ import annotations

import io
import logging

import numpy as np
import soundfile as sf

from .base import VoiceProvider

logger = logging.getLogger(__name__)


class ProviderTTSBackend:
    """Conforms to the in-process ``TTSBackend`` protocol, backed by HTTP."""

    def __init__(self, provider: VoiceProvider):
        self.provider = provider

    # ── Lifecycle no-ops (no local model to load) ────────────────────
    async def load_model(self, model_size: str = "default") -> None:
        return None

    async def load_model_async(self, model_size: str = "default") -> None:
        return None

    def unload_model(self) -> None:
        return None

    def is_loaded(self) -> bool:
        return True

    def _is_model_cached(self, model_size: str = "default") -> bool:
        # "Cached" = a key is configured; otherwise generation will 400.
        from . import get_api_key

        return get_api_key(self.provider.key) is not None

    # ── Generation ───────────────────────────────────────────────────
    async def generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str = "en",
        seed=None,
        instruct=None,
    ):
        from . import get_api_key

        api_key = get_api_key(self.provider.key)
        if not api_key:
            raise RuntimeError(
                f"No API key configured for provider '{self.provider.key}'. "
                "Add one on the API Keys page."
            )
        voice = voice_prompt.get("preset_voice_id") if voice_prompt else None
        model = self.provider.tts_models[0][0]
        audio_bytes, _media = await self.provider.speech(
            api_key=api_key,
            model=model,
            voice=voice,
            text=text,
            language=language,
            response_format="wav",
        )
        data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if data.ndim > 1:  # downmix to mono
            data = data.mean(axis=1)
        return np.asarray(data, dtype=np.float32), int(sample_rate)
