from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pagerbuddy.database import Base
from pagerbuddy.escalation import process_due_incident, start_escalation
from pagerbuddy.models import EscalationPolicy, Incident, IncidentStatus, Service, User


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_escalation_retries_then_moves_to_next_step():
    db = make_session()
    first = User(name="First", email="first@example.com", phone_number="+15550000001")
    second = User(name="Second", email="second@example.com", phone_number="+15550000002")
    db.add_all([first, second])
    db.flush()
    policy = EscalationPolicy(
        name="Production",
        steps=[
            {
                "target_type": "user",
                "target_id": str(first.id),
                "attempt_timeout_seconds": 120,
                "max_attempts": 2,
            },
            {
                "target_type": "user",
                "target_id": str(second.id),
                "attempt_timeout_seconds": 120,
                "max_attempts": 1,
            },
        ],
    )
    db.add(policy)
    db.flush()
    service = Service(name="API", escalation_policy_id=policy.id, inbound_phone_number="+15551112222")
    db.add(service)
    db.flush()
    incident = Incident(service_id=service.id, title="Voicemail from caller")
    db.add(incident)
    db.commit()

    start_escalation(db, incident)
    assert incident.escalation_step == 0
    assert incident.attempts_in_step == 1

    due = datetime.now(timezone.utc) + timedelta(minutes=3)
    process_due_incident(db, incident, now=due)
    assert incident.escalation_step == 0
    assert incident.attempts_in_step == 2

    process_due_incident(db, incident, now=due + timedelta(minutes=3))
    assert incident.escalation_step == 1
    assert incident.attempts_in_step == 1
    assert incident.status == IncidentStatus.triggered
