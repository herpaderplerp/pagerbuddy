from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pagerbuddy.api import (
    _incident_action_confirmation_token,
    confirm_incident_action,
    consume_incident_action,
)
from pagerbuddy.config import Settings
from pagerbuddy.database import Base
from pagerbuddy.models import EscalationPolicy, Incident, IncidentActionToken, IncidentStatus, Service, User


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def seed_action_token(
    db,
    *,
    action="acknowledge",
    incident_status=IncidentStatus.triggered,
    token_value="action-token",
    created_at=None,
):
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
        title="Database latency",
        status=incident_status,
        resolved_at=datetime.now(timezone.utc) if incident_status == IncidentStatus.resolved else None,
    )
    db.add(incident)
    db.flush()
    token = IncidentActionToken(
        incident_id=incident.id,
        user_id=user.id,
        action=action,
        token=token_value,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(token)
    db.commit()
    return incident, token


def test_action_token_expires_after_incident_resolved():
    db = make_session()
    seed_action_token(db, incident_status=IncidentStatus.resolved, token_value="closed-token")

    try:
        consume_incident_action("closed-token", "bad-confirmation-token", db)
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
    seed_action_token(
        db,
        token_value="expired-token",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )

    try:
        consume_incident_action("expired-token", "bad-confirmation-token", db)
    except HTTPException as exc:
        assert exc.status_code == 410
        assert "expired" in str(exc.detail)
    else:
        raise AssertionError("old action token should expire")


def test_get_action_token_renders_confirmation_without_mutating_incident():
    db = make_session()
    incident, token = seed_action_token(db)

    response = confirm_incident_action("action-token", db)

    db.refresh(incident)
    db.refresh(token)
    body = response.body.decode()
    assert response.status_code == 200
    assert '<form method="post">' in body
    assert 'name="confirmation_token"' in body
    assert "Confirm acknowledge" in body
    assert "will not change the incident until you confirm" in body
    assert incident.status == IncidentStatus.triggered
    assert token.used_at is None


def test_post_action_token_requires_confirmation_token():
    db = make_session()
    incident, token = seed_action_token(db)

    try:
        consume_incident_action("action-token", "bad-confirmation-token", db)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("action should require a valid confirmation token")

    db.refresh(incident)
    db.refresh(token)
    assert incident.status == IncidentStatus.triggered
    assert token.used_at is None


def test_post_action_token_consumes_valid_confirmation():
    db = make_session()
    incident, token = seed_action_token(db)
    confirmation_token = _incident_action_confirmation_token(token)

    result = consume_incident_action("action-token", confirmation_token, db)

    db.refresh(token)
    assert result.id == incident.id
    assert result.status == IncidentStatus.acknowledged
    assert token.used_at is not None
