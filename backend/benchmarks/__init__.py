"""TTS benchmark/rating harness for the Voicebox backend.

This package drives the *existing* HTTP API (``POST /v1/audio/speech`` and
``POST /v1/audio/transcriptions``) to measure and rate local TTS engines. It
deliberately does not import the engines directly, so it tests the real
integration surface a third-party app would use.
"""
