import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from pagerbuddy.models import IncidentPriority, IncidentStatus, UserRole

NOTIFICATION_CHANNELS = {"phone_call", "sms", "email"}


def validate_notification_preferences(value: dict[str, Any]) -> dict[str, Any]:
    if "channels" not in value:
        return value
    channels = value["channels"]
    if not isinstance(channels, list) or not channels:
        raise ValueError("notification_preferences.channels must include at least one channel")
    invalid = sorted({channel for channel in channels if channel not in NOTIFICATION_CHANNELS})
    if invalid:
        raise ValueError(f"unsupported notification channel(s): {', '.join(invalid)}")
    return {**value, "channels": channels}


class UserBase(BaseModel):
    name: str
    email: EmailStr
    phone_number: str
    timezone: str = "UTC"
    role: UserRole = UserRole.responder
    is_active: bool = True
    notification_preferences: dict[str, Any] = Field(
        default_factory=lambda: {"channels": ["phone_call", "sms", "email"]}
    )

    @field_validator("notification_preferences")
    @classmethod
    def validate_channels(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_notification_preferences(value)


class UserCreate(UserBase):
    password: str | None = Field(default=None, min_length=8)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class UserUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    phone_number: str | None = None
    timezone: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)
    notification_preferences: dict[str, Any] | None = None

    @field_validator("notification_preferences")
    @classmethod
    def validate_channels(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_notification_preferences(value) if value is not None else None


class AuthPrincipalRead(BaseModel):
    username: str
    role: UserRole
    user_id: str | None = None
    source: str


class EscalationPolicyCreate(BaseModel):
    name: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    repeat_enabled: bool = False
    repeat_count: int = 0
    catchall_user_id: uuid.UUID | None = None


class EscalationPolicyRead(EscalationPolicyCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class EscalationPolicyUpdate(BaseModel):
    name: str | None = None
    steps: list[dict[str, Any]] | None = None
    repeat_enabled: bool | None = None
    repeat_count: int | None = None
    catchall_user_id: uuid.UUID | None = None


class ServiceCreate(BaseModel):
    name: str
    description: str = ""
    escalation_policy_id: uuid.UUID
    inbound_phone_number: str


class ServiceRead(ServiceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    escalation_policy_id: uuid.UUID | None = None
    inbound_phone_number: str | None = None


class ScheduleCreate(BaseModel):
    name: str
    timezone: str = "UTC"
    layers: list[dict[str, Any]] = Field(default_factory=list)
    overrides: list[dict[str, Any]] = Field(default_factory=list)


class ScheduleRead(ScheduleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class ScheduleUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None
    layers: list[dict[str, Any]] | None = None
    overrides: list[dict[str, Any]] | None = None


class OverrideCreate(BaseModel):
    override_user_id: uuid.UUID
    start: datetime
    end: datetime
    created_by: uuid.UUID
    reason: str = ""


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    status: IncidentStatus
    priority: IncidentPriority
    title: str
    caller_id: str | None
    recording_url: str | None
    transcription: str | None
    assigned_user_id: uuid.UUID | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    merged_into_incident_id: uuid.UUID | None
    escalation_step: int
    escalation_cycle: int
    attempts_in_step: int
    next_escalation_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IncidentCreate(BaseModel):
    service_id: uuid.UUID
    title: str = "Manual incident"
    priority: IncidentPriority = IncidentPriority.P2
    caller_id: str | None = None
    recording_url: str | None = None
    transcription: str | None = None
    start_escalation: bool = True


class IncidentUpdate(BaseModel):
    priority: IncidentPriority | None = None
    title: str | None = None


class NoteCreate(BaseModel):
    author_id: uuid.UUID
    body: str
    status_update: bool = False


class ReassignCreate(BaseModel):
    actor_id: uuid.UUID
    assignee_id: uuid.UUID


class AckResolveCreate(BaseModel):
    user_id: uuid.UUID
    channel: str = "api"


class MergeCreate(BaseModel):
    actor_id: uuid.UUID
    child_incident_ids: list[uuid.UUID]
