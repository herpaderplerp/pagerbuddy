from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from pagerbuddy.config import Settings
from pagerbuddy.database import Base
from pagerbuddy.incidents import acknowledge_incident
from pagerbuddy.models import EscalationPolicy, Incident, NotificationAttempt, NotificationChannel, NotificationStatus, Service, User
from pagerbuddy.notifications import NotificationClient, SendResult, dispatch_notification
from pagerbuddy.notifications import sms_body


class FakeNotificationClient:
    settings = Settings(public_base_url="https://pagerbuddy.example.com")

    def __init__(self, fail_sms: bool = False):
        self.calls = []
        self.sms = []
        self.emails = []
        self.cancellations = []
        self.fail_sms = fail_sms

    def send_phone_call(self, incident, user, attempt_id):
        self.calls.append((incident.id, user.id, attempt_id))
        return SendResult("call-sid")

    def send_sms(self, incident, user):
        self.sms.append((incident.id, user.id))
        if self.fail_sms:
            raise RuntimeError("sms failed")
        return SendResult("sms-sid")

    def send_email(self, incident, user, step, ack_url=None, resolve_url=None):
        self.emails.append((incident.id, user.id, step, ack_url, resolve_url))
        return SendResult("email-sid")

    def cancel_phone_call(self, provider_message_id):
        self.cancellations.append(provider_message_id)
        return SendResult(provider_message_id)


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def make_service(db):
    policy = EscalationPolicy(name="Production", steps=[])
    db.add(policy)
    db.flush()
    return Service(name="API", escalation_policy_id=policy.id, inbound_phone_number="+15551112222")


def test_dispatch_notification_sends_all_user_configured_channels_for_attempt():
    db = make_session()
    user = User(
        name="Responder",
        email="responder@example.com",
        phone_number="+15550000001",
        notification_preferences={"channels": ["phone_call", "sms"]},
    )
    service = make_service(db)
    incident = Incident(service=service, service_id=None, title="Incident")
    db.add_all([user, service, incident])
    db.flush()

    client = FakeNotificationClient()
    attempts = dispatch_notification(db, incident, user, escalation_step=0, attempt_number=1, client=client)

    assert [attempt.channel for attempt in attempts] == [NotificationChannel.phone_call, NotificationChannel.sms]
    assert len(client.calls) == 1
    assert len(client.sms) == 1
    assert len(client.emails) == 0
    persisted = db.scalars(select(NotificationAttempt).where(NotificationAttempt.incident_id == incident.id)).all()
    assert len(persisted) == 2
    assert all(attempt.status == NotificationStatus.delivered for attempt in persisted)


def test_acknowledge_incident_cancels_in_flight_phone_calls():
    db = make_session()
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    service = make_service(db)
    incident = Incident(service=service, service_id=None, title="Incident")
    other_incident = Incident(service=service, service_id=None, title="Other incident")
    db.add_all([user, service, incident, other_incident])
    db.flush()
    active_call = NotificationAttempt(
        incident_id=incident.id,
        user_id=user.id,
        channel=NotificationChannel.phone_call,
        status=NotificationStatus.delivered,
        attempt_number=1,
        escalation_step=0,
        provider_message_id="CA123",
    )
    sms_attempt = NotificationAttempt(
        incident_id=incident.id,
        user_id=user.id,
        channel=NotificationChannel.sms,
        status=NotificationStatus.delivered,
        attempt_number=1,
        escalation_step=0,
        provider_message_id="SM123",
    )
    failed_call = NotificationAttempt(
        incident_id=incident.id,
        user_id=user.id,
        channel=NotificationChannel.phone_call,
        status=NotificationStatus.failed,
        attempt_number=2,
        escalation_step=0,
        provider_message_id="CA456",
    )
    other_call = NotificationAttempt(
        incident_id=other_incident.id,
        user_id=user.id,
        channel=NotificationChannel.phone_call,
        status=NotificationStatus.delivered,
        attempt_number=1,
        escalation_step=0,
        provider_message_id="CA789",
    )
    db.add_all([active_call, sms_attempt, failed_call, other_call])
    db.flush()

    client = FakeNotificationClient()
    acknowledge_incident(db, incident, user, "api", notification_client=client)
    for attempt in (active_call, sms_attempt, failed_call, other_call):
        db.refresh(attempt)

    assert client.cancellations == ["CA123"]
    assert active_call.status == NotificationStatus.acknowledged
    assert sms_attempt.status == NotificationStatus.acknowledged
    assert failed_call.status == NotificationStatus.failed
    assert other_call.status == NotificationStatus.delivered


def test_dispatch_notification_continues_other_channels_when_one_fails():
    db = make_session()
    user = User(
        name="Responder",
        email="responder@example.com",
        phone_number="+15550000001",
        notification_preferences={"channels": ["sms", "email"]},
    )
    service = make_service(db)
    incident = Incident(service=service, service_id=None, title="Incident")
    db.add_all([user, service, incident])
    db.flush()

    client = FakeNotificationClient(fail_sms=True)
    attempts = dispatch_notification(db, incident, user, escalation_step=0, attempt_number=1, client=client)

    assert [attempt.channel for attempt in attempts] == [NotificationChannel.sms, NotificationChannel.email]
    assert attempts[0].status == NotificationStatus.failed
    assert attempts[1].status == NotificationStatus.delivered
    assert len(client.emails) == 1


def test_sms_body_includes_incident_id_reply_format():
    db = make_session()
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    service = make_service(db)
    incident = Incident(service=service, service_id=None, title="Incident")
    db.add_all([user, service, incident])
    db.flush()

    body = sms_body(incident)

    assert f"Incident: {incident.id}" in body
    assert "Reply ACK <incident ID>" in body
    assert "Reply RESOLVE <incident ID>" in body


def test_twilio_trial_mode_rejects_non_allowed_sms_recipient():
    client = NotificationClient(
        Settings(
            twilio_account_sid="sid",
            twilio_auth_token="token",
            twilio_from_number="+15550000000",
            twilio_trial_allowed_number="+15550101010",
        )
    )
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    service = Service(name="API", escalation_policy_id=None, inbound_phone_number="+15551112222")
    incident = Incident(service=service, service_id=None, title="Incident")

    try:
        client.send_sms_text(incident, user, "hello")
    except ValueError as exc:
        assert "+15550101010" in str(exc)
    else:
        raise AssertionError("trial mode should reject non-allowed phone numbers")
