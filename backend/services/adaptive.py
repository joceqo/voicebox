"""Adaptive streaming orchestration.

Implements the multi-tier pipeline from the machia-reader pattern:
split text into chunks, generate the fastest tier in forward order to
drive playback immediately, generate higher tiers in reverse order in
parallel, and swap up to the best ready tier per chunk — with stickiness
and a tail-guard so the listener never hears a tier swap for the last
few chunks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import AsyncIterator

import librosa
import numpy as np
import soundfile as sf

from .tiers import Tier, resolve_active_tiers, _RESAMPLE_TARGET

logger = logging.getLogger(__name__)

# Minimum number of chunks remaining before we refuse to swap *up* to a
# higher tier. This prevents jarring late-stream transitions.
MIN_REMAINING_FOR_SWAP_UP = 5

# PCM block size for streaming yields (64 KiB).
PCM_BLOCK_SIZE = 64 * 1024


class TierLevel(IntEnum):
    """Semantic names for logical tier indices."""

    FAST = 0
    MID = 1
    HQ = 2


# ── Voice mapping: OpenAI / user-facing name → per-engine preset voice id ──

OPENAI_VOICE_MAP: dict[str, dict[str, str]] = {
    "alloy":   {"supertonic": "F1", "kyutai_pocket": "anna"},
    "echo":    {"supertonic": "M1", "kyutai_pocket": "george"},
    "fable":   {"supertonic": "M2", "kyutai_pocket": "marius"},
    "onyx":    {"supertonic": "M3", "kyutai_pocket": "javert"},
    "nova":    {"supertonic": "F2", "kyutai_pocket": "cosette"},
    "shimmer": {"supertonic": "F3", "kyutai_pocket": "alba"},
}


# ── WAV streaming header (24 kHz mono 16-bit, data_size = 0xFFFFFFFF) ────

WAV_HEADER_24K_MONO_INT16 = bytes(
    [
        # RIFF header
        0x52, 0x49, 0x46, 0x46,  # "RIFF"
        0xFF, 0xFF, 0xFF, 0x7F,  # chunk size (max signed int32 → open-ended)
        0x57, 0x41, 0x56, 0x45,  # "WAVE"
        # fmt  subchunk
        0x66, 0x6D, 0x74, 0x20,  # "fmt "
        0x10, 0x00, 0x00, 0x00,  # subchunk size = 16
        0x01, 0x00,              # audio format = PCM
        0x01, 0x00,              # num channels = 1
        0xC0, 0x5D, 0x00, 0x00,  # sample rate = 24000
        0x00, 0xBB, 0x00, 0x00,  # byte rate = 24000 * 2 = 48000
        0x02, 0x00,              # block align = 2
        0x10, 0x00,              # bits per sample = 16
        # data subchunk
        0x64, 0x61, 0x74, 0x61,  # "data"
        0xFF, 0xFF, 0xFF, 0x7F,  # data size (max signed int32 → open-ended)
    ]
)


def resolve_voice(voice_name: str, tiers: list[Tier]) -> dict[str, str]:
    """Resolve a user-facing voice name to a per-engine preset voice-id map.

    Returns a dict of ``{engine: voice_id}`` or raises ``ValueError``.
    """
    if voice_name in OPENAI_VOICE_MAP:
        base = OPENAI_VOICE_MAP[voice_name]
        result: dict[str, str] = {}
        for tier in tiers:
            if tier.engine in base:
                result[tier.engine] = base[tier.engine]
        missing = {t.engine for t in tiers} - set(result)
        if missing:
            raise ValueError(
                f"Voice '{voice_name}' is not mapped for engine(s): {', '.join(sorted(missing))}"
            )
        return result

    # Voicebox-native: caller passes "engine:voice_id" or "engine"
    if ":" in voice_name:
        engine, vid = voice_name.split(":", 1)
        for tier in tiers:
            if tier.engine == engine:
                return {tier.engine: vid}
        raise ValueError(f"Engine '{engine}' is not an active tier")

    # Bare voice_id — try to match across all tiers.
    from .profiles import _get_preset_voice_ids

    result = {}
    for tier in tiers:
        valid = _get_preset_voice_ids(tier.engine)
        if voice_name in valid:
            result[tier.engine] = voice_name
        elif valid:
            # Fallback: pick first available voice
            result[tier.engine] = next(iter(valid))
    if not result:
        raise ValueError(
            f"Voice '{voice_name}' not found in any active tier engine"
        )
    return result


def _get_session_dir() -> Path:
    """Return the root directory for adaptive session temp files."""
    from .. import config
    return config.get_data_dir() / "adaptive_sessions"


# ── Core data structures ────────────────────────────────────────────────


@dataclass
class Chunk:
    text: str
    ready: list[bool] = field(default_factory=list)
    paths: list[Path | None] = field(default_factory=list)


@dataclass
class AdaptiveSession:
    """Per-request state for one adaptive streaming generation."""

    session_id: str
    tiers: list[Tier]
    voice_map: dict[str, str]  # engine → voice_id
    language: str
    chunks: list[Chunk]
    session_dir: Path

    # Internal concurrency state
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _ready_events: list[list[asyncio.Event]] = field(default_factory=list)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _sticky_tier: int = 0

    @classmethod
    async def start(
        cls,
        text: str,
        voice_name: str,
        language: str = "en",
        *,
        tiers: list[Tier] | None = None,
        max_chunk_chars: int = 800,
    ) -> "AdaptiveSession":
        if tiers is None:
            tiers = resolve_active_tiers()
        if not tiers:
            raise RuntimeError("No adaptive tiers available")

        voice_map = resolve_voice(voice_name, tiers)

        from ..utils.chunked_tts import split_text_into_chunks

        chunk_texts = split_text_into_chunks(text, max_chunk_chars)
        if not chunk_texts:
            raise ValueError("Empty text input")

        session_id = uuid.uuid4().hex[:12]
        session_dir = _get_session_dir() / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        n_tiers = len(tiers)
        n_chunks = len(chunk_texts)

        chunks = [
            Chunk(
                text=ct,
                ready=[False] * n_tiers,
                paths=[None] * n_tiers,
            )
            for ct in chunk_texts
        ]

        ready_events = [
            [asyncio.Event() for _ in range(n_tiers)]
            for _ in range(n_chunks)
        ]

        session = cls(
            session_id=session_id,
            tiers=tiers,
            voice_map=voice_map,
            language=language,
            chunks=chunks,
            session_dir=session_dir,
            _ready_events=ready_events,
        )

        await session._spawn_tasks()
        return session

    async def _spawn_tasks(self) -> None:
        # Tier 0 (fastest): forward order, drives the stream.
        t0 = asyncio.create_task(
            self._generate_tier(0, list(range(len(self.chunks))))
        )
        self._tasks.append(t0)

        # Tiers ≥ 1: reverse order, so playback starts before they finish.
        for k in range(1, len(self.tiers)):
            tk = asyncio.create_task(
                self._generate_tier(k, list(range(len(self.chunks) - 1, -1, -1)))
            )
            self._tasks.append(tk)

    async def _generate_tier(
        self,
        tier_idx: int,
        order: list[int],
    ) -> None:
        tier = self.tiers[tier_idx]
        voice_id = self.voice_map.get(tier.engine)
        if voice_id is None:
            logger.error("Tier %d engine '%s' has no mapped voice", tier_idx, tier.engine)
            return

        voice_prompt = {
            "voice_type": "preset",
            "preset_engine": tier.engine,
            "preset_voice_id": voice_id,
        }

        try:
            from ..backends import (
                get_tts_backend_for_engine,
                load_engine_model,
            )

            backend = get_tts_backend_for_engine(tier.engine)
            await load_engine_model(tier.engine)
        except Exception as exc:
            logger.warning(
                "Tier %d (%s): failed to load model, tier disabled: %s",
                tier_idx,
                tier.engine,
                exc,
            )
            return

        for i in order:
            chunk = self.chunks[i]
            path = self.session_dir / f"c{i}_t{tier_idx}.wav"

            try:
                audio, sample_rate = await backend.generate(
                    chunk.text,
                    voice_prompt,
                    self.language,
                )

                # Resample to 24 kHz if needed.
                if sample_rate != _RESAMPLE_TARGET:
                    audio = librosa.resample(
                        np.asarray(audio, dtype=np.float32),
                        orig_sr=sample_rate,
                        target_sr=_RESAMPLE_TARGET,
                    )

                # Apply optional normalization.
                from ..utils.audio import normalize_audio

                audio = normalize_audio(audio)
                sf.write(str(path), audio, _RESAMPLE_TARGET, format="WAV")

                async with self._lock:
                    chunk.ready[tier_idx] = True
                    chunk.paths[tier_idx] = path

                self._ready_events[i][tier_idx].set()

            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(
                    "Tier %d (%s): chunk %d generation failed",
                    tier_idx,
                    tier.engine,
                    i,
                )
                # Leave ready=False, paths=None — fallback transparent.

    async def wait_and_pick(self, i: int) -> int:
        """Wait until at least the sticky-floor tier has chunk i ready,
        then pick the best available tier and return its index.

        Implements:
        - Sticky downgrade floor (never drop below previously served tier).
        - Tail guard (no swap *up* when < MIN_REMAINING_FOR_SWAP_UP remain).
        """
        # Wait for at least the floor-tier to be ready.
        floor = max(0, self._sticky_tier)
        await self._wait_chunk_ready(i, floor)

        remaining = len(self.chunks) - i
        ceiling = (
            self._sticky_tier
            if remaining < MIN_REMAINING_FOR_SWAP_UP
            else len(self.tiers) - 1
        )

        # Pick highest ready tier in [floor, ceiling].
        best = floor
        async with self._lock:
            for k in range(ceiling, floor - 1, -1):
                if self.chunks[i].ready[k]:
                    best = k
                    break

        self._sticky_tier = max(self._sticky_tier, best)
        return best

    async def _wait_chunk_ready(self, i: int, tier_idx: int) -> None:
        """Block until chunk i is ready at tier_idx."""
        event = self._ready_events[i][tier_idx]
        # Fast path: already ready.
        async with self._lock:
            if self.chunks[i].ready[tier_idx]:
                return
        await event.wait()

    async def read_pcm(self, i: int, tier_idx: int) -> AsyncIterator[bytes]:
        """Yield PCM int16 blocks for chunk i, tier tier_idx."""
        path: Path | None = None
        async with self._lock:
            path = self.chunks[i].paths[tier_idx]

        if path is None:
            logger.warning("Chunk %d tier %d has no path", i, tier_idx)
            return

        try:
            audio, sr = sf.read(str(path), dtype="float32")
            if sr != _RESAMPLE_TARGET:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=_RESAMPLE_TARGET)

            # float32 → int16
            int16_data = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            pcm_bytes = int16_data.tobytes()

            for offset in range(0, len(pcm_bytes), PCM_BLOCK_SIZE):
                yield pcm_bytes[offset : offset + PCM_BLOCK_SIZE]
        except Exception:
            logger.exception("Failed to read PCM for chunk %d tier %d", i, tier_idx)

    async def close(self) -> None:
        """Cancel background tasks and remove session directory."""
        for t in self._tasks:
            if not t.done():
                t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        try:
            if self.session_dir.exists():
                shutil.rmtree(self.session_dir, ignore_errors=True)
        except Exception:
            logger.exception("Failed to clean session dir %s", self.session_dir)

    @property
    def n_chunks(self) -> int:
        return len(self.chunks)


def sweep_adaptive_sessions() -> None:
    """Remove orphaned session directories from prior crashed runs."""
    session_root = _get_session_dir()
    if not session_root.exists():
        return
    for entry in session_root.iterdir():
        if entry.is_dir():
            try:
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("Swept stale session: %s", entry.name)
            except Exception:
                logger.exception("Failed to sweep %s", entry)
