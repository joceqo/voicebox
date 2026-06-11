"""Upstream voice-provider credential management.

Lets the UI store/validate/remove API keys for frontier providers (Mistral,
…). Once a key is stored, the OpenAI-compatible ``/v1/audio/*`` endpoints proxy
requests for that provider's models (e.g. ``mistral/voxtral-mini-tts-2603``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import providers as providers_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/providers", tags=["providers"])


def _status_for(provider, db: Session) -> models.ProviderStatus:
    """Build the catalog + configured status for a single provider."""
    model_infos = [
        models.ProviderModelInfo(id=f"{provider.key}/{mid}", kind="tts", label=label)
        for mid, label in provider.tts_models
    ] + [
        models.ProviderModelInfo(id=f"{provider.key}/{mid}", kind="stt", label=label)
        for mid, label in provider.stt_models
    ]
    masked = providers_svc.get_masked_key(db, provider.key)
    return models.ProviderStatus(
        provider=provider.key,
        name=provider.name,
        configured=masked is not None,
        masked_key=masked,
        models=model_infos,
    )


@router.get("", response_model=models.ProviderListResponse)
async def list_providers(db: Session = Depends(get_db)):
    """List all providers, their catalogs, and whether a key is stored."""
    return models.ProviderListResponse(
        providers=[_status_for(p, db) for p in providers_svc.list_providers()]
    )


@router.put("/{provider}/key", response_model=models.ProviderValidateResponse)
async def set_provider_key(
    provider: str,
    body: models.ProviderKeyUpsert,
    db: Session = Depends(get_db),
):
    """Validate, then store, a provider's API key.

    The key is checked against the upstream API first; an invalid key is
    rejected (400) and never stored, so the UI can show a clear error.
    """
    impl = providers_svc.get_provider(provider)
    if not impl:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")

    valid, detail = await impl.validate(body.api_key)
    if not valid:
        raise HTTPException(status_code=400, detail=detail or "Invalid API key")

    providers_svc.set_api_key(db, provider, body.api_key)

    # Surface the provider's voices in the app: fetch them and seed selectable
    # preset profiles (like Supertonic). Best-effort — a voices-endpoint hiccup
    # shouldn't fail the key save; the cache self-heals on next access.
    try:
        voices = await providers_svc.refresh_voices(provider, body.api_key)
        seeded = providers_svc.seed_provider_profiles(db, provider, voices)
        logger.info("Seeded %d %s voice profiles", seeded, provider)
    except Exception:
        logger.warning("Could not seed voices for provider %s", provider, exc_info=True)

    return models.ProviderValidateResponse(valid=True, detail=detail)


@router.post("/{provider}/validate", response_model=models.ProviderValidateResponse)
async def validate_provider_key(
    provider: str,
    body: models.ProviderKeyUpsert,
):
    """Check a key without storing it (lets the UI test before saving)."""
    impl = providers_svc.get_provider(provider)
    if not impl:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    valid, detail = await impl.validate(body.api_key)
    return models.ProviderValidateResponse(valid=valid, detail=detail)


@router.post("/{provider}/test", response_model=models.ProviderValidateResponse)
async def test_provider_generation(provider: str):
    """Run a real generation with the stored key (auth + credit + pipeline).

    Unlike ``/validate`` (auth only), this actually generates a tiny clip so a
    valid-but-out-of-credit key is caught. Uses any default voice — the point
    is to confirm generation works, not which voice."""
    impl = providers_svc.get_provider(provider)
    if not impl:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    api_key = providers_svc.get_api_key(provider)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key stored for '{provider}'. Save one first.",
        )
    ok, detail = await impl.test_generation(api_key)
    return models.ProviderValidateResponse(valid=ok, detail=detail)


@router.delete("/{provider}/key")
async def delete_provider_key(provider: str, db: Session = Depends(get_db)):
    """Remove a provider's stored key."""
    impl = providers_svc.get_provider(provider)
    if not impl:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    providers_svc.delete_api_key(db, provider)
    # Remove the seeded voice profiles + cache so the engine/voices disappear
    # from the UI once the key is gone.
    removed = providers_svc.remove_provider_profiles(db, provider)
    logger.info("Removed %d %s voice profiles", removed, provider)
    return {"ok": True}
