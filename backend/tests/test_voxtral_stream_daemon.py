"""Integration tests for the Voxtral streaming-STT daemon manager + mock daemon.

These drive the real subprocess over the framed stdin/stdout protocol, so they
cover the contract the Rust ``voxtral stream`` binary must implement too. They
use only the stdlib (``asyncio.run`` per test), so they run without
``pytest-asyncio`` or the heavy backend venv.
"""

import asyncio

import pytest

from ..services.voxtral_stream_daemon import (
    _LEN,
    FRAME_FLUSH,
    FRAME_RESET,
    SAMPLE_RATE,
    SessionBusyError,
    VoxtralStreamDaemon,
    encode_flush,
    encode_frame,
    encode_reset,
)


def _pcm(seconds: float) -> bytes:
    """`seconds` of silent s16le mono audio at the protocol sample rate."""
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


async def _next_event(daemon: VoxtralStreamDaemon, timeout: float = 5.0) -> dict:
    return await asyncio.wait_for(daemon.events.get(), timeout)


async def _drain_until_final(daemon: VoxtralStreamDaemon, timeout: float = 5.0) -> dict:
    """Pull events until a ``final`` arrives; return it. Bounded by ``timeout``."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise AssertionError("timed out waiting for final event")
        event = await _next_event(daemon, remaining)
        if event.get("type") == "final":
            return event


# -- framing -------------------------------------------------------------------


def test_frame_encoding_roundtrip():
    body = b"\x01\x02\x03\x04"
    framed = encode_frame(body)
    (length,) = _LEN.unpack(framed[: _LEN.size])
    assert length == len(body)
    assert framed[_LEN.size :] == body

    (flush_len,) = _LEN.unpack(encode_flush())
    assert flush_len == FRAME_FLUSH
    (reset_len,) = _LEN.unpack(encode_reset())
    assert reset_len == FRAME_RESET


# -- daemon lifecycle + transcription cadence ----------------------------------


def test_ready_partials_and_final():
    async def scenario():
        daemon = VoxtralStreamDaemon()
        try:
            async with daemon.session() as d:
                assert d.ready_info.get("type") == "ready"
                assert d.ready_info.get("sample_rate") == SAMPLE_RATE

                # Feed ~1.5s of audio in 0.1s chunks, then finalize.
                for _ in range(15):
                    await d.feed(_pcm(0.1))
                await d.flush()

                # Collect everything up to the final; partials precede it.
                partials = []
                final = None
                deadline = asyncio.get_event_loop().time() + 5.0
                while final is None:
                    remaining = deadline - asyncio.get_event_loop().time()
                    assert remaining > 0, "timed out waiting for final"
                    ev = await _next_event(daemon, remaining)
                    if ev.get("type") == "partial":
                        partials.append(ev)
                    elif ev.get("type") == "final":
                        final = ev

                assert partials, "expected interim partials as audio streamed"
                assert final["utterance"] == 0
                assert final["text"], "final transcript should be non-empty"
                # The transcript only grows: each partial is no longer than the final.
                assert len(partials[-1]["text"]) <= len(final["text"])
        finally:
            await daemon.stop()

    asyncio.run(scenario())


def test_multiple_utterances_increment_index():
    async def scenario():
        daemon = VoxtralStreamDaemon()
        try:
            async with daemon.session() as d:
                await d.feed(_pcm(0.6))
                await d.flush()
                first = await _drain_until_final(daemon)

                await d.feed(_pcm(0.6))
                await d.flush()
                second = await _drain_until_final(daemon)

                assert first["utterance"] == 0
                assert second["utterance"] == 1
        finally:
            await daemon.stop()

    asyncio.run(scenario())


def test_reset_between_sessions_clears_utterance_index():
    async def scenario():
        daemon = VoxtralStreamDaemon()
        try:
            async with daemon.session() as d:
                await d.feed(_pcm(0.6))
                await d.flush()
                assert (await _drain_until_final(daemon))["utterance"] == 0

            # New session resets state: utterance index starts at 0 again.
            async with daemon.session() as d:
                await d.feed(_pcm(0.6))
                await d.flush()
                assert (await _drain_until_final(daemon))["utterance"] == 0
        finally:
            await daemon.stop()

    asyncio.run(scenario())


def test_second_concurrent_session_is_rejected():
    async def scenario():
        daemon = VoxtralStreamDaemon()
        try:
            async with daemon.session():
                assert daemon.session_busy() is True
                with pytest.raises(SessionBusyError):
                    async with daemon.session():
                        pass
            assert daemon.session_busy() is False
        finally:
            await daemon.stop()

    asyncio.run(scenario())


if __name__ == "__main__":
    # Allow running directly without pytest for a quick smoke check.
    import sys

    test_frame_encoding_roundtrip()
    test_ready_partials_and_final()
    test_multiple_utterances_increment_index()
    test_reset_between_sessions_clears_utterance_index()
    test_second_concurrent_session_is_rejected()
    print("ok", file=sys.stderr)
