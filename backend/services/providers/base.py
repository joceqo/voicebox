"""Upstream voice-provider abstraction.

A provider proxies Voicebox's OpenAI-compatible ``/v1/audio/*`` requests to a
frontier API (Mistral, OpenAI, ElevenLabs, …). Each provider declares the
models it exposes and implements ``speech`` / ``transcribe`` against the
upstream REST API. The registry in ``__init__`` maps a model string like
``mistral/voxtral-mini-tts-2603`` to a provider + upstream model id.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class VoiceProvider(ABC):
    """Base class for an upstream voice provider.

    Subclasses set the class attributes and implement the two coroutines.
    Methods take the api_key explicitly so the provider holds no per-user
    state and a single instance can be shared across requests.
    """

    key: str  # registry key, e.g. "mistral" (also the model namespace prefix)
    name: str  # display name, e.g. "Mistral (Voxtral)"
    base_url: str  # upstream API base, e.g. "https://api.mistral.ai/v1"
    description: str = ""  # shown in the engine catalog
    languages: list[str] = []  # ISO codes the provider's TTS supports
    # (upstream_model_id, human label) pairs.
    tts_models: list[tuple[str, str]] = []
    stt_models: list[tuple[str, str]] = []

    async def list_voices(self, api_key: str) -> list[dict]:
        """List the provider's preset voices.

        Returns dicts with ``voice_id``, ``name``, ``gender``, ``language``
        (ISO). Default: none. Providers with preset voices override this.
        """
        return []

    @abstractmethod
    async def speech(
        self,
        *,
        api_key: str,
        model: str,
        voice: str | None,
        text: str,
        language: str | None,
        response_format: str,
    ) -> tuple[bytes, str]:
        """Synthesize speech upstream. Returns ``(audio_bytes, media_type)``."""

    @abstractmethod
    async def transcribe(
        self,
        *,
        api_key: str,
        model: str,
        file_bytes: bytes,
        filename: str,
        language: str | None,
    ) -> str:
        """Transcribe audio upstream. Returns the transcript text."""

    async def validate(self, api_key: str) -> tuple[bool, str | None]:
        """Best-effort key check. Returns ``(is_valid, error_detail)``.

        Default implementation reports valid (no cheap probe available);
        providers override with a lightweight authenticated call.
        """
        return True, None

    async def test_generation(self, api_key: str) -> tuple[bool, str | None]:
        """Real end-to-end check: actually generate something.

        Unlike :meth:`validate` (auth only), this exercises the generation
        pipeline and surfaces credit/quota errors. Returns ``(ok, detail)``
        where detail is a human-readable success summary or the upstream error.
        Default implementation falls back to :meth:`validate`.
        """
        return await self.validate(api_key)
