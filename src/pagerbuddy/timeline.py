import uuid
from typing import Any

from sqlalchemy.orm import Session

from pagerbuddy.models import IncidentTimeline, TimelineEventType


def record_event(
    db: Session,
    incident_id: uuid.UUID,
    event_type: TimelineEventType,
    payload: dict[str, Any] | None = None,
    actor: str = "system",
) -> IncidentTimeline:
    event = IncidentTimeline(
        incident_id=incident_id,
        event_type=event_type,
        actor=actor,
        payload=payload or {},
    )
    db.add(event)
    return event

