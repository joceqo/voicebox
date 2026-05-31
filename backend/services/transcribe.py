"""
STT (Speech-to-Text) module - delegates to backend abstraction layer.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from ..backends import get_stt_backend, STTBackend

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB


class ModelDownloadingError(Exception):
    """Raised when the requested Whisper size is not cached and a background
    download has just been kicked off. Callers map this to their own HTTP
    shape (native route -> 202, OpenAI route -> 503 + Retry-After)."""

    def __init__(self, model_size: str):
        self.model_size = model_size
        self.model_name = f"whisper-{model_size}"
        super().__init__(f"Whisper model {model_size} is being downloaded.")


def get_whisper_model() -> STTBackend:
    """
    Get STT backend instance (MLX or PyTorch based on platform).

    Returns:
        STT backend instance
    """
    return get_stt_backend()


def unload_whisper_model():
    """Unload Whisper model to free memory."""
    backend = get_stt_backend()
    backend.unload_model()


async def transcribe_upload(
    file: UploadFile,
    language: Optional[str],
    model_size: Optional[str],
) -> tuple[str, float]:
    """Transcribe an uploaded audio file with Whisper.

    Saves the upload to a temp file, resolves/validates the Whisper size,
    triggers a background download if the model isn't cached, and returns
    ``(text, duration_seconds)``.

    Shared by the native ``POST /transcribe`` route and the OpenAI-compatible
    ``POST /v1/audio/transcriptions`` route so neither duplicates the
    download/validation logic.

    Raises:
        ValueError: the resolved model size is not a known Whisper size.
        ModelDownloadingError: the size is downloading; the caller should retry.
    """
    from ..utils.audio import load_audio, transcode_to_wav, is_soundfile_readable
    from ..backends import (
        WHISPER_HF_REPOS,
        resolve_stt_model,
        get_parakeet_backend,
        get_voxtral_stt_backend,
    )
    from .task_queue import create_background_task
    from ..utils.tasks import get_task_manager

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            tmp.write(chunk)
        tmp_path = tmp.name

    # The ASR backends (onnx-asr/Parakeet, Whisper) read the file with
    # soundfile, which only understands libsndfile formats (WAV/FLAC/OGG).
    # Browser MediaRecorder uploads are webm/opus and fail with "file does not
    # start with RIFF id". librosa's load() would mask this (it has an
    # audioread fallback the backends don't), so probe with soundfile directly
    # and transcode to a canonical WAV via ffmpeg when it can't read the input.
    transcoded_path: str | None = None
    try:
        decode_path = tmp_path
        if not await asyncio.to_thread(is_soundfile_readable, tmp_path):
            transcoded_path = await asyncio.to_thread(transcode_to_wav, tmp_path)
            decode_path = transcoded_path

        audio, sr = await asyncio.to_thread(load_audio, decode_path)
        duration = len(audio) / sr

        # Dispatch: Parakeet is opt-in by model name; Whisper is the default.
        engine, normalized_id = resolve_stt_model(model_size)
        if engine == "parakeet":
            # onnx-asr downloads the ONNX weights on first load() inside the
            # worker thread; we accept first-call latency rather than mirroring
            # the Whisper background-download / ModelDownloadingError dance.
            parakeet = get_parakeet_backend()
            text = await parakeet.transcribe(decode_path, language, normalized_id)
            return text, duration

        if engine == "voxtral_stt":
            # Local Voxtral Mini Realtime via the Rust CLI. No background
            # download dance: the binary + GGUF weights are installed manually,
            # and the backend raises an actionable error if they're missing.
            voxtral = get_voxtral_stt_backend()
            text = await voxtral.transcribe(decode_path, language, normalized_id)
            return text, duration

        whisper_model = get_whisper_model()
        size = model_size if model_size else whisper_model.model_size

        if size not in WHISPER_HF_REPOS:
            valid = ", ".join(WHISPER_HF_REPOS.keys())
            raise ValueError(f"Invalid model size '{size}'. Must be one of: {valid}")

        already_loaded = whisper_model.is_loaded() and whisper_model.model_size == size
        if not already_loaded and not whisper_model._is_model_cached(size):
            progress_model_name = f"whisper-{size}"
            task_manager = get_task_manager()

            async def download_whisper_background():
                try:
                    await whisper_model.load_model_async(size)
                    task_manager.complete_download(progress_model_name)
                except Exception as e:
                    task_manager.error_download(progress_model_name, str(e))

            task_manager.start_download(progress_model_name)
            create_background_task(download_whisper_background())
            raise ModelDownloadingError(size)

        text = await whisper_model.transcribe(decode_path, language, size)
        return text, duration
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        if transcoded_path:
            Path(transcoded_path).unlink(missing_ok=True)
