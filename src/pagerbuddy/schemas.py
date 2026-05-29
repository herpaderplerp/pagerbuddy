import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from pagerbuddy.models import IncidentPriority, IncidentStatus, UserRole


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    phone_number: str
    timezone: str = "UTC"
    role: UserRole = UserRole.responder
    notification_preferences: dict[str, Any] = Field(
        default_factory=lambda: {"channels": ["phone_call", "sms", "email"]}
    )


class UserRead(UserCreate):
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
    notification_preferences: dict[str, Any] | None = None


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
