from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pagerbuddy.config import Settings


class LocalTranscriptionError(RuntimeError):
    pass


class Segment(Protocol):
    text: str


@lru_cache(maxsize=4)
def _model(model_name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise LocalTranscriptionError("faster-whisper is not installed") from exc
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def transcribe_recording(path: Path, settings: Settings) -> str:
    if not path.exists():
        raise LocalTranscriptionError(f"recording file not found: {path}")
    try:
        segments, _ = _model(
            settings.local_transcription_model,
            settings.local_transcription_device,
            settings.local_transcription_compute_type,
        ).transcribe(str(path))
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    except LocalTranscriptionError:
        raise
    except Exception as exc:
        raise LocalTranscriptionError(str(exc)) from exc
    return text.strip()
