from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from pagerbuddy.config import Settings
from pagerbuddy.database import Base
from pagerbuddy.models import EscalationPolicy, Incident, Service, User
from pagerbuddy.twilio_webhooks import inbound_voice, outbound_response, recording_complete


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_inbound_voice_disables_twilio_transcription_when_local_transcription_enabled(monkeypatch):
    monkeypatch.setattr(
        "pagerbuddy.twilio_webhooks.get_settings",
        lambda: Settings(public_base_url="https://pagerbuddy.example.com", local_transcription_enabled=True),
    )

    response = inbound_voice(To="+15551112222", db=make_session())
    body = response.body.decode()

    assert 'transcribe="false"' in body
    assert "transcriptionCallback" not in body


def test_outbound_response_plays_twilio_recording_url_when_available():
    db = make_session()
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    db.add(user)
    db.flush()
    policy = EscalationPolicy(name="Production", steps=[])
    db.add(policy)
    db.flush()
    service = Service(name="API", escalation_policy_id=policy.id, inbound_phone_number="+15551112222")
    db.add(service)
    db.flush()
    incident = Incident(
        service_id=service.id,
        title="Voicemail",
        caller_id="+15550000002",
        recording_url="https://api.twilio.com/2010-04-01/Accounts/test/Recordings/RE123",
    )
    db.add(incident)
    db.commit()

    response = outbound_response(incident_id=incident.id, user_id=user.id, db=db)
    body = response.body.decode()

    assert "<Play>https://api.twilio.com/2010-04-01/Accounts/test/Recordings/RE123.mp3</Play>" in body
    assert "Playing voicemail now" in body
    assert "Press 1 to acknowledge" in body


def test_outbound_response_speaks_transcription_when_recording_url_missing():
    db = make_session()
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    db.add(user)
    db.flush()
    policy = EscalationPolicy(name="Production", steps=[])
    db.add(policy)
    db.flush()
    service = Service(name="API", escalation_policy_id=policy.id, inbound_phone_number="+15551112222")
    db.add(service)
    db.flush()
    incident = Incident(service_id=service.id, title="Voicemail", transcription="Database is down.")
    db.add(incident)
    db.commit()

    response = outbound_response(incident_id=incident.id, user_id=user.id, db=db)
    body = response.body.decode()

    assert "<Play>" not in body
    assert "Database is down." in body


def test_recording_callback_downloads_and_transcribes_before_escalation(tmp_path, monkeypatch):
    db = make_session()
    policy = EscalationPolicy(name="Production", steps=[])
    db.add(policy)
    db.flush()
    service = Service(name="API", escalation_policy_id=policy.id, inbound_phone_number="+15551112222")
    db.add(service)
    db.commit()
    local_recording = tmp_path / "RE123.mp3"
    local_recording.write_bytes(b"fake-audio")
    call_order = []

    def fake_download(recording_url, recording_sid, settings):
        call_order.append("download")
        return Path(local_recording)

    def fake_transcribe(path, settings):
        call_order.append("transcribe")
        assert path == local_recording
        return "Database latency is above threshold."

    monkeypatch.setattr(
        "pagerbuddy.twilio_webhooks.get_settings",
        lambda: Settings(store_recordings_locally=True, local_transcription_enabled=True),
    )
    monkeypatch.setattr("pagerbuddy.twilio_webhooks.download_recording", fake_download)
    monkeypatch.setattr("pagerbuddy.twilio_webhooks.transcribe_recording", fake_transcribe)

    recording_complete(
        To="+15551112222",
        From="+15550000002",
        CallSid="CA123",
        RecordingSid="RE123",
        RecordingUrl="https://api.twilio.com/2010-04-01/Accounts/test/Recordings/RE123",
        db=db,
    )

    incident = db.scalar(select(Incident).where(Incident.recording_sid == "RE123"))
    assert incident is not None
    assert incident.transcription == "Database latency is above threshold."
    assert call_order == ["download", "transcribe"]
