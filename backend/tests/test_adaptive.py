"""Tests for backend/services/adaptive.py — AdaptiveSession pipeline."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

# Prevent torch-dependent modules from being imported
_fake_profiles = SimpleNamespace()
_fake_profiles._get_preset_voice_ids = lambda eng: set()
_fake_profiles.validate_profile_engine = lambda p, e: None
_fake_profiles.create_voice_prompt_for_profile = AsyncMock()
sys.modules["backend.services.profiles"] = _fake_profiles

_fake_cache = SimpleNamespace()
_fake_cache._get_cache_dir = lambda *a, **kw: Path("/tmp")
_fake_cache.clear_profile_cache = lambda *a, **kw: None
sys.modules["backend.utils.cache"] = _fake_cache

from ..services.adaptive import (  # noqa: E402
    AdaptiveSession,
    Chunk,
    Tier,
    TierLevel,
    WAV_HEADER_24K_MONO_INT16,
    resolve_voice,
    MIN_REMAINING_FOR_SWAP_UP,
    sweep_adaptive_sessions,
    _get_session_dir,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_tiers():
    return [
        Tier(index=0, engine="supertonic", sample_rate=44100, voice_mode="preset"),
        Tier(index=1, engine="kyutai_pocket", sample_rate=24000, voice_mode="preset"),
    ]


class FakeBackend:
    async def generate(self, text, voice_prompt, language="en", seed=None, instruct=None):
        await asyncio.sleep(0.01)
        sr = 24000
        t = np.linspace(0, 0.3, int(sr * 0.3), endpoint=False)
        audio = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        return audio, sr

    def is_loaded(self):
        return True

    async def load_model(self, *args, **kwargs):
        pass


class FailingBackend:
    async def generate(self, *args, **kwargs):
        raise RuntimeError("simulated failure")

    def is_loaded(self):
        return True

    async def load_model(self, *args, **kwargs):
        pass


# ── resolve_voice tests ─────────────────────────────────────────────────


class TestResolveVoice:
    def test_openai_alloy(self, fake_tiers):
        result = resolve_voice("alloy", fake_tiers)
        assert result == {"supertonic": "F1", "kyutai_pocket": "anna"}

    def test_openai_echo(self, fake_tiers):
        result = resolve_voice("echo", fake_tiers)
        assert result == {"supertonic": "M1", "kyutai_pocket": "george"}

    def test_bare_voice_in_catalog(self, fake_tiers):
        sys.modules["backend.services.profiles"]._get_preset_voice_ids = (
            lambda eng: {"F1", "F2", "F3"} if eng == "supertonic" else {"anna", "george"}
        )
        result = resolve_voice("F1", fake_tiers)
        assert result["supertonic"] == "F1"

    def test_engine_colon_voice(self, fake_tiers):
        result = resolve_voice("supertonic:M1", fake_tiers)
        assert result == {"supertonic": "M1"}

    def test_unknown_voice_raises(self, fake_tiers):
        sys.modules["backend.services.profiles"]._get_preset_voice_ids = lambda eng: set()
        with pytest.raises(ValueError):
            resolve_voice("unknown_zzz", fake_tiers)


# ── WAV header tests ────────────────────────────────────────────────────


class TestWavHeader:
    def test_header_length(self):
        assert len(WAV_HEADER_24K_MONO_INT16) == 44

    def test_header_magic(self):
        assert WAV_HEADER_24K_MONO_INT16[0:4] == b"RIFF"
        assert WAV_HEADER_24K_MONO_INT16[8:12] == b"WAVE"


# ── AdaptiveSession tests ────────────────────────────────────────────────


class TestAdaptiveSession:
    @pytest.mark.asyncio
    async def test_single_chunk_single_tier(self, monkeypatch, tmp_path):
        tiers = [
            Tier(index=0, engine="supertonic", sample_rate=44100, voice_mode="preset"),
        ]
        monkeypatch.setattr(
            "backend.services.adaptive._get_session_dir",
            lambda: tmp_path / "adaptive_sessions",
        )
        monkeypatch.setattr(
            "backend.services.adaptive.resolve_voice",
            lambda name, tiers: {"supertonic": "M1"},
        )
        # Reset profiles mock to return M1
        sys.modules["backend.services.profiles"]._get_preset_voice_ids = (
            lambda eng: {"M1", "F1"}
        )

        with patch(
            "backend.backends.get_tts_backend_for_engine",
            return_value=FakeBackend(),
        ), patch(
            "backend.backends.load_engine_model",
            new_callable=AsyncMock,
        ):
            session = await AdaptiveSession.start(
                "Hello world.",
                "echo",
                tiers=tiers,
                max_chunk_chars=800,
            )
            try:
                assert session.n_chunks == 1
                tier = await session.wait_and_pick(0)
                assert tier == 0

                blocks = []
                async for b in session.read_pcm(0, 0):
                    blocks.append(b)
                assert len(blocks) > 0
                assert all(isinstance(b, bytes) for b in blocks)
            finally:
                await session.close()
                assert not (tmp_path / "adaptive_sessions" / session.session_id).exists()

    @pytest.mark.asyncio
    async def test_multi_chunk_multi_tier(self, monkeypatch, tmp_path):
        tiers = [
            Tier(index=0, engine="supertonic", sample_rate=44100, voice_mode="preset"),
            Tier(index=1, engine="kyutai_pocket", sample_rate=24000, voice_mode="preset"),
        ]
        monkeypatch.setattr(
            "backend.services.adaptive._get_session_dir",
            lambda: tmp_path / "adaptive_sessions",
        )
        monkeypatch.setattr(
            "backend.services.adaptive.resolve_voice",
            lambda name, tiers: {"supertonic": "M1", "kyutai_pocket": "george"},
        )

        text = (
            "Chunk one. Chunk two. Chunk three. Chunk four. "
            "Chunk five. Chunk six. Chunk seven. Chunk eight. "
            "Chunk nine. Chunk ten."
        )

        with patch(
            "backend.backends.get_tts_backend_for_engine",
            return_value=FakeBackend(),
        ), patch(
            "backend.backends.load_engine_model",
            new_callable=AsyncMock,
        ):
            session = await AdaptiveSession.start(
                text, "echo", tiers=tiers, max_chunk_chars=30,
            )
            try:
                assert session.n_chunks >= 5
                prev_tier = -1
                for i in range(session.n_chunks):
                    tier = await session.wait_and_pick(i)
                    assert tier >= prev_tier
                    prev_tier = tier
                    async for _ in session.read_pcm(i, tier):
                        pass
            finally:
                await session.close()
                assert not (tmp_path / "adaptive_sessions" / session.session_id).exists()

    @pytest.mark.asyncio
    async def test_tier_failure_fallback(self, monkeypatch, tmp_path):
        tiers = [
            Tier(index=0, engine="supertonic", sample_rate=44100, voice_mode="preset"),
            Tier(index=1, engine="kyutai_pocket", sample_rate=24000, voice_mode="preset"),
        ]
        monkeypatch.setattr(
            "backend.services.adaptive._get_session_dir",
            lambda: tmp_path / "adaptive_sessions",
        )
        monkeypatch.setattr(
            "backend.services.adaptive.resolve_voice",
            lambda name, tiers: {"supertonic": "M1", "kyutai_pocket": "george"},
        )

        class MixedBackend:
            async def generate(self, text, voice_prompt, language="en", seed=None, instruct=None):
                await asyncio.sleep(0.005)
                sr = 24000
                t = np.linspace(0, 0.2, int(sr * 0.2), endpoint=False)
                audio = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
                return audio, sr

            def is_loaded(self):
                return True

        with patch(
            "backend.backends.get_tts_backend_for_engine",
            side_effect=lambda eng: MixedBackend() if eng == "supertonic" else FailingBackend(),
        ), patch(
            "backend.backends.load_engine_model",
            new_callable=AsyncMock,
        ):
            session = await AdaptiveSession.start(
                "One. Two. Three. Four. Five. Six. Seven. Eight.",
                "echo",
                tiers=tiers,
                max_chunk_chars=12,
            )
            try:
                for i in range(session.n_chunks):
                    tier = await session.wait_and_pick(i)
                    assert tier == 0
                    async for _ in session.read_pcm(i, tier):
                        pass
            finally:
                await session.close()

    @pytest.mark.asyncio
    async def test_tail_guard_no_swap_up(self, monkeypatch, tmp_path):
        tiers = [
            Tier(index=0, engine="supertonic", sample_rate=44100, voice_mode="preset"),
            Tier(index=1, engine="kyutai_pocket", sample_rate=24000, voice_mode="preset"),
        ]
        monkeypatch.setattr(
            "backend.services.adaptive._get_session_dir",
            lambda: tmp_path / "adaptive_sessions",
        )
        monkeypatch.setattr(
            "backend.services.adaptive.resolve_voice",
            lambda name, tiers: {"supertonic": "M1", "kyutai_pocket": "george"},
        )

        with patch(
            "backend.backends.get_tts_backend_for_engine",
            return_value=FakeBackend(),
        ), patch(
            "backend.backends.load_engine_model",
            new_callable=AsyncMock,
        ):
            text = ".".join(f"Chunk {i + 1}" for i in range(12))
            session = await AdaptiveSession.start(
                text, "echo", tiers=tiers, max_chunk_chars=15,
            )
            try:
                await asyncio.sleep(0.5)
                assert session.n_chunks >= 7
                tiers_seen: list[int] = []
                for i in range(session.n_chunks):
                    t = await session.wait_and_pick(i)
                    tiers_seen.append(t)
                    async for _ in session.read_pcm(i, t):
                        pass

                tail_start = max(0, session.n_chunks - MIN_REMAINING_FOR_SWAP_UP)
                if tail_start > 0:
                    pre_tail_max = max(tiers_seen[:tail_start])
                    for t in tiers_seen[tail_start:]:
                        assert t <= pre_tail_max + 1
            finally:
                await session.close()


# ── Session cleanup tests ───────────────────────────────────────────────


class TestSessionCleanup:
    def test_sweep_removes_orphan_dirs(self, tmp_path, monkeypatch):
        root = tmp_path / "adaptive_sessions"
        root.mkdir(parents=True)
        (root / "stale1").mkdir()
        (root / "stale2").mkdir()
        (root / "stale_file.txt").write_text("keep")

        monkeypatch.setattr(
            "backend.services.adaptive._get_session_dir",
            lambda: root,
        )
        sweep_adaptive_sessions()

        assert not (root / "stale1").exists()
        assert not (root / "stale2").exists()
        assert (root / "stale_file.txt").exists()
