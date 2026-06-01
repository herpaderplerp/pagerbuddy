"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def guid() -> sa.TypeEngine:
    return postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite")


def json_type() -> sa.TypeEngine:
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    user_role = sa.Enum("admin", "responder", "stakeholder", name="userrole")
    incident_status = sa.Enum("triggered", "acknowledged", "resolved", "merged", name="incidentstatus")
    incident_priority = sa.Enum("P1", "P2", "P3", "P4", name="incidentpriority")
    notification_channel = sa.Enum("phone_call", "sms", "email", name="notificationchannel")
    notification_status = sa.Enum("pending", "delivered", "failed", "acknowledged", name="notificationstatus")
    timeline_event_type = sa.Enum(
        "incident_triggered",
        "recording_received",
        "transcription_received",
        "notification_sent",
        "notification_failed",
        "notification_acknowledged",
        "escalation_step_started",
        "escalation_exhausted",
        "incident_acknowledged",
        "incident_resolved",
        "incident_reassigned",
        "incident_note_added",
        "incident_merged",
        "incident_reopened",
        "override_created",
        "schedule_gap_detected",
        name="timelineeventtype",
    )

    op.create_table(
        "users",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("phone_number", sa.String(40), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notification_preferences", json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "escalation_policies",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("steps", json_type(), nullable=False),
        sa.Column("repeat_enabled", sa.Boolean(), nullable=False),
        sa.Column("repeat_count", sa.Integer(), nullable=False),
        sa.Column("catchall_user_id", guid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "services",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("escalation_policy_id", guid(), sa.ForeignKey("escalation_policies.id"), nullable=False),
        sa.Column("inbound_phone_number", sa.String(40), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "schedules",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("layers", json_type(), nullable=False),
        sa.Column("overrides", json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "incidents",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("service_id", guid(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("status", incident_status, nullable=False),
        sa.Column("priority", incident_priority, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("caller_id", sa.String(40), nullable=True),
        sa.Column("call_sid", sa.String(80), nullable=True),
        sa.Column("recording_sid", sa.String(80), nullable=True),
        sa.Column("recording_url", sa.Text(), nullable=True),
        sa.Column("transcription", sa.Text(), nullable=True),
        sa.Column("assigned_user_id", guid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_into_incident_id", guid(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("escalation_step", sa.Integer(), nullable=False),
        sa.Column("escalation_cycle", sa.Integer(), nullable=False),
        sa.Column("attempts_in_step", sa.Integer(), nullable=False),
        sa.Column("next_escalation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_incidents_call_sid"), "incidents", ["call_sid"])
    op.create_index(op.f("ix_incidents_recording_sid"), "incidents", ["recording_sid"])
    op.create_table(
        "incident_timeline",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("incident_id", guid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("event_type", timeline_event_type, nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("payload", json_type(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_attempts",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("incident_id", guid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("user_id", guid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel", notification_channel, nullable=False),
        sa.Column("status", notification_status, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("escalation_step", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_table(
        "stakeholder_subscriptions",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("service_id", guid(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("user_id", guid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("service_id", "user_id", name="uq_service_stakeholder"),
    )
    op.create_table(
        "incident_action_tokens",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("token", sa.String(96), nullable=False),
        sa.Column("incident_id", guid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("user_id", guid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_incident_action_tokens_token"), "incident_action_tokens", ["token"], unique=True)
    op.create_table(
        "system_events",
        sa.Column("id", guid(), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", json_type(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("system_events")
    op.drop_index(op.f("ix_incident_action_tokens_token"), table_name="incident_action_tokens")
    op.drop_table("incident_action_tokens")
    op.drop_table("stakeholder_subscriptions")
    op.drop_table("notification_attempts")
    op.drop_table("incident_timeline")
    op.drop_index(op.f("ix_incidents_recording_sid"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_call_sid"), table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("schedules")
    op.drop_table("services")
    op.drop_table("escalation_policies")
    op.drop_table("users")
    if bind.dialect.name == "postgresql":
        for enum_name in (
            "timelineeventtype",
            "notificationstatus",
            "notificationchannel",
            "incidentpriority",
            "incidentstatus",
            "userrole",
        ):
            sa.Enum(name=enum_name).drop(bind, checkfirst=True)
