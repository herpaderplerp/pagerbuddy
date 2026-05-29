from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pagerbuddy.models import (
    EscalationPolicy,
    Incident,
    IncidentStatus,
    Schedule,
    TimelineEventType,
    User,
)
from pagerbuddy.notifications import NotificationClient, dispatch_notification
from pagerbuddy.schedules import resolve_on_call_user
from pagerbuddy.timeline import record_event


DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_ATTEMPTS = 1


def start_escalation(db: Session, incident: Incident) -> Incident:
    incident.escalation_step = 0
    incident.escalation_cycle = 0
    incident.attempts_in_step = 0
    incident.next_escalation_at = None
    return notify_current_step(db, incident)


def process_due_escalations(db: Session, now: datetime | None = None) -> int:
    current_time = now or datetime.now(timezone.utc)
    incidents = db.scalars(
        select(Incident).where(
            Incident.status == IncidentStatus.triggered,
            Incident.next_escalation_at.is_not(None),
            Incident.next_escalation_at <= current_time,
        )
    ).all()
    for incident in incidents:
        process_due_incident(db, incident, current_time)
    return len(incidents)


def process_due_incident(db: Session, incident: Incident, now: datetime | None = None) -> Incident:
    if incident.status != IncidentStatus.triggered:
        return incident
    policy = incident.service.escalation_policy
    step = _current_step(policy, incident.escalation_step)
    max_attempts = int(step.get("max_attempts", DEFAULT_MAX_ATTEMPTS)) if step else 0
    if step and incident.attempts_in_step < max_attempts:
        return notify_current_step(db, incident, now=now)
    return advance_to_next_step(db, incident, now=now)


def manual_escalate(db: Session, incident: Incident, actor_user_id: str, channel: str = "phone_call") -> Incident:
    record_event(
        db,
        incident.id,
        TimelineEventType.escalation_step_started,
        {"manual": True, "actor_user_id": actor_user_id, "channel": channel, "from_step": incident.escalation_step},
    )
    return advance_to_next_step(db, incident)


def notify_current_step(
    db: Session,
    incident: Incident,
    now: datetime | None = None,
    client: NotificationClient | None = None,
) -> Incident:
    if incident.status != IncidentStatus.triggered:
        return incident
    policy = incident.service.escalation_policy
    step = _current_step(policy, incident.escalation_step)
    if step is None:
        return exhaust_or_repeat(db, incident, now=now)

    user = _resolve_step_user(db, step, incident)
    if user is None:
        record_event(
            db,
            incident.id,
            TimelineEventType.notification_failed,
            {"reason": "no target user resolved", "escalation_step": incident.escalation_step},
        )
        incident.attempts_in_step = int(step.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
        return advance_to_next_step(db, incident, now=now)

    if incident.attempts_in_step == 0:
        record_event(
            db,
            incident.id,
            TimelineEventType.escalation_step_started,
            {"escalation_step": incident.escalation_step, "target_user_id": str(user.id)},
        )

    attempt_number = incident.attempts_in_step + 1
    dispatch_notification(db, incident, user, incident.escalation_step, attempt_number, client=client)
    incident.attempts_in_step = attempt_number
    timeout = int(step.get("attempt_timeout_seconds", DEFAULT_ATTEMPT_TIMEOUT_SECONDS))
    incident.next_escalation_at = (now or datetime.now(timezone.utc)) + timedelta(seconds=timeout)
    return incident


def advance_to_next_step(db: Session, incident: Incident, now: datetime | None = None) -> Incident:
    incident.escalation_step += 1
    incident.attempts_in_step = 0
    record_event(
        db,
        incident.id,
        TimelineEventType.escalation_step_started,
        {"escalation_step": incident.escalation_step},
    )
    return notify_current_step(db, incident, now=now)


def exhaust_or_repeat(db: Session, incident: Incident, now: datetime | None = None) -> Incident:
    policy = incident.service.escalation_policy
    can_repeat = policy.repeat_enabled and (policy.repeat_count == 0 or incident.escalation_cycle < policy.repeat_count)
    if can_repeat:
        incident.escalation_cycle += 1
        incident.escalation_step = 0
        incident.attempts_in_step = 0
        record_event(
            db,
            incident.id,
            TimelineEventType.escalation_step_started,
            {"repeat_cycle": incident.escalation_cycle, "escalation_step": 0},
        )
        return notify_current_step(db, incident, now=now)

    incident.next_escalation_at = None
    record_event(
        db,
        incident.id,
        TimelineEventType.escalation_exhausted,
        {"catchall_user_id": str(policy.catchall_user_id) if policy.catchall_user_id else None},
    )
    if policy.catchall_user_id:
        catchall = db.get(User, policy.catchall_user_id)
        if catchall:
            dispatch_notification(db, incident, catchall, incident.escalation_step, 1)
    return incident


def _current_step(policy: EscalationPolicy, step_index: int) -> dict[str, Any] | None:
    if step_index < 0 or step_index >= len(policy.steps):
        return None
    return policy.steps[step_index]


def _resolve_step_user(db: Session, step: dict[str, Any], incident: Incident) -> User | None:
    target_type = step.get("target_type")
    target_id = step.get("target_id")
    if not target_type or not target_id:
        return None
    if target_type == "user":
        return db.get(User, target_id)
    if target_type == "schedule":
        schedule = db.get(Schedule, target_id)
        if not schedule:
            return None
        user_id = resolve_on_call_user(schedule, incident.created_at)
        return db.get(User, user_id) if user_id else None
    return None

