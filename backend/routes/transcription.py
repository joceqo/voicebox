"""Transcription endpoints."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import models
from ..services import transcribe
from ..services.transcribe import ModelDownloadingError

router = APIRouter()


@router.post("/transcribe", response_model=models.TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    model: str | None = Form(None),
):
    """Transcribe audio file to text."""
    try:
        text, duration = await transcribe.transcribe_upload(file, language, model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ModelDownloadingError as e:
        raise HTTPException(
            status_code=202,
            detail={
                "message": f"Whisper model {e.model_size} is being downloaded. Please wait and try again.",
                "model_name": e.model_name,
                "downloading": True,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return models.TranscriptionResponse(text=text, duration=duration)
