"""OpenAI-compatible ``POST /v1/audio/speech`` endpoint.

Streams adaptive multi-tier audio (or single-engine fallback) to any
OpenAI-compatible client.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ..backends import WHISPER_HF_REPOS, get_stt_model_configs
from ..models import OpenAISpeechRequest
from ..services.adaptive import (
    AdaptiveSession,
    WAV_HEADER_24K_MONO_INT16,
    resolve_active_tiers,
    sweep_adaptive_sessions,
)
from ..services.tiers import Tier
from ..services.transcribe import ModelDownloadingError, transcribe_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["openai-compat"])

_SESSION_SEMAPHORE = asyncio.Semaphore(1)


def _model_to_engine(model: str) -> str | None:
    """Extract a specific engine name from ``voicebox-<engine>`` patterns."""
    if model.startswith("voicebox-"):
        return model[len("voicebox-"):]
    return None


# Preset engines whose voices may pin a language (kokoro, qwen_custom_voice).
# Engines with language-agnostic ("multi") voices return None and are harmless
# to probe here. Order matters only as a tie-break; voice ids are unique enough
# in practice that the first engine that knows the voice wins.
_ADAPTIVE_LANG_ENGINES = ("kokoro", "qwen_custom_voice", "supertonic", "kyutai_pocket")


def _resolve_adaptive_language(voice: str) -> str | None:
    """Best-effort language for the adaptive pipeline based on the voice.

    The adaptive pipeline isn't tied to a single engine, so we probe the preset
    engines for one that recognises the voice and pins a language. Returns None
    when no engine pins a language (e.g. language-agnostic voices), letting the
    caller fall back to its own default. Never raises.
    """
    from ..services.profiles import _get_preset_voice_ids, _get_preset_voice_language

    for engine in _ADAPTIVE_LANG_ENGINES:
        try:
            if voice in _get_preset_voice_ids(engine):
                lang = _get_preset_voice_language(engine, voice)
                if lang:
                    return lang
        except Exception:  # pragma: no cover - defensive, never crash the request
            logger.debug("language resolution failed for engine %s", engine, exc_info=True)
    return None


def _openai_error(status_code, message, err_type, code, headers=None):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": err_type, "code": code}},
        headers=headers,
    )


_OPENAI_STT_MODEL_MAP = {
    "whisper-1": "turbo",
    "gpt-4o-transcribe": "turbo",
    "gpt-4o-mini-transcribe": "small",
}

# OpenAI/alias names that route to the Parakeet engine. Resolved to the
# canonical "parakeet-v3" id, which transcribe_upload dispatches via
# resolve_stt_model. Whisper remains the default for everything else.
_PARAKEET_STT_NAMES = {
    "parakeet",
    "parakeet-v3",
    "parakeet-tdt-0.6b-v3",
    "nvidia/parakeet-tdt-0.6b-v3",
}


def _resolve_stt_model(model: str | None) -> str:
    """Map an OpenAI transcription model name to a local STT model id.

    Returns either a Whisper size (base/small/medium/large/turbo) or the
    Parakeet id ("parakeet-v3"). The value is passed through to
    ``transcribe_upload``, which dispatches to the right backend.
    """
    if not model:
        return "turbo"
    if model in _PARAKEET_STT_NAMES:                   # opt-in Parakeet engine
        return "parakeet-v3"
    if model in WHISPER_HF_REPOS:                      # already base/small/medium/large/turbo
        return model
    stripped = model[len("whisper-"):] if model.startswith("whisper-") else ""
    if stripped in WHISPER_HF_REPOS:                   # e.g. "whisper-turbo"
        return stripped
    return _OPENAI_STT_MODEL_MAP.get(model, "turbo")   # default fallback


async def _single_engine_stream(
    engine: str,
    voice: str,
    text: str,
    language: str | None,
    response_format: str,
) -> StreamingResponse:
    """Fallback: single-engine generation (non-adaptive).

    ``language`` is the caller's explicit override (or None). The effective
    language is resolved as ``language or language_of(voice) or "en"`` against
    the voice that is actually used (which may differ from the requested voice
    when an invalid voice falls back to the engine default).
    """
    from ..services.generation import quick_generate_sync
    from ..services.profiles import _get_preset_voice_ids, _get_preset_voice_language

    valid = _get_preset_voice_ids(engine)
    if not valid:
        raise HTTPException(
            status_code=400,
            detail=f"Engine '{engine}' does not support preset voices",
        )
    voice_id = voice if voice in valid else next(iter(valid))

    # Resolve against the voice actually used so the language matches it.
    effective_language = language or _get_preset_voice_language(engine, voice_id) or "en"

    wav_bytes = await quick_generate_sync(
        engine=engine,
        voice_id=voice_id,
        text=text,
        language=effective_language,
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
        # Single-engine fast path. Pass the explicit override through;
        # _single_engine_stream resolves voice-based language + "en" fallback
        # against the voice it actually uses.
        return await _single_engine_stream(
            engine=specific_engine,
            voice=req.voice,
            text=req.input,
            language=req.language,
            response_format=req.response_format,
        )

    # ── Adaptive pipeline ────────────────────────────────────────────
    tiers = resolve_active_tiers()
    if not tiers:
        raise HTTPException(
            status_code=503,
            detail="No adaptive TTS tiers available. Set VOICEBOX_ADAPTIVE_TIERS or download a model first.",
        )

    # Resolve the language for the adaptive pipeline. The engine isn't fixed
    # here, so honour an explicit override first; otherwise try to infer the
    # language from the voice across the preset engines, falling back to "en".
    # Never crash if the voice is unknown to any engine.
    adaptive_language = req.language or _resolve_adaptive_language(req.voice) or "en"

    async with _SESSION_SEMAPHORE:
        session = await AdaptiveSession.start(
            req.input,
            req.voice,
            language=adaptive_language,
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


@router.post("/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    model: str | None = Form(None),
    language: str | None = Form(None),
    response_format: str = Form("json"),
):
    """Transcribe audio — OpenAI-compatible. Maps OpenAI model names
    (whisper-1, gpt-4o-transcribe…) to local Whisper sizes. Supports
    response_format 'json' (default) and 'text'."""
    if response_format not in ("json", "text"):
        return _openai_error(
            400,
            f"response_format '{response_format}' is not supported. Use 'json' or 'text'.",
            "invalid_request_error",
            "unsupported_response_format",
        )
    stt_model = _resolve_stt_model(model)
    try:
        text, _duration = await transcribe_upload(file, language, stt_model)
    except ValueError as e:
        return _openai_error(400, str(e), "invalid_request_error", "invalid_model")
    except ModelDownloadingError as e:
        return _openai_error(
            503,
            f"Whisper model '{e.model_size}' is still downloading. Retry shortly.",
            "model_not_ready",
            "model_downloading",
            headers={"Retry-After": "10"},
        )
    except Exception as e:
        logger.exception("transcription failed")
        return _openai_error(500, str(e), "server_error", "transcription_failed")

    if response_format == "text":
        return PlainTextResponse(text)
    return {"text": text}


class OpenAIVoice(BaseModel):
    id: str
    engine: str
    model: str  # usable as the `model` field in POST /v1/audio/speech


class OpenAIVoicesResponse(BaseModel):
    object: str = "list"
    voices: list[OpenAIVoice]
    transcription_models: list[str]


@router.get("/audio/voices", response_model=OpenAIVoicesResponse)
async def list_voices():
    """List TTS voices and STT model names (OpenAI-ish catalog).
    Each voice's `model`+`id` round-trips into POST /v1/audio/speech as
    the `model` and `voice` fields."""
    from ..backends import _TTS_REGISTRY
    from ..services.profiles import _get_preset_voice_ids

    voices: list[OpenAIVoice] = []
    for entry in _TTS_REGISTRY:
        model_alias = f"voicebox-{entry.engine}"
        for voice_id in sorted(_get_preset_voice_ids(entry.engine)):
            voices.append(OpenAIVoice(id=voice_id, engine=entry.engine, model=model_alias))

    stt_models = [cfg.model_name for cfg in get_stt_model_configs()]
    return OpenAIVoicesResponse(voices=voices, transcription_models=stt_models)
