import uuid
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from pagerbuddy.models import (
    Incident,
    IncidentPriority,
    IncidentStatus,
    NotificationAttempt,
    NotificationStatus,
    Service,
    StakeholderSubscription,
    TimelineEventType,
    User,
)
from pagerbuddy.notifications import NotificationClient, cancel_in_flight_phone_calls, dispatch_transcription_followup
from pagerbuddy.timeline import record_event


def create_incident_from_recording(
    db: Session,
    to_number: str,
    caller_id: str | None,
    call_sid: str | None,
    recording_sid: str | None,
    recording_url: str | None,
) -> Incident:
    service = db.scalar(select(Service).where(Service.inbound_phone_number == to_number))
    if service is None:
        raise LookupError(f"no service configured for Twilio number {to_number}")

    incident = Incident(
        service_id=service.id,
        status=IncidentStatus.triggered,
        priority=IncidentPriority.P2,
        title=f"Voicemail from {caller_id or 'unknown caller'}",
        caller_id=caller_id,
        call_sid=call_sid,
        recording_sid=recording_sid,
        recording_url=recording_url,
    )
    db.add(incident)
    db.flush()
    record_event(
        db,
        incident.id,
        TimelineEventType.incident_triggered,
        {"caller_id": caller_id, "service_id": str(service.id), "call_sid": call_sid},
    )
    record_event(
        db,
        incident.id,
        TimelineEventType.recording_received,
        {"recording_sid": recording_sid, "recording_url": recording_url},
    )
    return incident


def apply_transcription(
    db: Session,
    recording_sid: str | None,
    transcription: str,
    client: NotificationClient | None = None,
) -> Incident:
    incident = db.scalar(select(Incident).where(Incident.recording_sid == recording_sid))
    if incident is None:
        raise LookupError(f"no incident found for recording {recording_sid}")
    incident.transcription = transcription
    if transcription:
        incident.title = transcription[:120]
    record_event(db, incident.id, TimelineEventType.transcription_received, {"recording_sid": recording_sid})
    dispatch_transcription_followup(db, incident, client=client)
    return incident


def acknowledge_incident(
    db: Session,
    incident: Incident,
    user: User,
    channel: str,
    notification_client: NotificationClient | None = None,
) -> Incident:
    if incident.status == IncidentStatus.resolved:
        return incident
    now = datetime.now(timezone.utc)
    cancel_in_flight_phone_calls(db, incident, client=notification_client)
    incident.status = IncidentStatus.acknowledged
    incident.assigned_user_id = user.id
    incident.acknowledged_at = now
    incident.next_escalation_at = None
    db.query(NotificationAttempt).filter(
        NotificationAttempt.incident_id == incident.id,
        NotificationAttempt.status.in_([NotificationStatus.pending, NotificationStatus.delivered]),
    ).update({"status": NotificationStatus.acknowledged, "acked_at": now}, synchronize_session=False)
    record_event(db, incident.id, TimelineEventType.notification_acknowledged, {"user_id": str(user.id), "channel": channel})
    record_event(db, incident.id, TimelineEventType.incident_acknowledged, {"user_id": str(user.id), "channel": channel})
    return incident


def resolve_incident(db: Session, incident: Incident, user: User, channel: str) -> Incident:
    if incident.status == IncidentStatus.resolved:
        return incident
    incident.status = IncidentStatus.resolved
    incident.resolved_at = datetime.now(timezone.utc)
    incident.next_escalation_at = None
    record_event(db, incident.id, TimelineEventType.incident_resolved, {"user_id": str(user.id), "channel": channel})
    notify_stakeholders_resolved(db, incident)
    return incident


def reopen_incident(db: Session, incident: Incident, actor: User) -> Incident:
    if incident.status != IncidentStatus.acknowledged:
        raise ValueError("only acknowledged incidents can be reopened")
    incident.status = IncidentStatus.triggered
    incident.acknowledged_at = None
    incident.assigned_user_id = None
    incident.escalation_step = 0
    incident.escalation_cycle = 0
    incident.attempts_in_step = 0
    incident.next_escalation_at = None
    record_event(db, incident.id, TimelineEventType.incident_reopened, {"actor_id": str(actor.id)})
    return incident


