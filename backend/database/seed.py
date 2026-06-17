"""Post-migration data seeding and backfills."""

import json
import logging
import uuid

from .. import config

logger = logging.getLogger(__name__)


def backfill_generation_versions(SessionLocal, Generation, GenerationVersion) -> None:
    """Create 'clean' version entries for generations that predate the versions feature."""
    db = SessionLocal()
    try:
        existing_version_gen_ids = {
            row[0] for row in db.query(GenerationVersion.generation_id).all()
        }
        generations = db.query(Generation).filter(
            Generation.status == "completed",
            Generation.audio_path.isnot(None),
            Generation.audio_path != "",
        ).all()

        count = 0
        for gen in generations:
            if gen.id in existing_version_gen_ids:
                continue
            resolved_audio_path = config.resolve_storage_path(gen.audio_path)
            if resolved_audio_path is None or not resolved_audio_path.exists():
                continue
            version = GenerationVersion(
                id=str(uuid.uuid4()),
                generation_id=gen.id,
                label="clean",
                audio_path=gen.audio_path,
                effects_chain=None,
                is_default=True,
            )
            db.add(version)
            count += 1

        if count > 0:
            db.commit()
            logger.info("Backfilled %d generation version entries", count)
    finally:
        db.close()


def seed_preset_voices(SessionLocal, VoiceProfile) -> None:
    """Ensure the built-in Supertonic preset voices exist as selectable profiles.

    Supertonic is preset-only and ships 10 voices (M1–M5, F1–F5). The main
    TTS screen lists DB profiles, so without these the default engine would
    show no compatible voice out of the box. We materialize each preset voice
    as a locked preset profile. Idempotent: keyed on ``preset_voice_id`` so it
    only creates what's missing and never duplicates on subsequent startups.
    """
    from ..backends.supertonic_backend import SUPERTONIC_VOICES

    db = SessionLocal()
    try:
        existing = {
            row[0]
            for row in db.query(VoiceProfile.preset_voice_id)
            .filter(
                VoiceProfile.voice_type == "preset",
                VoiceProfile.preset_engine == "supertonic",
            )
            .all()
        }
        count = 0
        for vid, name, _gender, _lang in SUPERTONIC_VOICES:
            if vid in existing:
                continue
            db.add(
                VoiceProfile(
                    id=str(uuid.uuid4()),
                    name=f"{name} (Supertonic)",
                    description="Built-in Supertonic preset voice.",
                    language="en",
                    voice_type="preset",
                    preset_engine="supertonic",
                    preset_voice_id=vid,
                    default_engine="supertonic",
                )
            )
            count += 1
        if count > 0:
            db.commit()
            logger.info("Seeded %d Supertonic preset voice profiles", count)
    finally:
        db.close()


def seed_builtin_presets(SessionLocal, EffectPreset) -> None:
    """Ensure built-in effect presets exist in the database."""
    from ..utils.effects import BUILTIN_PRESETS

    db = SessionLocal()
    try:
        for idx, (_key, preset_data) in enumerate(BUILTIN_PRESETS.items()):
            sort_order = preset_data.get("sort_order", idx)
            existing = db.query(EffectPreset).filter_by(name=preset_data["name"]).first()
            if not existing:
                preset = EffectPreset(
                    id=str(uuid.uuid4()),
                    name=preset_data["name"],
                    description=preset_data.get("description"),
                    effects_chain=json.dumps(preset_data["effects_chain"]),
                    is_builtin=True,
                    sort_order=sort_order,
                )
                db.add(preset)
            elif existing.sort_order != sort_order:
                existing.sort_order = sort_order
        db.commit()
    finally:
        db.close()
