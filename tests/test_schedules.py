from datetime import datetime
from uuid import uuid4

import pytest

from pagerbuddy.models import Schedule
from pagerbuddy.schedules import add_override, detect_schedule_gaps, resolve_on_call_user


class DummyDb:
    def add(self, _value):
        return None


def test_daily_rotation_and_override_take_precedence():
    first = uuid4()
    second = uuid4()
    override = uuid4()
    schedule = Schedule(
        name="Primary",
        timezone="America/Toronto",
        layers=[
            {
                "users": [str(first), str(second)],
                "rotation_type": "daily",
                "starts_at": "2026-05-25T09:00:00-04:00",
            }
        ],
        overrides=[
            {
                "override_user_id": str(override),
                "start": "2026-05-26T08:00:00-04:00",
                "end": "2026-05-26T12:00:00-04:00",
                "created_by": str(first),
            }
        ],
    )

    assert resolve_on_call_user(schedule, datetime.fromisoformat("2026-05-25T10:00:00-04:00")) == first
    assert resolve_on_call_user(schedule, datetime.fromisoformat("2026-05-26T13:00:00-04:00")) == second
    assert resolve_on_call_user(schedule, datetime.fromisoformat("2026-05-26T10:00:00-04:00")) == override


def test_add_override_rejects_overlap():
    user = uuid4()
    schedule = Schedule(
        name="Primary",
        timezone="UTC",
        layers=[],
        overrides=[
            {
                "override_user_id": str(user),
                "start": "2026-05-26T08:00:00+00:00",
                "end": "2026-05-26T12:00:00+00:00",
                "created_by": str(user),
            }
        ],
    )

    with pytest.raises(ValueError, match="overlaps"):
        add_override(
            DummyDb(),
            schedule,
            {
                "override_user_id": user,
                "start": datetime.fromisoformat("2026-05-26T11:00:00+00:00"),
                "end": datetime.fromisoformat("2026-05-26T13:00:00+00:00"),
                "created_by": user,
            },
        )


def test_gap_detection_reports_uncovered_windows():
    schedule = Schedule(name="Empty", timezone="UTC", layers=[], overrides=[])
    gaps = detect_schedule_gaps(
        schedule,
        start=datetime.fromisoformat("2026-05-26T00:00:00+00:00"),
        days=1,
        step_minutes=60,
    )

    assert len(gaps) == 1
    assert gaps[0].start.isoformat() == "2026-05-26T00:00:00+00:00"
    assert gaps[0].end.isoformat() == "2026-05-27T00:00:00+00:00"

