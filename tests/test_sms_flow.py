from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pagerbuddy.database import Base
from pagerbuddy.models import (
    EscalationPolicy,
    Incident,
    IncidentStatus,
    NotificationAttempt,
    NotificationChannel,
    NotificationStatus,
    Service,
    User,
)
from pagerbuddy.twilio_webhooks import inbound_sms


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def make_service(db):
    policy = EscalationPolicy(name="Production", steps=[])
    db.add(policy)
    db.flush()
    service = Service(name="API", escalation_policy_id=policy.id, inbound_phone_number="+15551112222")
    db.add(service)
    db.flush()
    return service


def add_sms_attempt(db, incident, user):
    attempt = NotificationAttempt(
        incident_id=incident.id,
        user_id=user.id,
        channel=NotificationChannel.sms,
        status=NotificationStatus.delivered,
        attempt_number=1,
        escalation_step=0,
    )
    db.add(attempt)
    db.flush()
    return attempt


def test_sms_ack_with_incident_id_resolves_ambiguous_open_incidents():
    db = make_session()
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    service = make_service(db)
    first = Incident(service_id=service.id, title="First incident")
    second = Incident(service_id=service.id, title="Second incident")
    db.add_all([user, first, second])
    db.flush()
    add_sms_attempt(db, first, user)
    add_sms_attempt(db, second, user)
    db.commit()

    response = inbound_sms(From=user.phone_number, Body=f"ACK {first.id}", db=db)
    body = response.body.decode()

    assert f"Acknowledged incident {first.id}" in body
    assert db.get(Incident, first.id).status == IncidentStatus.acknowledged
    assert db.get(Incident, second.id).status == IncidentStatus.triggered


def test_sms_resolve_with_incident_id_requires_user_notification_or_assignment():
    db = make_session()
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    other = User(name="Other", email="other@example.com", phone_number="+15550000002")
    service = make_service(db)
    incident = Incident(service_id=service.id, title="Other incident")
    db.add_all([user, other, incident])
    db.flush()
    add_sms_attempt(db, incident, other)
    db.commit()

    response = inbound_sms(From=user.phone_number, Body=f"RESOLVE {incident.id}", db=db)
    body = response.body.decode()

    assert "Multiple or no open incidents found" in body
    assert db.get(Incident, incident.id).status == IncidentStatus.triggered


def test_sms_command_reports_invalid_incident_id():
    db = make_session()
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    db.add(user)
    db.commit()

    response = inbound_sms(From=user.phone_number, Body="ACK nope", db=db)

    assert "Incident ID must be a UUID" in response.body.decode()
