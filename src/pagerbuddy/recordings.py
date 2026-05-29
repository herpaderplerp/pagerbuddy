import base64
import re
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from pagerbuddy.config import Settings


class RecordingDownloadError(RuntimeError):
    pass


def download_recording(recording_url: str, recording_sid: str | None, settings: Settings) -> Path:
    if not recording_url:
        raise RecordingDownloadError("recording URL is missing")
    storage_dir = Path(settings.recording_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    source_url = recording_media_url(recording_url)
    filename = f"{_safe_name(recording_sid or uuid.uuid4().hex)}.mp3"
    destination = storage_dir / filename
    temporary = destination.with_suffix(".tmp")

    request = Request(source_url)
    if settings.twilio_account_sid and settings.twilio_auth_token:
        credentials = f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode("utf-8")
        request.add_header("Authorization", f"Basic {base64.b64encode(credentials).decode('ascii')}")

    try:
        with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                output.write(chunk)
        temporary.replace(destination)
    except (OSError, URLError) as exc:
        if temporary.exists():
            temporary.unlink()
        raise RecordingDownloadError(str(exc)) from exc
    return destination


def recording_media_url(recording_url: str) -> str:
    if recording_url.endswith((".mp3", ".wav")):
        return recording_url
    return f"{recording_url}.mp3"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("._")
    return safe or uuid.uuid4().hex
