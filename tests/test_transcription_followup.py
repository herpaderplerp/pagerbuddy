from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from pagerbuddy.database import Base
from pagerbuddy.incidents import apply_transcription
from pagerbuddy.models import (
    EscalationPolicy,
    Incident,
    IncidentTimeline,
    NotificationAttempt,
    NotificationChannel,
    NotificationStatus,
    Service,
    TimelineEventType,
    User,
)
from pagerbuddy.notifications import SendResult


class FakeNotificationClient:
    def __init__(self):
        self.sms_bodies = []
        self.email_messages = []

    def send_sms_text(self, incident, user, body):
        self.sms_bodies.append((incident.id, user.id, body))
        return SendResult("fake-followup-sms")

    def send_email_text(self, incident, user, subject, body):
        self.email_messages.append((incident.id, user.id, subject, body))
        return SendResult("fake-followup-email")


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_transcription_callback_sends_sms_and_email_followup_to_current_responder():
    db = make_session()
    user = User(
        name="Responder",
        email="responder@example.com",
        phone_number="+15550000001",
        notification_preferences={"channels": ["phone_call", "sms", "email"]},
    )
    db.add(user)
    db.flush()
    policy = EscalationPolicy(
        name="Production",
        steps=[{"target_type": "user", "target_id": str(user.id), "attempt_timeout_seconds": 120, "max_attempts": 2}],
    )
    db.add(policy)
    db.flush()
    service = Service(name="API", escalation_policy_id=policy.id, inbound_phone_number="+15551112222")
    db.add(service)
    db.flush()
    incident = Incident(
        service_id=service.id,
        title="Voicemail from caller",
        recording_sid="RE123",
        escalation_step=0,
    )
    db.add(incident)
    db.flush()
    db.add(
        NotificationAttempt(
            incident_id=incident.id,
            user_id=user.id,
            channel=NotificationChannel.phone_call,
            status=NotificationStatus.delivered,
            attempt_number=1,
            escalation_step=0,
        )
    )
    db.commit()

    fake_client = FakeNotificationClient()
    apply_transcription(db, "RE123", "Database latency is above threshold.", client=fake_client)
    db.commit()

    attempts = db.scalars(select(NotificationAttempt).where(NotificationAttempt.incident_id == incident.id)).all()
    followup_attempts = [attempt for attempt in attempts if attempt.channel in {NotificationChannel.sms, NotificationChannel.email}]
    assert len(followup_attempts) == 2
    assert all(attempt.status == NotificationStatus.delivered for attempt in followup_attempts)
    assert len(fake_client.sms_bodies) == 1
    assert "Database latency is above threshold." in fake_client.sms_bodies[0][2]
    assert len(fake_client.email_messages) == 1
    assert "Voicemail Transcription" in fake_client.email_messages[0][2]

    sent_events = db.scalars(
        select(IncidentTimeline).where(
            IncidentTimeline.incident_id == incident.id,
            IncidentTimeline.event_type == TimelineEventType.notification_sent,
        )
    ).all()
    assert any(event.payload.get("follow_up") == "transcription_received" for event in sent_events)

