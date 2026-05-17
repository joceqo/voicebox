"""Tier resolver for the adaptive streaming pipeline.

Derives an ordered list of :class:`Tier` from the engine registry,
filtered by available RAM budget and an optional env override.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]

from ..backends import _TTS_REGISTRY

logger = logging.getLogger(__name__)

MAX_TIERS = 3
"""Hard cap — the pipeline can handle more but we cap for CPU contention."""

DEFAULT_RAM_BUDGET_FRACTION = 0.5
"""Fraction of available RAM we are willing to consume across all tiers."""


@dataclass(frozen=True)
class Tier:
    """An engine mapped to an adaptive-priority slot.

    ``index`` is the tier rank (0 = fastest, drives the stream).
    ``sample_rate`` is the engine's native output rate — caller must
    resample to 24 kHz for uniform streaming.
    """

    index: int
    engine: str
    sample_rate: int
    voice_mode: str  # "preset" or "cloning"


def _get_native_sample_rate(engine: str) -> int:
    """Return the engine's native sample rate.

    Heuristic derived from backend implementations; avoids importing
    backends at module level.
    """
    if engine in ("kokoro", "kyutai_pocket"):
        return 24000
    if engine == "supertonic":
        return 44100
    # Default for most engines (qwen, luxtts, chatterbox, tada):
    return 24000


_RESAMPLE_TARGET = 24000


def resolve_active_tiers(
    *,
    available_ram_mb: int | None = None,
) -> list[Tier]:
    """Return the ordered list of tiers to use in the adaptive pipeline.

    1. If ``VOICEBOX_ADAPTIVE_TIERS`` is set, use that comma-separated
       engine list — order = tier index.
    2. Else: take all entries in ``_TTS_REGISTRY`` with
       ``adaptive_priority`` not None, sort by priority asc, then
       greedily admit until cumulative ``adaptive_size_mb`` exceeds
       ``available_ram_mb * DEFAULT_RAM_BUDGET_FRACTION``. Always
       admit tier 0.
    3. Cap at ``MAX_TIERS`` regardless.
    """

    env_tiers = os.environ.get("VOICEBOX_ADAPTIVE_TIERS", "").strip()
    if env_tiers:
        engine_names = [e.strip() for e in env_tiers.split(",") if e.strip()]
        result: list[Tier] = []
        for idx, name in enumerate(engine_names):
            entry = _find_entry(name)
            if entry is None:
                logger.warning(
                    "VOICEBOX_ADAPTIVE_TIERS lists unknown engine '%s', skipping",
                    name,
                )
                continue
            result.append(
                Tier(
                    index=idx,
                    engine=entry.engine,
                    sample_rate=_get_native_sample_rate(entry.engine),
                    voice_mode=entry.voice_mode,
                )
            )
        if result and len(result) > MAX_TIERS:
            logger.warning(
                "VOICEBOX_ADAPTIVE_TIERS specifies %d engines, capping at %d",
                len(result),
                MAX_TIERS,
            )
            result = result[:MAX_TIERS]
        return result

    # Auto-selection via RAM budget.
    candidates = sorted(
        (
            e
            for e in _TTS_REGISTRY
            if e.adaptive_priority is not None
        ),
        key=lambda e: e.adaptive_priority,  # type: ignore[arg-type,return-value]
    )
    if not candidates:
        logger.warning("No adaptive-eligible engines in registry")
        return []

    if available_ram_mb is None:
        available_ram_mb = _get_available_ram_mb()
    budget_mb = int(available_ram_mb * DEFAULT_RAM_BUDGET_FRACTION)

    admitted: list[Tier] = []
    cumulative_mb = 0

    for entry in candidates:
        # Always admit tier 0 (the fastest).
        if not admitted:
            admitted.append(
                Tier(
                    index=0,
                    engine=entry.engine,
                    sample_rate=_get_native_sample_rate(entry.engine),
                    voice_mode=entry.voice_mode,
                )
            )
            cumulative_mb += entry.adaptive_size_mb
            continue

        cumulative_mb += entry.adaptive_size_mb
        if cumulative_mb > budget_mb:
            logger.info(
                "RAM budget (%d MB) exhausted after %d tiers; skipping '%s' "
                "(needs %d MB, cum=%d MB)",
                budget_mb,
                len(admitted),
                entry.engine,
                entry.adaptive_size_mb,
                cumulative_mb,
            )
            break

        admitted.append(
            Tier(
                index=len(admitted),
                engine=entry.engine,
                sample_rate=_get_native_sample_rate(entry.engine),
                voice_mode=entry.voice_mode,
            )
        )

        if len(admitted) >= MAX_TIERS:
            break

    return admitted


def _find_entry(engine_name: str):
    """Look up a registry entry by engine name."""
    for e in _TTS_REGISTRY:
        if e.engine == engine_name:
            return e
    return None


def _get_available_ram_mb() -> int:
    """Return available system RAM in MB, or a safe default."""
    if _psutil is not None:
        try:
            return _psutil.virtual_memory().available // (1024 * 1024)
        except Exception:
            pass
    # Fallback: assume 8 GB — most machines have more, but we're conservative.
    logger.warning("psutil unavailable or failed; assuming 8192 MB available RAM")
    return 8192
