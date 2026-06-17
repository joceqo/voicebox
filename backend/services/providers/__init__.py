"""Provider registry + credential storage.

Maps the ``<provider>/<model>`` namespace used in OpenAI-compatible requests to
a concrete :class:`VoiceProvider`, and reads/writes provider API keys in the
``provider_credentials`` table.
"""

from __future__ import annotations

from .base import VoiceProvider
from .mistral import MistralProvider

# Registry of available providers, keyed by their namespace prefix.
_PROVIDERS: dict[str, VoiceProvider] = {p.key: p for p in (MistralProvider(),)}


def list_providers() -> list[VoiceProvider]:
    return list(_PROVIDERS.values())


def get_provider(name: str) -> VoiceProvider | None:
    return _PROVIDERS.get(name)


def split_provider_model(model: str) -> tuple[VoiceProvider, str] | None:
    """Resolve a ``"<provider>/<upstream_model>"`` string.

    Returns ``(provider, upstream_model_id)`` when the prefix names a known
    provider, else None (so the caller falls through to local engines). The
    upstream model id is passed to the provider verbatim, so callers can target
    any model the provider accepts — not only those in its declared catalog.
    """
    if "/" not in model:
        return None
    prefix, _, rest = model.partition("/")
    provider = _PROVIDERS.get(prefix)
    if not provider or not rest:
        return None
    return provider, rest


# ── Credential storage (provider_credentials table) ──────────────────────────


def _mask_key(api_key: str) -> str:
    """Mask a key for display: keep a short tail, hide the rest."""
    if len(api_key) <= 4:
        return "…"
    return f"…{api_key[-4:]}"


def get_api_key(provider: str) -> str | None:
    """Read the stored key for *provider* (opens its own short-lived session)."""
    from ...database.models import ProviderCredential
    from ...database.session import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(ProviderCredential, provider)
        return row.api_key if row else None
    finally:
        db.close()


def set_api_key(db, provider: str, api_key: str) -> None:
    """Upsert the key for *provider*."""
    from ...database.models import ProviderCredential

    row = db.get(ProviderCredential, provider)
    if row:
        row.api_key = api_key
    else:
        db.add(ProviderCredential(provider=provider, api_key=api_key))
    db.commit()


def delete_api_key(db, provider: str) -> bool:
    """Delete the key for *provider*. Returns True if a row was removed."""
    from ...database.models import ProviderCredential

    row = db.get(ProviderCredential, provider)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def get_masked_key(db, provider: str) -> str | None:
    from ...database.models import ProviderCredential

    row = db.get(ProviderCredential, provider)
    return _mask_key(row.api_key) if row else None


def configured_providers() -> list[VoiceProvider]:
    """Providers that currently have a stored key (sync; opens a session)."""
    from ...database.models import ProviderCredential
    from ...database.session import SessionLocal

    if SessionLocal is None:
        return []
    db = SessionLocal()
    try:
        keyed = {row.provider for row in db.query(ProviderCredential.provider).all()}
    finally:
        db.close()
    return [p for p in _PROVIDERS.values() if p.key in keyed]


# ── Preset-voice cache + seeding ─────────────────────────────────────────────
# Provider voices are fetched once (on key-set / startup) and cached so the
# sync hot paths (_get_preset_voice_ids, generation validation) don't do network
# I/O. The cache is self-healing: a cold read derives voices from the seeded
# VoiceProfile rows, which are the persistent source of truth.

_VOICES_CACHE: dict[str, list[dict]] = {}


async def refresh_voices(provider: str, api_key: str) -> list[dict]:
    """Fetch the provider's voices from upstream and refresh the cache."""
    impl = get_provider(provider)
    if not impl:
        return []
    voices = await impl.list_voices(api_key)
    _VOICES_CACHE[provider] = voices
    return voices


def _voices_from_seeded_profiles(provider: str) -> list[dict]:
    from ...database.models import VoiceProfile
    from ...database.session import SessionLocal

    if SessionLocal is None:
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(VoiceProfile)
            .filter(
                VoiceProfile.voice_type == "preset",
                VoiceProfile.preset_engine == provider,
            )
            .all()
        )
        return [
            {
                "voice_id": r.preset_voice_id,
                "name": r.name,
                "gender": "unknown",
                "language": r.language or "en",
            }
            for r in rows
            if r.preset_voice_id
        ]
    finally:
        db.close()


def get_cached_voices(provider: str) -> list[dict]:
    """Cached provider voices; falls back to seeded DB profiles when cold."""
    if provider in _VOICES_CACHE:
        return _VOICES_CACHE[provider]
    voices = _voices_from_seeded_profiles(provider)
    if voices:
        _VOICES_CACHE[provider] = voices
    return voices


def seed_provider_profiles(db, provider: str, voices: list[dict]) -> int:
    """Upsert the provider's voices as selectable preset VoiceProfile rows.

    Idempotent (keyed on preset_voice_id). Mirrors the Supertonic seeding so
    provider voices appear in the normal voice picker. Returns count added.
    """
    import uuid

    from ...database.models import VoiceProfile

    impl = get_provider(provider)
    label = impl.name if impl else provider
    existing = {
        row[0]
        for row in db.query(VoiceProfile.preset_voice_id)
        .filter(
            VoiceProfile.voice_type == "preset",
            VoiceProfile.preset_engine == provider,
        )
        .all()
    }
    added = 0
    for v in voices:
        vid = v.get("voice_id")
        if not vid or vid in existing:
            continue
        db.add(
            VoiceProfile(
                id=str(uuid.uuid4()),
                name=f"{v.get('name', vid)} ({label})",
                description=f"Preset voice proxied via {label}.",
                language=v.get("language") or "en",
                voice_type="preset",
                preset_engine=provider,
                preset_voice_id=vid,
                default_engine=provider,
            )
        )
        added += 1
    if added:
        db.commit()
    return added


def remove_provider_profiles(db, provider: str) -> int:
    """Delete the provider's seeded preset profiles. Returns count removed."""
    from ...database.models import VoiceProfile

    rows = (
        db.query(VoiceProfile)
        .filter(
            VoiceProfile.voice_type == "preset",
            VoiceProfile.preset_engine == provider,
        )
        .all()
    )
    for r in rows:
        db.delete(r)
    if rows:
        db.commit()
    _VOICES_CACHE.pop(provider, None)
    return len(rows)
