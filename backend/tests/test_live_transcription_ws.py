"""WebSocket tests for ``/ws/transcribe`` (the live-STT bridge).

Uses Starlette's in-process TestClient (no ``websockets`` package needed) and
drives the real mock daemon end-to-end: audio frames in, ``ready`` / ``partial``
/ ``final`` JSON out, plus the single-session busy rejection.
"""

import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ..routes.live_transcription import router
from ..services.voxtral_stream_daemon import SAMPLE_RATE


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Stop the (singleton) daemon on app exit, in the app's own event loop, so
    # the spawned subprocess doesn't leak between tests.
    yield
    from ..services.voxtral_stream_daemon import shutdown_voxtral_stream_daemon

    await shutdown_voxtral_stream_daemon()


@pytest.fixture
def client():
    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)
    # `with` enters/exits the lifespan, ensuring daemon teardown after the test.
    with TestClient(app) as test_client:
        yield test_client


def _pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def test_ready_then_partial_then_final(client):
    with client.websocket_connect("/ws/transcribe") as ws:
        status = ws.receive_json()
        assert status == {"type": "status", "state": "loading"}

        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["sample_rate"] == SAMPLE_RATE

        # Stream ~1.2s of audio in chunks so several partials are produced.
        for _ in range(4):
            ws.send_bytes(_pcm(0.3))
        ws.send_text(json.dumps({"action": "flush"}))

        partials = []
        final = None
        for _ in range(100):
            event = ws.receive_json()
            if event["type"] == "partial":
                partials.append(event)
            elif event["type"] == "final":
                final = event
                break

        assert partials, "expected interim partial events"
        assert final is not None
        assert final["utterance"] == 0
        assert final["text"], "final transcript should be non-empty"

        ws.send_text(json.dumps({"action": "stop"}))


def test_second_connection_rejected_while_busy(client):
    with client.websocket_connect("/ws/transcribe") as ws1:
        assert ws1.receive_json()["type"] == "status"
        assert ws1.receive_json()["type"] == "ready"  # session now held

        # A second connection must be told it's busy, then closed.
        with client.websocket_connect("/ws/transcribe") as ws2:
            err = ws2.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "busy"
