"""Live speech-to-text over WebSocket.

Bridges a browser microphone stream to the resident Voxtral streaming-STT daemon
(see :mod:`backend.services.voxtral_stream_daemon`). The client sends binary
frames of 16 kHz mono ``s16le`` PCM and JSON control messages; the server streams
back ``ready`` / ``partial`` / ``final`` / ``error`` events as JSON.

Kept deliberately free of the heavy STT/torch imports used by the one-shot
``POST /transcribe`` route so the daemon bridge can be exercised on its own.

Client protocol (over the WebSocket)
------------------------------------
* **binary message** -> a chunk of 16 kHz mono ``s16le`` PCM audio.
* **text message** ``{"action": "flush"}`` -> finalize the current utterance.
* **text message** ``{"action": "stop"}``  -> end the session (the client may
  also just close the socket).

Server -> client (JSON text messages):
* ``{"type": "status", "state": "loading"}`` -> daemon is starting / loading.
* ``{"type": "ready", "sample_rate": 16000, ...}`` -> send audio now.
* ``{"type": "partial", "utterance": n, "text": ...}`` -> interim hypothesis.
* ``{"type": "final", "utterance": n, "text": ...}`` -> finalized utterance.
* ``{"type": "error", "message": ..., "code": ...}`` -> error (then close).
"""

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.voxtral_stream_daemon import (
    PROTOCOL_VERSION,
    SAMPLE_RATE,
    DaemonNotRunningError,
    SessionBusyError,
    VoxtralStreamDaemon,
    get_voxtral_stream_daemon,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# WebSocket close codes.
_WS_TRY_AGAIN_LATER = 1013
_WS_INTERNAL_ERROR = 1011


@router.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket) -> None:
    """Stream microphone audio in, live transcription out."""
    await websocket.accept()
    daemon = get_voxtral_stream_daemon()

    # One continuous audio stream per resident model -> one session at a time.
    if daemon.session_busy():
        await websocket.send_json(
            {
                "type": "error",
                "code": "busy",
                "message": "A transcription session is already active.",
            }
        )
        await websocket.close(code=_WS_TRY_AGAIN_LATER)
        return

    await websocket.send_json({"type": "status", "state": "loading"})

    try:
        async with daemon.session() as session:
            ready = dict(session.ready_info) or {}
            ready.update(type="ready")
            ready.setdefault("protocol", PROTOCOL_VERSION)
            ready.setdefault("sample_rate", SAMPLE_RATE)
            await websocket.send_json(ready)

            downlink = asyncio.create_task(_forward_events(websocket, daemon), name="ws-transcribe-downlink")
            try:
                await _consume_uplink(websocket, daemon)
            finally:
                downlink.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await downlink
    except SessionBusyError:
        # Lost a race for the single session slot.
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"type": "error", "code": "busy", "message": "A transcription session is already active."}
            )
        await _safe_close(websocket, _WS_TRY_AGAIN_LATER)
        return
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("voxtral-stream: live transcription session failed")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "message": "Transcription session failed."})
        await _safe_close(websocket, _WS_INTERNAL_ERROR)
        return

    await _safe_close(websocket)


async def _consume_uplink(websocket: WebSocket, daemon: VoxtralStreamDaemon) -> None:
    """Pump client audio / control messages into the daemon until it ends."""
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return

        chunk = message.get("bytes")
        if chunk:
            try:
                await daemon.feed(chunk)
            except DaemonNotRunningError:
                with contextlib.suppress(Exception):
                    await websocket.send_json(
                        {"type": "error", "code": "daemon_closed", "message": "Transcription engine stopped."}
                    )
                return
            continue

        text = message.get("text")
        if text is None:
            continue
        try:
            action = json.loads(text).get("action")
        except (ValueError, AttributeError):
            action = None
        if action == "flush":
            with contextlib.suppress(DaemonNotRunningError):
                await daemon.flush()
        elif action == "stop":
            return


async def _forward_events(websocket: WebSocket, daemon: VoxtralStreamDaemon) -> None:
    """Forward daemon events to the client until cancelled or the daemon dies."""
    while True:
        event = await daemon.events.get()
        if event.get("type") == "closed":
            with contextlib.suppress(Exception):
                await websocket.send_json(
                    {"type": "error", "code": "daemon_closed", "message": "Transcription engine stopped."}
                )
            return
        await websocket.send_json(event)


async def _safe_close(websocket: WebSocket, code: int = 1000) -> None:
    with contextlib.suppress(Exception):
        await websocket.close(code=code)
