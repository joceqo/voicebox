"""Tests for backend/services/tiers.py."""

import os
from unittest.mock import patch

from ..services.tiers import resolve_active_tiers, Tier, _RESAMPLE_TARGET


class TestResolveActiveTiersEnv:
    """VOICEBOX_ADAPTIVE_TIERS env override path."""

    def test_empty_env_uses_registry(self, monkeypatch):
        monkeypatch.delenv("VOICEBOX_ADAPTIVE_TIERS", raising=False)
        tiers = resolve_active_tiers(available_ram_mb=32_000)
        assert len(tiers) >= 1
        assert tiers[0].engine == "supertonic"
        assert tiers[0].index == 0

    def test_env_single_engine(self, monkeypatch):
        monkeypatch.setenv("VOICEBOX_ADAPTIVE_TIERS", "supertonic")
        tiers = resolve_active_tiers()
        assert len(tiers) == 1
        assert tiers[0].engine == "supertonic"
        assert tiers[0].index == 0
        assert tiers[0].sample_rate == 44100

    def test_env_two_engines(self, monkeypatch):
        monkeypatch.setenv("VOICEBOX_ADAPTIVE_TIERS", "supertonic,kyutai_pocket")
        tiers = resolve_active_tiers()
        assert len(tiers) == 2
        assert tiers[0].engine == "supertonic"
        assert tiers[1].engine == "kyutai_pocket"
        assert tiers[1].index == 1
        assert tiers[1].sample_rate == 24000

    def test_env_unknown_engine_skipped(self, monkeypatch, caplog):
        monkeypatch.setenv("VOICEBOX_ADAPTIVE_TIERS", "supertonic,nonexistent")
        tiers = resolve_active_tiers()
        assert len(tiers) == 1
        assert tiers[0].engine == "supertonic"

    def test_env_capped_at_max_tiers(self, monkeypatch):
        monkeypatch.setenv(
            "VOICEBOX_ADAPTIVE_TIERS",
            "supertonic,kyutai_pocket,kokoro,supertonic",
        )
        tiers = resolve_active_tiers()
        assert len(tiers) <= 3


class TestRAMBudgetFilter:
    """RAM-budget auto-selection path."""

    def test_ram_plenty_allows_one(self, monkeypatch):
        monkeypatch.delenv("VOICEBOX_ADAPTIVE_TIERS", raising=False)
        tiers = resolve_active_tiers(available_ram_mb=32_000)
        assert len(tiers) >= 1

    def test_ram_tight_only_tier0(self, monkeypatch):
        monkeypatch.delenv("VOICEBOX_ADAPTIVE_TIERS", raising=False)
        # 200 MB available → budget = 100 MB < supertonic size_mb (400).
        tiers = resolve_active_tiers(available_ram_mb=200)
        # Tier 0 is always admitted regardless of budget.
        assert len(tiers) == 1
        assert tiers[0].engine == "supertonic"

    def test_ram_enough_for_two(self, monkeypatch):
        monkeypatch.delenv("VOICEBOX_ADAPTIVE_TIERS", raising=False)
        # 2000 MB → budget = 1000 MB, can fit supertonic (400) + kyutai (400).
        tiers = resolve_active_tiers(available_ram_mb=2000)
        assert len(tiers) == 2
        assert tiers[0].engine == "supertonic"
        assert tiers[1].engine == "kyutai_pocket"

    def test_tier_fields(self, monkeypatch):
        monkeypatch.setenv("VOICEBOX_ADAPTIVE_TIERS", "supertonic")
        tiers = resolve_active_tiers()
        tier = tiers[0]
        assert tier.engine == "supertonic"
        assert tier.index == 0
        assert tier.sample_rate in (24000, 44100)
        assert tier.voice_mode == "preset"
