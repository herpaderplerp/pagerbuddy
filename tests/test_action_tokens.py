from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pagerbuddy.config import Settings
from pagerbuddy.api import consume_incident_action
from pagerbuddy.database import Base
from pagerbuddy.models import EscalationPolicy, Incident, IncidentActionToken, IncidentStatus, Service, User


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_action_token_expires_after_incident_resolved():
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
        title="Closed incident",
        status=IncidentStatus.resolved,
        resolved_at=datetime.now(timezone.utc),
    )
    db.add(incident)
    db.flush()
    token = IncidentActionToken(incident_id=incident.id, user_id=user.id, action="acknowledge", token="closed-token")
    db.add(token)
    db.commit()

    try:
        consume_incident_action("closed-token", db)
    except HTTPException as exc:
        assert exc.status_code == 410
    else:
        raise AssertionError("closed incident token should expire")


def test_action_token_expires_after_configured_ttl(monkeypatch):
    monkeypatch.setattr(
        "pagerbuddy.api.get_settings",
        lambda: Settings(incident_action_token_ttl_seconds=60),
    )
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
    incident = Incident(service_id=service.id, title="Open incident")
    db.add(incident)
    db.flush()
    token = IncidentActionToken(
        incident_id=incident.id,
        user_id=user.id,
        action="acknowledge",
        token="expired-token",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    db.add(token)
    db.commit()

    try:
        consume_incident_action("expired-token", db)
    except HTTPException as exc:
        assert exc.status_code == 410
        assert "expired" in str(exc.detail)
    else:
        raise AssertionError("old action token should expire")