def reassign_incident(db: Session, incident: Incident, actor: User, assignee: User) -> Incident:
    incident.assigned_user_id = assignee.id
    record_event(
        db,
        incident.id,
        TimelineEventType.incident_reassigned,
        {"actor_id": str(actor.id), "assignee_id": str(assignee.id)},
    )
    return incident


def add_note(db: Session, incident: Incident, author: User, body: str, status_update: bool = False) -> None:
    record_event(
        db,
        incident.id,
        TimelineEventType.incident_note_added,
        {"author_id": str(author.id), "body": body, "status_update": status_update},
        actor=str(author.id),
    )


def merge_incidents(db: Session, parent: Incident, children: list[Incident], actor: User) -> Incident:
    for child in children:
        if child.id == parent.id:
            continue
        child.status = IncidentStatus.merged
        child.merged_into_incident_id = parent.id
        child.next_escalation_at = None
        record_event(
            db,
            child.id,
            TimelineEventType.incident_merged,
            {"actor_id": str(actor.id), "parent_incident_id": str(parent.id)},
        )
    record_event(
        db,
        parent.id,
        TimelineEventType.incident_merged,
        {"actor_id": str(actor.id), "child_incident_ids": [str(child.id) for child in children]},
    )
    return parent


def find_sms_target_incident(db: Session, user: User, incident_id: uuid.UUID | None = None) -> Incident | None:
    if incident_id is not None:
        incident = db.get(Incident, incident_id)
        if incident is None or incident.status not in {IncidentStatus.triggered, IncidentStatus.acknowledged}:
            return None
        if incident.assigned_user_id == user.id:
            return incident
        attempted = db.scalar(
            select(NotificationAttempt)
            .where(
                NotificationAttempt.incident_id == incident.id,
                NotificationAttempt.user_id == user.id,
                NotificationAttempt.status.in_([NotificationStatus.pending, NotificationStatus.delivered]),
            )
            .limit(1)
        )
        return incident if attempted is not None else None

    assigned = db.scalars(
        select(Incident)
        .where(Incident.assigned_user_id == user.id, Incident.status.in_([IncidentStatus.triggered, IncidentStatus.acknowledged]))
        .order_by(desc(Incident.created_at))
    ).all()
    if len(assigned) == 1:
        return assigned[0]

    attempted = db.scalars(
        select(Incident)
        .join(NotificationAttempt, NotificationAttempt.incident_id == Incident.id)
        .where(
            NotificationAttempt.user_id == user.id,
            Incident.status == IncidentStatus.triggered,
            NotificationAttempt.status.in_([NotificationStatus.pending, NotificationStatus.delivered]),
        )
        .order_by(desc(Incident.created_at))
    ).unique().all()
    if len(attempted) == 1:
        return attempted[0]
    return None


def notify_stakeholders_triggered(db: Session, incident: Incident) -> None:
    _notify_stakeholders(db, incident, resolved=False)


def notify_stakeholders_resolved(db: Session, incident: Incident) -> None:
    _notify_stakeholders(db, incident, resolved=True)


def _notify_stakeholders(db: Session, incident: Incident, resolved: bool) -> None:
    client = NotificationClient()
    subscriptions = db.scalars(
        select(StakeholderSubscription).where(StakeholderSubscription.service_id == incident.service_id)
    ).all()
    for subscription in subscriptions:
        user = db.get(User, subscription.user_id)
        if not user:
            continue
        try:
            client.send_email(incident, user, step=-1)
            event_type = "resolved" if resolved else "triggered"
            record_event(
                db,
                incident.id,
                TimelineEventType.notification_sent,
                {"user_id": str(user.id), "channel": "email", "stakeholder": True, "event": event_type},
            )
        except Exception as exc:
            record_event(
                db,
                incident.id,
                TimelineEventType.notification_failed,
                {"user_id": str(user.id), "channel": "email", "stakeholder": True, "error": str(exc)},
            )
