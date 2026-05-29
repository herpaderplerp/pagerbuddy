from io import BytesIO

from pagerbuddy.config import Settings
from pagerbuddy.recordings import download_recording, recording_media_url
from pagerbuddy.transcription import transcribe_recording


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


def test_download_recording_adds_mp3_extension_and_writes_file(tmp_path, monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(b"recording-bytes")

    monkeypatch.setattr("pagerbuddy.recordings.urlopen", fake_urlopen)

    path = download_recording(
        "https://api.twilio.com/2010-04-01/Accounts/test/Recordings/RE123",
        "RE123",
        Settings(
            recording_storage_dir=str(tmp_path),
            twilio_account_sid="sid",
            twilio_auth_token="token",
        ),
    )

    assert path.name == "RE123.mp3"
    assert path.read_bytes() == b"recording-bytes"
    assert requests[0][0].full_url.endswith("/RE123.mp3")
    assert requests[0][0].get_header("Authorization").startswith("Basic ")


def test_recording_media_url_preserves_existing_media_extension():
    assert recording_media_url("https://example.com/RE123.mp3") == "https://example.com/RE123.mp3"
    assert recording_media_url("https://example.com/RE123") == "https://example.com/RE123.mp3"


def test_transcribe_recording_uses_configured_local_whisper_model(tmp_path, monkeypatch):
    recording = tmp_path / "RE123.mp3"
    recording.write_bytes(b"fake-audio")
    calls = []

    class FakeSegment:
        def __init__(self, text):
            self.text = text

    class FakeModel:
        def transcribe(self, path):
            calls.append(path)
            return [FakeSegment(" first "), FakeSegment("second")], object()

    monkeypatch.setattr("pagerbuddy.transcription._model", lambda model, device, compute_type: FakeModel())

    text = transcribe_recording(
        recording,
        Settings(
            local_transcription_model="tiny.en",
            local_transcription_device="cpu",
            local_transcription_compute_type="int8",
        ),
    )

    assert text == "first second"
    assert calls == [str(recording)]
