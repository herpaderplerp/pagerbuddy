import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from pagerbuddy.models import Schedule, TimelineEventType, User
from pagerbuddy.timeline import record_event


@dataclass(frozen=True)
class CoverageGap:
    start: datetime
    end: datetime


def _parse_datetime(value: str | datetime, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _parse_time(value: str) -> time:
    hour, minute, *rest = value.split(":")
    second = int(rest[0]) if rest else 0
    return time(int(hour), int(minute), second)


def _weekday_matches(weekday: int, allowed: list[Any] | None) -> bool:
    if not allowed:
        return True
    names = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    normalized = {names.get(str(day).lower(), day) for day in allowed}
    return weekday in {int(day) for day in normalized}


def _restriction_active(restriction: dict[str, Any], at: datetime) -> bool:
    if not _weekday_matches(at.weekday(), restriction.get("days")):
        return False
    start = _parse_time(restriction.get("start_time", "00:00"))
    end = _parse_time(restriction.get("end_time", "23:59:59"))
    current = at.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _layer_active(layer: dict[str, Any], at: datetime) -> bool:
    restrictions = layer.get("restrictions") or []
    return not restrictions or any(_restriction_active(restriction, at) for restriction in restrictions)


def _rotation_hours(layer: dict[str, Any]) -> int:
    rotation_type = layer.get("rotation_type", "weekly")
    if rotation_type == "daily":
        return 24
    if rotation_type == "weekly":
        return 24 * 7
    return int(layer.get("rotation_length_hours", 24))


def _active_layer_user(layer: dict[str, Any], at: datetime, schedule_tz: ZoneInfo) -> uuid.UUID | None:
    users = layer.get("users") or []
    if not users or not _layer_active(layer, at):
        return None
    starts_at = _parse_datetime(layer.get("starts_at", at.isoformat()), schedule_tz)
    elapsed_hours = max(0, int((at - starts_at).total_seconds() // 3600))
    index = (elapsed_hours // _rotation_hours(layer)) % len(users)
    return uuid.UUID(str(users[index]))


def resolve_on_call_user(schedule: Schedule, at: datetime | None = None) -> uuid.UUID | None:
    tz = ZoneInfo(schedule.timezone)
    when = (at or datetime.now(timezone.utc)).astimezone(tz)

    for override in schedule.overrides or []:
        start = _parse_datetime(override["start"], tz)
        end = _parse_datetime(override["end"], tz)
        if start <= when < end:
            return uuid.UUID(str(override["override_user_id"]))

    for layer in schedule.layers or []:
        user_id = _active_layer_user(layer, when, tz)
        if user_id:
            return user_id
    return None


def add_override(db: Session, schedule: Schedule, override: dict[str, Any]) -> Schedule:
    tz = ZoneInfo(schedule.timezone)
    new_start = _parse_datetime(override["start"], tz)
    new_end = _parse_datetime(override["end"], tz)
    if new_start >= new_end:
        raise ValueError("override start must be before end")

    for existing in schedule.overrides or []:
        existing_start = _parse_datetime(existing["start"], tz)
        existing_end = _parse_datetime(existing["end"], tz)
        overlaps = new_start < existing_end and new_end > existing_start
        if overlaps:
            raise ValueError("override overlaps an existing override")

    schedule.overrides = [
        *(schedule.overrides or []),
        {
            "override_user_id": str(override["override_user_id"]),
            "start": new_start.isoformat(),
            "end": new_end.isoformat(),
            "created_by": str(override["created_by"]),
            "reason": override.get("reason", ""),
        },
    ]
    db.add(schedule)
    return schedule


def _has_active_coverage(db: Session | None, schedule: Schedule, at: datetime) -> bool:
    user_id = resolve_on_call_user(schedule, at)
    if user_id is None:
        return False
    if db is None:
        return True
    user = db.get(User, user_id)
    return bool(user and user.is_active)


def detect_schedule_gaps(
    schedule: Schedule,
    start: datetime | None = None,
    days: int = 30,
    step_minutes: int = 60,
    db: Session | None = None,
) -> list[CoverageGap]:
    tz = ZoneInfo(schedule.timezone)
    cursor = (start or datetime.now(timezone.utc)).astimezone(tz)
    end = cursor + timedelta(days=days)
    step = timedelta(minutes=step_minutes)
    gaps: list[CoverageGap] = []
    active_gap_start: datetime | None = None

    while cursor < end:
        has_coverage = _has_active_coverage(db, schedule, cursor)
        if not has_coverage and active_gap_start is None:
            active_gap_start = cursor
        elif has_coverage and active_gap_start is not None:
            gaps.append(CoverageGap(active_gap_start, cursor))
            active_gap_start = None
        cursor += step

    if active_gap_start is not None:
        gaps.append(CoverageGap(active_gap_start, end))
    return gaps


def record_gap_events(db: Session, schedule: Schedule, incident_id: uuid.UUID, gaps: list[CoverageGap]) -> None:
    for gap in gaps:
        record_event(
            db,
            incident_id,
            TimelineEventType.schedule_gap_detected,
            {"schedule_id": str(schedule.id), "start": gap.start.isoformat(), "end": gap.end.isoformat()},
        )

