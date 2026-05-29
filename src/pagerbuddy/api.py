import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pagerbuddy import incidents as incident_service
from pagerbuddy import schemas
from pagerbuddy.database import get_db
from pagerbuddy.escalation import start_escalation
from pagerbuddy.models import (
    EscalationPolicy,
    IncidentActionToken,
    Incident,
    IncidentStatus,
    IncidentTimeline,
    Schedule,
    Service,
    StakeholderSubscription,
    TimelineEventType,
    User,
    UserRole,
)
from pagerbuddy.notifications import dispatch_notification
from pagerbuddy.schedules import add_override, detect_schedule_gaps
from pagerbuddy.timeline import record_event

router = APIRouter()


def _get_or_404(db: Session, model, item_id: uuid.UUID):
    item = db.get(model, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return item


def _delete_or_409(db: Session, item) -> None:
    db.delete(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cannot delete this record because it is still referenced") from exc


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/users", response_model=schemas.UserRead, status_code=201)
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db)) -> User:
    user = User(**payload.model_dump())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/users", response_model=list[schemas.UserRead])
def list_users(db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User)).all())


@router.patch("/users/{user_id}", response_model=schemas.UserRead)
def update_user(user_id: uuid.UUID, payload: schemas.UserUpdate, db: Session = Depends(get_db)) -> User:
    user = _get_or_404(db, User, user_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    user = _get_or_404(db, User, user_id)
    _delete_or_409(db, user)


@router.post("/escalation-policies", response_model=schemas.EscalationPolicyRead, status_code=201)
def create_policy(payload: schemas.EscalationPolicyCreate, db: Session = Depends(get_db)) -> EscalationPolicy:
    policy = EscalationPolicy(**payload.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/escalation-policies", response_model=list[schemas.EscalationPolicyRead])
def list_policies(db: Session = Depends(get_db)) -> list[EscalationPolicy]:
    return list(db.scalars(select(EscalationPolicy)).all())


@router.patch("/escalation-policies/{policy_id}", response_model=schemas.EscalationPolicyRead)
def update_policy(
    policy_id: uuid.UUID,
    payload: schemas.EscalationPolicyUpdate,
    db: Session = Depends(get_db),
) -> EscalationPolicy:
    policy = _get_or_404(db, EscalationPolicy, policy_id)
    if payload.catchall_user_id is not None:
        _get_or_404(db, User, payload.catchall_user_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(policy, key, value)
    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/escalation-policies/{policy_id}", status_code=204)
def delete_policy(policy_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    policy = _get_or_404(db, EscalationPolicy, policy_id)
    _delete_or_409(db, policy)


@router.post("/services", response_model=schemas.ServiceRead, status_code=201)
def create_service(payload: schemas.ServiceCreate, db: Session = Depends(get_db)) -> Service:
    _get_or_404(db, EscalationPolicy, payload.escalation_policy_id)
    service = Service(**payload.model_dump())
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


@router.get("/services", response_model=list[schemas.ServiceRead])
def list_services(db: Session = Depends(get_db)) -> list[Service]:
    return list(db.scalars(select(Service)).all())


@router.patch("/services/{service_id}", response_model=schemas.ServiceRead)
def update_service(service_id: uuid.UUID, payload: schemas.ServiceUpdate, db: Session = Depends(get_db)) -> Service:
    service = _get_or_404(db, Service, service_id)
    if payload.escalation_policy_id is not None:
        _get_or_404(db, EscalationPolicy, payload.escalation_policy_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(service, key, value)
    db.commit()
    db.refresh(service)
    return service


@router.delete("/services/{service_id}", status_code=204)
def delete_service(service_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    service = _get_or_404(db, Service, service_id)
    _delete_or_409(db, service)


@router.post("/schedules", response_model=schemas.ScheduleRead, status_code=201)
def create_schedule(payload: schemas.ScheduleCreate, db: Session = Depends(get_db)) -> Schedule:
    schedule = Schedule(**payload.model_dump())
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.get("/schedules", response_model=list[schemas.ScheduleRead])
def list_schedules(db: Session = Depends(get_db)) -> list[Schedule]:
    return list(db.scalars(select(Schedule)).all())


@router.patch("/schedules/{schedule_id}", response_model=schemas.ScheduleRead)
def update_schedule(schedule_id: uuid.UUID, payload: schemas.ScheduleUpdate, db: Session = Depends(get_db)) -> Schedule:
    schedule = _get_or_404(db, Schedule, schedule_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(schedule, key, value)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    schedule = _get_or_404(db, Schedule, schedule_id)
    _delete_or_409(db, schedule)


@router.post("/schedules/{schedule_id}/overrides", response_model=schemas.ScheduleRead)
def create_override(
    schedule_id: uuid.UUID,
    payload: schemas.OverrideCreate,
    db: Session = Depends(get_db),
) -> Schedule:
    schedule = _get_or_404(db, Schedule, schedule_id)
    _get_or_404(db, User, payload.override_user_id)
    _get_or_404(db, User, payload.created_by)
    try:
        add_override(db, schedule, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(schedule)
    return schedule


@router.get("/schedules/{schedule_id}/gaps")
def get_schedule_gaps(schedule_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict[str, str]]:
    schedule = _get_or_404(db, Schedule, schedule_id)
    return [{"start": gap.start.isoformat(), "end": gap.end.isoformat()} for gap in detect_schedule_gaps(schedule)]


@router.post("/services/{service_id}/stakeholders/{user_id}", status_code=204)
def subscribe_stakeholder(service_id: uuid.UUID, user_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    _get_or_404(db, Service, service_id)
    user = _get_or_404(db, User, user_id)
    if user.role != UserRole.stakeholder:
        raise HTTPException(status_code=400, detail="only stakeholder users can subscribe as stakeholders")
    db.add(StakeholderSubscription(service_id=service_id, user_id=user_id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="stakeholder is already subscribed to this service") from exc


@router.delete("/services/{service_id}/stakeholders/{user_id}", status_code=204)
def unsubscribe_stakeholder(service_id: uuid.UUID, user_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    subscription = db.scalar(
        select(StakeholderSubscription).where(
            StakeholderSubscription.service_id == service_id,
            StakeholderSubscription.user_id == user_id,
        )
    )
    if subscription is None:
        raise HTTPException(status_code=404, detail="stakeholder subscription not found")
    db.delete(subscription)
    db.commit()


@router.get("/incidents", response_model=list[schemas.IncidentRead])
def list_incidents(db: Session = Depends(get_db)) -> list[Incident]:
    return list(db.scalars(select(Incident).order_by(Incident.created_at.desc())).all())


@router.post("/incidents", response_model=schemas.IncidentRead, status_code=201)
def create_incident(payload: schemas.IncidentCreate, db: Session = Depends(get_db)) -> Incident:
    service = _get_or_404(db, Service, payload.service_id)
    incident = Incident(
        service_id=service.id,
        title=payload.title,
        priority=payload.priority,
        caller_id=payload.caller_id,
        recording_url=payload.recording_url,
        transcription=payload.transcription,
    )
    db.add(incident)
    db.flush()
    record_event(
        db,
        incident.id,
        TimelineEventType.incident_triggered,
        {"manual": True, "service_id": str(service.id), "caller_id": payload.caller_id},
        actor="admin",
    )
    if payload.start_escalation:
        start_escalation(db, incident)
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/incidents/{incident_id}", response_model=schemas.IncidentRead)
def get_incident(incident_id: uuid.UUID, db: Session = Depends(get_db)) -> Incident:
    return _get_or_404(db, Incident, incident_id)


@router.patch("/incidents/{incident_id}", response_model=schemas.IncidentRead)
def update_incident(
    incident_id: uuid.UUID,
    payload: schemas.IncidentUpdate,
    db: Session = Depends(get_db),
) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(incident, key, value)
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/incidents/{incident_id}/start-escalation", response_model=schemas.IncidentRead)
def start_incident_escalation(incident_id: uuid.UUID, db: Session = Depends(get_db)) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    start_escalation(db, incident)
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/incidents/{incident_id}/acknowledge", response_model=schemas.IncidentRead)
def acknowledge_incident(
    incident_id: uuid.UUID,
    payload: schemas.AckResolveCreate,
    db: Session = Depends(get_db),
) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    user = _get_or_404(db, User, payload.user_id)
    incident_service.acknowledge_incident(db, incident, user, payload.channel)
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/incidents/{incident_id}/acknowledge-link", response_model=schemas.IncidentRead)
def acknowledge_link(incident_id: uuid.UUID, user_id: uuid.UUID, db: Session = Depends(get_db)) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    user = _get_or_404(db, User, user_id)
    incident_service.acknowledge_incident(db, incident, user, "email")
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/incidents/{incident_id}/resolve", response_model=schemas.IncidentRead)
def resolve_incident(
    incident_id: uuid.UUID,
    payload: schemas.AckResolveCreate,
    db: Session = Depends(get_db),
) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    user = _get_or_404(db, User, payload.user_id)
    incident_service.resolve_incident(db, incident, user, payload.channel)
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/incidents/{incident_id}/resolve-link", response_model=schemas.IncidentRead)
def resolve_link(incident_id: uuid.UUID, user_id: uuid.UUID, db: Session = Depends(get_db)) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    user = _get_or_404(db, User, user_id)
    incident_service.resolve_incident(db, incident, user, "email")
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/incident-actions/{token}", response_model=schemas.IncidentRead)
def consume_incident_action(token: str, db: Session = Depends(get_db)) -> Incident:
    action_token = db.scalar(select(IncidentActionToken).where(IncidentActionToken.token == token))
    if action_token is None:
        raise HTTPException(status_code=404, detail="action token not found")
    if action_token.used_at is not None:
        raise HTTPException(status_code=409, detail="action token has already been used")
    incident = _get_or_404(db, Incident, action_token.incident_id)
    if incident.status in {IncidentStatus.resolved, IncidentStatus.merged}:
        raise HTTPException(status_code=410, detail="action token expired because the incident is closed")
    user = _get_or_404(db, User, action_token.user_id)
    if action_token.action == "acknowledge":
        incident_service.acknowledge_incident(db, incident, user, "email")
    elif action_token.action == "resolve":
        incident_service.resolve_incident(db, incident, user, "email")
    else:
        raise HTTPException(status_code=400, detail="unsupported token action")
    from pagerbuddy.models import utcnow

    action_token.used_at = utcnow()
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/incidents/{incident_id}/reopen", response_model=schemas.IncidentRead)
def reopen_incident(
    incident_id: uuid.UUID,
    payload: schemas.AckResolveCreate,
    db: Session = Depends(get_db),
) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    actor = _get_or_404(db, User, payload.user_id)
    incident_service.reopen_incident(db, incident, actor)
    start_escalation(db, incident)
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/incidents/{incident_id}/reassign", response_model=schemas.IncidentRead)
def reassign_incident(
    incident_id: uuid.UUID,
    payload: schemas.ReassignCreate,
    db: Session = Depends(get_db),
) -> Incident:
    incident = _get_or_404(db, Incident, incident_id)
    actor = _get_or_404(db, User, payload.actor_id)
    assignee = _get_or_404(db, User, payload.assignee_id)
    incident_service.reassign_incident(db, incident, actor, assignee)
    dispatch_notification(db, incident, assignee, incident.escalation_step, incident.attempts_in_step + 1)
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/incidents/{incident_id}/notes", status_code=204)
def add_incident_note(
    incident_id: uuid.UUID,
    payload: schemas.NoteCreate,
    db: Session = Depends(get_db),
) -> None:
    incident = _get_or_404(db, Incident, incident_id)
    author = _get_or_404(db, User, payload.author_id)
    incident_service.add_note(db, incident, author, payload.body, payload.status_update)
    db.commit()


@router.post("/incidents/{incident_id}/merge", response_model=schemas.IncidentRead)
def merge_incidents(
    incident_id: uuid.UUID,
    payload: schemas.MergeCreate,
    db: Session = Depends(get_db),
) -> Incident:
    parent = _get_or_404(db, Incident, incident_id)
    actor = _get_or_404(db, User, payload.actor_id)
    children = [_get_or_404(db, Incident, child_id) for child_id in payload.child_incident_ids]
    incident_service.merge_incidents(db, parent, children, actor)
    db.commit()
    db.refresh(parent)
    return parent


@router.get("/incidents/{incident_id}/timeline")
def incident_timeline(incident_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    _get_or_404(db, Incident, incident_id)
    events = db.scalars(
        select(IncidentTimeline).where(IncidentTimeline.incident_id == incident_id).order_by(IncidentTimeline.occurred_at)
    ).all()
    return [
        {
            "id": str(event.id),
            "event_type": event.event_type.value,
            "actor": event.actor,
            "payload": event.payload,
            "occurred_at": event.occurred_at.isoformat(),
        }
        for event in events
    ]
