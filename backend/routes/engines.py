"""Engine catalog endpoint.

Serializes the in-process TTS / LLM engine registry so the desktop app
can render its engine picker, language list, and voice-mode badges
without hard-coding the same metadata in TypeScript.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from ..backends import (
    _LLM_REGISTRY,
    _TTS_REGISTRY,
    LLMEngineEntry,
    TTSEngineEntry,
)

router = APIRouter()


class EngineModelInfo(BaseModel):
    model_name: str
    display_name: str
    model_size: str
    size_mb: int


class TTSEngineInfo(BaseModel):
    engine: str
    display_name: str
    description: str
    voice_mode: str  # "preset" | "cloning"
    english_only: bool
    languages: list[str]
    models: list[EngineModelInfo]


class LLMEngineInfo(BaseModel):
    engine: str
    display_name: str
    description: str
    models: list[EngineModelInfo]


class EngineCatalogResponse(BaseModel):
    tts: list[TTSEngineInfo]
    llm: list[LLMEngineInfo]


def _serialize_tts(entry: TTSEngineEntry) -> TTSEngineInfo:
    configs = entry.model_configs()
    languages: list[str] = []
    seen: set[str] = set()
    for cfg in configs:
        for lang in cfg.languages:
            if lang not in seen:
                seen.add(lang)
                languages.append(lang)

    return TTSEngineInfo(
        engine=entry.engine,
        display_name=entry.display_name,
        description=entry.description,
        voice_mode=entry.voice_mode,
        english_only=entry.english_only,
        languages=languages,
        models=[
            EngineModelInfo(
                model_name=cfg.model_name,
                display_name=cfg.display_name,
                model_size=cfg.model_size,
                size_mb=cfg.size_mb,
            )
            for cfg in configs
        ],
    )


def _serialize_llm(entry: LLMEngineEntry) -> LLMEngineInfo:
    configs = entry.model_configs()
    return LLMEngineInfo(
        engine=entry.engine,
        display_name=entry.display_name,
        description=entry.description,
        models=[
            EngineModelInfo(
                model_name=cfg.model_name,
                display_name=cfg.display_name,
                model_size=cfg.model_size,
                size_mb=cfg.size_mb,
            )
            for cfg in configs
        ],
    )


def _provider_tts_engines() -> list[TTSEngineInfo]:
    """TTS engines for any upstream provider that has a key configured.

    Surfaced only when configured so the picker never shows an engine the user
    can't actually use (key removed → engine disappears). Voice mode is
    "preset" — provider voices are seeded as profiles, like Supertonic.
    """
    from ..services.providers import configured_providers

    infos: list[TTSEngineInfo] = []
    for prov in configured_providers():
        if not prov.tts_models:
            continue
        infos.append(
            TTSEngineInfo(
                engine=prov.key,
                display_name=prov.name,
                description=prov.description,
                voice_mode="preset",
                english_only=False,
                languages=list(prov.languages),
                models=[
                    EngineModelInfo(
                        model_name=mid,
                        display_name=label,
                        model_size="default",
                        size_mb=0,
                    )
                    for mid, label in prov.tts_models
                ],
            )
        )
    return infos


@router.get("/engines", response_model=EngineCatalogResponse)
async def list_engines() -> EngineCatalogResponse:
    """Return the full TTS + LLM engine catalog (local registry + configured providers)."""
    return EngineCatalogResponse(
        tts=[_serialize_tts(e) for e in _TTS_REGISTRY] + _provider_tts_engines(),
        llm=[_serialize_llm(e) for e in _LLM_REGISTRY],
    )
