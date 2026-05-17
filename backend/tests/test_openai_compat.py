"""Tests for backend/routes/openai_compat.py."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Prevent torch-dependent modules from being imported
sys.modules.setdefault("backend.services.profiles", SimpleNamespace(
    _get_preset_voice_ids=lambda eng: set(),
    validate_profile_engine=lambda p, e: None,
    create_voice_prompt_for_profile=AsyncMock(),
))
sys.modules.setdefault("backend.utils.cache", SimpleNamespace(
    _get_cache_dir=lambda *a, **kw: None,
    clear_profile_cache=lambda *a, **kw: None,
))

from ..routes.openai_compat import router  # noqa: E402
from ..services.adaptive import WAV_HEADER_24K_MONO_INT16  # noqa: E402


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestOpenAISpeechEndpoint:
    def test_missing_input_returns_422(self, client):
        resp = client.post("/v1/audio/speech", json={"model": "tts-1", "voice": "alloy"})
        assert resp.status_code == 422

    def test_unsupported_format_returns_400(self, client):
        resp = client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": "hello",
                "voice": "alloy",
                "response_format": "mp3",
            },
        )
        assert resp.status_code == 400
        assert "mp3" in resp.json()["detail"]

    def test_empty_input_returns_422(self, client):
        resp = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1", "input": "", "voice": "alloy"},
        )
        assert resp.status_code == 422

    def test_input_too_long_returns_422(self, client):
        resp = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1", "input": "x" * 50001, "voice": "alloy"},
        )
        assert resp.status_code == 422

    def test_valid_request_streams_wav(self, client, monkeypatch):
        with patch(
            "backend.routes.openai_compat.resolve_active_tiers",
            return_value=[
                SimpleNamespace(
                    index=0, engine="supertonic",
                    sample_rate=44100, voice_mode="preset",
                ),
            ],
        ), patch(
            "backend.backends.get_tts_backend_for_engine",
            return_value=SimpleNamespace(
                generate=AsyncMock(return_value=(
                    __import__("numpy").zeros(100, dtype="float32"), 24000,
                )),
                is_loaded=lambda: True,
            ),
        ), patch(
            "backend.backends.load_engine_model",
            new_callable=AsyncMock,
        ), patch(
            "backend.services.profiles._get_preset_voice_ids",
            return_value={"M1", "F1"},
        ):
            resp = client.post(
                "/v1/audio/speech",
                json={"model": "tts-1", "input": "Hello world.", "voice": "alloy"},
            )
            assert resp.status_code == 200
            assert len(resp.content) > 0

    def test_valid_request_streams_pcm(self, client):
        with patch(
            "backend.routes.openai_compat.resolve_active_tiers",
            return_value=[
                SimpleNamespace(
                    index=0, engine="supertonic",
                    sample_rate=44100, voice_mode="preset",
                ),
            ],
        ), patch(
            "backend.backends.get_tts_backend_for_engine",
            return_value=SimpleNamespace(
                generate=AsyncMock(return_value=(
                    __import__("numpy").zeros(100, dtype="float32"), 24000,
                )),
                is_loaded=lambda: True,
            ),
        ), patch(
            "backend.backends.load_engine_model",
            new_callable=AsyncMock,
        ), patch(
            "backend.services.profiles._get_preset_voice_ids",
            return_value={"M1", "F1"},
        ):
            resp = client.post(
                "/v1/audio/speech",
                json={
                    "model": "tts-1",
                    "input": "Hello world.",
                    "voice": "alloy",
                    "response_format": "pcm",
                },
            )
            assert resp.status_code == 200


class TestSingleEngineFallback:
    def test_voicebox_supertonic_model(self, client):
        with patch(
            "backend.services.profiles._get_preset_voice_ids",
            return_value={"M1", "F1"},
        ), patch(
            "backend.services.generation.quick_generate_sync",
            new_callable=AsyncMock,
            return_value=WAV_HEADER_24K_MONO_INT16 + b"\x00" * 100,
        ):
            resp = client.post(
                "/v1/audio/speech",
                json={
                    "model": "voicebox-supertonic",
                    "input": "Hello",
                    "voice": "M1",
                },
            )
            assert resp.status_code == 200

    def test_unknown_voice_falls_back_to_first_valid(self, client):
        with patch(
            "backend.services.profiles._get_preset_voice_ids",
            return_value={"M1"},
        ), patch(
            "backend.services.generation.quick_generate_sync",
            new_callable=AsyncMock,
            return_value=WAV_HEADER_24K_MONO_INT16 + b"\x00" * 100,
        ):
            resp = client.post(
                "/v1/audio/speech",
                json={
                    "model": "voicebox-supertonic",
                    "input": "Hello",
                    "voice": "unknown",
                },
            )
            assert resp.status_code == 200

    def test_engine_without_preset_voices_returns_400(self, client):
        with patch(
            "backend.services.profiles._get_preset_voice_ids",
            return_value=set(),
        ):
            resp = client.post(
                "/v1/audio/speech",
                json={
                    "model": "voicebox-qwen",
                    "input": "Hello",
                    "voice": "M1",
                },
            )
            assert resp.status_code == 400
