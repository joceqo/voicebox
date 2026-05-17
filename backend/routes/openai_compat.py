"""OpenAI-compatible ``POST /v1/audio/speech`` endpoint.

Streams adaptive multi-tier audio (or single-engine fallback) to any
OpenAI-compatible client.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..models import OpenAISpeechRequest
from ..services.adaptive import (
    AdaptiveSession,
    WAV_HEADER_24K_MONO_INT16,
    resolve_active_tiers,
    sweep_adaptive_sessions,
)
from ..services.tiers import Tier

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["openai-compat"])

_SESSION_SEMAPHORE = asyncio.Semaphore(1)


def _model_to_engine(model: str) -> str | None:
    """Extract a specific engine name from ``voicebox-<engine>`` patterns."""
    if model.startswith("voicebox-"):
        return model[len("voicebox-"):]
    return None


async def _single_engine_stream(
    engine: str,
    voice: str,
    text: str,
    language: str,
    response_format: str,
) -> StreamingResponse:
    """Fallback: single-engine generation (non-adaptive)."""
    from ..services.generation import quick_generate_sync
    from ..services.profiles import _get_preset_voice_ids

    valid = _get_preset_voice_ids(engine)
    if not valid:
        raise HTTPException(
            status_code=400,
            detail=f"Engine '{engine}' does not support preset voices",
        )
    voice_id = voice if voice in valid else next(iter(valid))

    wav_bytes = await quick_generate_sync(
        engine=engine,
        voice_id=voice_id,
        text=text,
        language=language,
    )

    if response_format == "pcm":
        # Strip WAV header (44 bytes) and return raw PCM.
        pcm_data = wav_bytes[44:]
        return StreamingResponse(
            iter([pcm_data]),
            media_type="application/octet-stream",
        )

    return StreamingResponse(
        iter([wav_bytes]),
        media_type="audio/wav",
    )


@router.post("/audio/speech")
async def create_speech(req: OpenAISpeechRequest):
    """Create speech from text — OpenAI-compatible streaming endpoint.

    Maps ``{model: "tts-1"|"tts-1-hd"|"voicebox-adaptive"}`` to the
    adaptive pipeline. ``{model: "voicebox-<engine>"}`` selects a
    specific single engine.

    Supports ``response_format``: ``wav`` (streaming header + PCM),
    ``pcm`` (raw int16). ``mp3``/``opus``/``aac``/``flac`` return 400.
    """

    if req.response_format in ("mp3", "opus", "aac", "flac"):
        raise HTTPException(
            status_code=400,
            detail=f"response_format '{req.response_format}' is not supported yet. Use 'wav' or 'pcm'.",
        )

    specific_engine = _model_to_engine(req.model)

    if specific_engine:
        # Single-engine fast path.
        return await _single_engine_stream(
            engine=specific_engine,
            voice=req.voice,
            text=req.input,
            language="en",
            response_format=req.response_format,
        )

    # ── Adaptive pipeline ────────────────────────────────────────────
    tiers = resolve_active_tiers()
    if not tiers:
        raise HTTPException(
            status_code=503,
            detail="No adaptive TTS tiers available. Set VOICEBOX_ADAPTIVE_TIERS or download a model first.",
        )

    async with _SESSION_SEMAPHORE:
        session = await AdaptiveSession.start(
            req.input,
            req.voice,
            language="en",
            tiers=tiers,
        )

    async def stream():
        try:
            if req.response_format == "wav":
                yield WAV_HEADER_24K_MONO_INT16
            for i in range(session.n_chunks):
                tier_idx = await session.wait_and_pick(i)
                async for block in session.read_pcm(i, tier_idx):
                    yield block
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            logger.debug("Client disconnected mid-stream, cancelling session")
        finally:
            await session.close()

    media = "audio/wav" if req.response_format == "wav" else "application/octet-stream"
    return StreamingResponse(stream(), media_type=media)
