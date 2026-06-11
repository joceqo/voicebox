"""
Mock streaming-STT daemon — a stand-in for the Rust ``voxtral stream`` binary.

It speaks the *exact* wire protocol documented in
:mod:`backend.services.voxtral_stream_daemon` (length-prefixed s16le PCM in;
newline-delimited JSON ``ready``/``partial``/``final`` out), so the WebSocket
bridge and the UI live-transcription path are fully exercised without the real
model. Swap in the real binary by pointing ``VOICEBOX_VOXTRAL_STREAM_CMD`` at
it — nothing else changes.

It "transcribes" by revealing words from a fixed pool in proportion to the
duration of audio received (~one word every 0.45 s), emitting a ``partial`` as
the running text grows and a ``final`` on each ``FLUSH`` / endpoint. The text is
deterministic placeholder content, not real speech recognition.

Run as: ``python -m backend.services.voxtral_stream_mock``
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from .voxtral_stream_daemon import (
    _LEN,
    FRAME_FLUSH,
    FRAME_RESET,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    SAMPLE_RATE,
)

# Reveal roughly one word per this many seconds of received audio.
SECONDS_PER_WORD = 0.45

_WORD_POOL = [
    "the",
    "quick",
    "brown",
    "fox",
    "jumps",
    "over",
    "the",
    "lazy",
    "dog",
    "while",
    "the",
    "curious",
    "cat",
    "watches",
    "quietly",
    "from",
    "a",
    "sunlit",
    "windowsill",
    "as",
    "morning",
    "light",
    "spills",
    "across",
    "the",
    "room",
    "and",
    "somewhere",
    "outside",
    "a",
    "gentle",
    "breeze",
    "carries",
    "the",
    "sound",
    "of",
    "distant",
    "voices",
]


def _emit(obj: dict[str, Any]) -> None:
    """Write one JSON event line to stdout and flush immediately."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _read_exact(n: int) -> bytes | None:
    """Read exactly ``n`` bytes from stdin, or ``None`` on EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sys.stdin.buffer.read(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class _Utterance:
    """Accumulates audio for one utterance and reveals words over time."""

    def __init__(self) -> None:
        self.samples = 0
        self.words: list[str] = []

    @property
    def duration_s(self) -> float:
        return self.samples / SAMPLE_RATE

    def add_audio(self, pcm: bytes) -> bool:
        """Append PCM; return True if the running text grew (-> emit partial)."""
        self.samples += len(pcm) // 2  # s16le -> 2 bytes/sample
        target = int(self.duration_s / SECONDS_PER_WORD)
        grew = False
        while len(self.words) < target:
            # Cycle the pool so utterances of any length keep producing text.
            self.words.append(_WORD_POOL[len(self.words) % len(_WORD_POOL)])
            grew = True
        return grew

    def text(self) -> str:
        if not self.words:
            return ""
        sentence = " ".join(self.words)
        return sentence[0].upper() + sentence[1:] + "."


def main() -> int:
    # Simulate a (brief) model load before announcing readiness.
    time.sleep(0.05)
    _emit(
        {
            "type": "ready",
            "protocol": PROTOCOL_VERSION,
            "engine": "mock",
            "sample_rate": SAMPLE_RATE,
        }
    )

    utterance_index = 0
    current = _Utterance()

    def finalize() -> None:
        nonlocal utterance_index, current
        _emit({"type": "final", "utterance": utterance_index, "text": current.text()})
        utterance_index += 1
        current = _Utterance()

    while True:
        header = _read_exact(_LEN.size)
        if header is None:
            break  # stdin EOF
        (n,) = _LEN.unpack(header)

        if n == FRAME_FLUSH:
            finalize()
            continue
        if n == FRAME_RESET:
            utterance_index = 0
            current = _Utterance()
            continue
        if n > MAX_FRAME_BYTES:
            _emit({"type": "error", "message": f"frame too large: {n} bytes"})
            break

        pcm = _read_exact(n)
        if pcm is None:
            break  # truncated frame at EOF
        if current.add_audio(pcm):
            _emit({"type": "partial", "utterance": utterance_index, "text": current.text()})

    # Flush any pending utterance on EOF so the host gets closure.
    if current.words or current.samples:
        finalize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
