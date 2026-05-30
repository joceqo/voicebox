"""Mistral (Voxtral) voice provider.

Proxies to Mistral's audio API:
  - TTS: POST {base}/audio/speech   → JSON {input, model, voice_id, response_format} → {audio_data: <base64>}
  - STT: POST {base}/audio/transcriptions (multipart: file + model) → {text, ...}
  - validate: GET {base}/models with the key.

Docs: https://docs.mistral.ai/studio-api/audio/
"""

from __future__ import annotations

import base64
import logging

import httpx

from .base import VoiceProvider

logger = logging.getLogger(__name__)

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"

# OpenAI's default voice sentinel; Mistral has its own voice ids
# (e.g. "en_paul_excited"), so when a client leaves voice unset we omit
# voice_id and let Mistral pick its default rather than send "alloy".
_OPENAI_DEFAULT_VOICE = "alloy"

_FORMAT_MEDIA_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "opus": "audio/opus",
    "pcm": "application/octet-stream",
}
_SUPPORTED_FORMATS = set(_FORMAT_MEDIA_TYPES)


class MistralProvider(VoiceProvider):
    key = "mistral"
    name = "Mistral (Voxtral)"
    base_url = MISTRAL_BASE_URL
    description = "Voxtral cloud TTS — expressive preset voices, proxied via your Mistral API key."
    # Voxtral TTS languages.
    languages = ["en", "fr", "es", "pt", "it", "nl", "de", "hi", "ar"]
    tts_models = [("voxtral-mini-tts-2603", "Voxtral Mini TTS")]
    stt_models = [("voxtral-mini-latest", "Voxtral Mini Transcribe")]

    async def list_voices(self, api_key: str) -> list[dict]:
        # GET /audio/voices is paginated (default page is small) — request a high
        # limit so we get the full catalog (currently ~30 voices across en/gb/fr,
        # incl. the French "Marie" set), not just the first page.
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{self.base_url}/audio/voices",
                params={"limit": 500},
                headers={"Authorization": f"Bearer {api_key}"},
            )
        resp.raise_for_status()
        out: list[dict] = []
        for v in resp.json().get("items", []):
            slug = v.get("slug") or v.get("id")
            if not slug:
                continue
            langs = v.get("languages") or []
            # Mistral uses locale-ish codes like "en_us"/"fr_fr"; keep the ISO root.
            lang = langs[0].split("_")[0] if langs else "en"
            out.append(
                {
                    "voice_id": slug,
                    "name": v.get("name") or slug,
                    "gender": v.get("gender") or "unknown",
                    "language": lang,
                }
            )
        return out

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
        # Mistral doesn't support aac; fall back to wav for unsupported formats.
        fmt = response_format if response_format in _SUPPORTED_FORMATS else "wav"
        payload: dict = {
            "model": model,
            "input": text,
            "response_format": fmt,
            "stream": False,
        }
        if voice and voice != _OPENAI_DEFAULT_VOICE:
            payload["voice_id"] = voice

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        resp.raise_for_status()
        audio_b64 = resp.json().get("audio_data")
        if not audio_b64:
            raise RuntimeError("Mistral TTS returned no audio_data")
        return base64.b64decode(audio_b64), _FORMAT_MEDIA_TYPES[fmt]

    async def transcribe(
        self,
        *,
        api_key: str,
        model: str,
        file_bytes: bytes,
        filename: str,
        language: str | None,
    ) -> str:
        data: dict = {"model": model}
        if language:
            data["language"] = language
        files = {"file": (filename or "audio.wav", file_bytes)}

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files=files,
            )
        resp.raise_for_status()
        return resp.json().get("text", "")

    async def validate(self, api_key: str) -> tuple[bool, str | None]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except Exception as e:  # network error, DNS, timeout…
            return False, f"Could not reach Mistral: {e}"
        if resp.status_code == 200:
            return True, None
        if resp.status_code in (401, 403):
            return False, "Invalid API key"
        return False, f"Unexpected response from Mistral ({resp.status_code})"

    async def _default_voice(self, api_key: str) -> str:
        """Pick a usable voice slug for the test (any one will do).

        Tries the live voices list first; falls back to a known preset so the
        test still runs if the voices endpoint is unavailable.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/audio/voices",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            for v in items:
                slug = v.get("slug") or v.get("id")
                if slug:
                    return slug
        except Exception:
            logger.debug("Could not list Mistral voices for test; using fallback", exc_info=True)
        return "en_paul_neutral"

    async def test_generation(self, api_key: str) -> tuple[bool, str | None]:
        # Auth first (cheap, no credit spent) so a bad key reports clearly.
        ok, detail = await self.validate(api_key)
        if not ok:
            return ok, detail

        voice = await self._default_voice(api_key)
        try:
            audio, _ = await self.speech(
                api_key=api_key,
                model=self.tts_models[0][0],
                voice=voice,
                text="Test.",
                language=None,
                response_format="wav",
            )
        except httpx.HTTPStatusError as e:
            body = e.response.text[:200]
            if e.response.status_code in (402, 429):
                return False, f"Quota/credit issue ({e.response.status_code}): {body}"
            return False, f"Generation failed ({e.response.status_code}): {body}"
        except Exception as e:
            return False, f"Generation failed: {e}"

        if not audio:
            return False, "Generation returned no audio"
        kb = len(audio) // 1024
        return True, f"Generated {kb} KB of audio with voice '{voice}'. Credit OK."
