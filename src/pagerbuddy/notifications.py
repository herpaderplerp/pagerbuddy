import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from pagerbuddy.config import Settings, get_settings
from pagerbuddy.models import (
    Incident,
    IncidentActionToken,
    NotificationAttempt,
    NotificationChannel,
    NotificationStatus,
    TimelineEventType,
    User,
)
from pagerbuddy.timeline import record_event


class ProviderResult(Protocol):
    provider_message_id: str | None


class SendResult:
    def __init__(self, provider_message_id: str | None = None):
        self.provider_message_id = provider_message_id


def preferred_channels(user: User, incident: Incident) -> list[NotificationChannel]:
    prefs = user.notification_preferences or {}
    quiet = prefs.get("quiet_hours") or {}
    raw_channels = prefs.get("channels") or ["phone_call", "sms", "email"]
    channels = [NotificationChannel(channel) for channel in raw_channels]

    if quiet.get("enabled") and incident.priority.value not in quiet.get("override_for_priority", ["P1", "P2"]):
        user_tz = ZoneInfo(user.timezone)
        now = datetime.now(timezone.utc).astimezone(user_tz).time()
        start = datetime.strptime(quiet.get("start", "22:00"), "%H:%M").time()
        end = datetime.strptime(quiet.get("end", "07:00"), "%H:%M").time()
        in_quiet_hours = start <= now <= end if start <= end else now >= start or now <= end
        if in_quiet_hours:
            return [channel for channel in channels if channel == NotificationChannel.email]
    return channels


def sms_body(incident: Incident) -> str:
    transcription = incident.transcription or f"Transcription pending - voicemail: {incident.recording_url or 'unavailable'}"
    excerpt = transcription[:160]
    return (
        f"[{incident.service.name}] [{incident.priority.value}] Incident triggered.\n"
        f"From: {incident.caller_id or 'Unknown'}\n"
        f"Voicemail: {excerpt}\n"
        "Reply ACK to acknowledge. Reply RESOLVE to resolve."
    )


def email_subject(incident: Incident) -> str:
    return f"[{incident.priority.value}] Incident Triggered - {incident.service.name} - {incident.id}"


def transcription_followup_sms_body(incident: Incident) -> str:
    transcription = incident.transcription or "No transcription text was provided."
    return (
        f"[{incident.service.name}] Transcription received for {incident.priority.value} incident {incident.id}.\n"
        f"{transcription[:240]}\n"
        "Reply ACK to acknowledge. Reply RESOLVE to resolve."
    )


def transcription_followup_email_subject(incident: Incident) -> str:
    return f"[{incident.priority.value}] Voicemail Transcription - {incident.service.name} - {incident.id}"


def transcription_followup_email_body(incident: Incident) -> str:
    return "\n".join(
        [
            f"Service: {incident.service.name}",
            f"Priority: {incident.priority.value}",
            f"Incident: {incident.id}",
            f"Caller: {incident.caller_id or 'Unknown'}",
            "",
            "Voicemail transcription:",
            incident.transcription or "No transcription text was provided.",
        ]
    )


def email_body(
    settings: Settings,
    incident: Incident,
    user: User,
    step: int,
    ack_url: str | None = None,
    resolve_url: str | None = None,
) -> str:
    transcription = incident.transcription or f"Transcription pending - voicemail available at: {incident.recording_url}"
    lines = [
        f"Service: {incident.service.name}",
        f"Priority: {incident.priority.value}",
        f"Incident: {incident.id}",
        f"Timestamp: {incident.created_at.isoformat()}",
        f"Caller: {incident.caller_id or 'Unknown'}",
        "",
        "Voicemail transcription:",
        transcription or "",
    ]
    if ack_url and resolve_url:
        lines.extend(["", f"Acknowledge: {ack_url}", f"Resolve: {resolve_url}", ""])
        lines.append(f"You are being paged as escalation step {step + 1}.")
    return "\n".join(lines)


class NotificationClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _validate_trial_recipient(self, user: User) -> None:
        allowed = self.settings.twilio_trial_allowed_number
        if allowed and user.phone_number != allowed:
            raise ValueError(f"Twilio trial mode only allows notifications to {allowed}")

    def send_phone_call(self, incident: Incident, user: User, attempt_id: uuid.UUID) -> SendResult:
        self._validate_trial_recipient(user)
        if not (self.settings.twilio_account_sid and self.settings.twilio_auth_token and self.settings.twilio_from_number):
            return SendResult("dry-run-phone-call")
        from twilio.rest import Client

        twiml_url = (
            f"{self.settings.public_base_url}/webhooks/twilio/outbound-response"
            f"?incident_id={incident.id}&user_id={user.id}&attempt_id={attempt_id}"
        )
        status_url = f"{self.settings.public_base_url}/webhooks/twilio/status"
        client = Client(self.settings.twilio_account_sid, self.settings.twilio_auth_token)
        call = client.calls.create(
            to=user.phone_number,
            from_=self.settings.twilio_from_number,
            url=twiml_url,
            status_callback=status_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        return SendResult(call.sid)

    def send_sms(self, incident: Incident, user: User) -> SendResult:
        return self.send_sms_text(incident, user, sms_body(incident))

    def send_sms_text(self, incident: Incident, user: User, body: str) -> SendResult:
        self._validate_trial_recipient(user)
        if not (self.settings.twilio_account_sid and self.settings.twilio_auth_token and self.settings.twilio_from_number):
            return SendResult("dry-run-sms")
        from twilio.rest import Client

        client = Client(self.settings.twilio_account_sid, self.settings.twilio_auth_token)
        message = client.messages.create(
            to=user.phone_number,
            from_=self.settings.twilio_from_number,
            body=body,
            status_callback=f"{self.settings.public_base_url}/webhooks/twilio/status",
        )
        return SendResult(message.sid)

    def send_email(
        self,
        incident: Incident,
        user: User,
        step: int,
        ack_url: str | None = None,
        resolve_url: str | None = None,
    ) -> SendResult:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from
        message["To"] = user.email
        message["Subject"] = email_subject(incident)
        message.set_content(email_body(self.settings, incident, user, step, ack_url, resolve_url))

        if self.settings.smtp_host == "dry-run":
            return SendResult("dry-run-email")
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as smtp:
            if self.settings.smtp_username and self.settings.smtp_password:
                smtp.starttls()
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(message)
        return SendResult(None)

    def send_email_text(self, incident: Incident, user: User, subject: str, body: str) -> SendResult:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from
        message["To"] = user.email
        message["Subject"] = subject
        message.set_content(body)

        if self.settings.smtp_host == "dry-run":
            return SendResult("dry-run-email")
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as smtp:
            if self.settings.smtp_username and self.settings.smtp_password:
                smtp.starttls()
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(message)
        return SendResult(None)


def dispatch_notification(
    db: Session,
    incident: Incident,
    user: User,
    escalation_step: int,
    attempt_number: int,
    client: NotificationClient | None = None,
) -> NotificationAttempt:
    channels = preferred_channels(user, incident)
    if not channels:
        raise ValueError(f"user {user.id} has no usable notification channels")
    channel = channels[(attempt_number - 1) % len(channels)]
    attempt = NotificationAttempt(
        incident_id=incident.id,
        user_id=user.id,
        channel=channel,
        attempt_number=attempt_number,
        escalation_step=escalation_step,
    )
    db.add(attempt)
    db.flush()

    notifier = client or NotificationClient()
    try:
        if channel == NotificationChannel.phone_call:
            result = notifier.send_phone_call(incident, user, attempt.id)
        elif channel == NotificationChannel.sms:
            result = notifier.send_sms(incident, user)
        else:
            ack_token = IncidentActionToken(incident_id=incident.id, user_id=user.id, action="acknowledge")
            resolve_token = IncidentActionToken(incident_id=incident.id, user_id=user.id, action="resolve")
            db.add_all([ack_token, resolve_token])
            db.flush()
            result = notifier.send_email(
                incident,
                user,
                escalation_step,
                ack_url=f"{notifier.settings.public_base_url}/incident-actions/{ack_token.token}",
                resolve_url=f"{notifier.settings.public_base_url}/incident-actions/{resolve_token.token}",
            )
        attempt.status = NotificationStatus.delivered
        attempt.provider_message_id = result.provider_message_id
        record_event(
            db,
            incident.id,
            TimelineEventType.notification_sent,
            {
                "user_id": str(user.id),
                "channel": channel.value,
                "attempt_number": attempt_number,
                "escalation_step": escalation_step,
            },
        )
    except Exception as exc:
        attempt.status = NotificationStatus.failed
        attempt.error = str(exc)
        record_event(
            db,
            incident.id,
            TimelineEventType.notification_failed,
            {
                "user_id": str(user.id),
                "channel": channel.value,
                "attempt_number": attempt_number,
                "escalation_step": escalation_step,
                "error": str(exc),
            },
        )
    return attempt


def dispatch_transcription_followup(
    db: Session,
    incident: Incident,
    client: NotificationClient | None = None,
) -> list[NotificationAttempt]:
    user = _current_notified_user(db, incident)
    if user is None or not incident.transcription:
        return []

    notifier = client or NotificationClient()
    channels = [channel for channel in preferred_channels(user, incident) if channel in {NotificationChannel.sms, NotificationChannel.email}]
    if not channels:
        channels = [NotificationChannel.email]

    attempts: list[NotificationAttempt] = []
    for channel in channels:
        attempt_number = _next_attempt_number(db, incident, user, channel)
        attempt = NotificationAttempt(
            incident_id=incident.id,
            user_id=user.id,
            channel=channel,
            attempt_number=attempt_number,
            escalation_step=incident.escalation_step,
        )
        db.add(attempt)
        db.flush()
        try:
            if channel == NotificationChannel.sms:
                result = notifier.send_sms_text(incident, user, transcription_followup_sms_body(incident))
            else:
                result = notifier.send_email_text(
                    incident,
                    user,
                    transcription_followup_email_subject(incident),
                    transcription_followup_email_body(incident),
                )
            attempt.status = NotificationStatus.delivered
            attempt.provider_message_id = result.provider_message_id
            record_event(
                db,
                incident.id,
                TimelineEventType.notification_sent,
                {
                    "user_id": str(user.id),
                    "channel": channel.value,
                    "attempt_number": attempt_number,
                    "escalation_step": incident.escalation_step,
                    "follow_up": "transcription_received",
                },
            )
        except Exception as exc:
            attempt.status = NotificationStatus.failed
            attempt.error = str(exc)
            record_event(
                db,
                incident.id,
                TimelineEventType.notification_failed,
                {
                    "user_id": str(user.id),
                    "channel": channel.value,
                    "attempt_number": attempt_number,
                    "escalation_step": incident.escalation_step,
                    "follow_up": "transcription_received",
                    "error": str(exc),
                },
            )
        attempts.append(attempt)
    return attempts


def _current_notified_user(db: Session, incident: Incident) -> User | None:
    if incident.assigned_user_id:
        return db.get(User, incident.assigned_user_id)

    latest_attempt = db.scalar(
        select(NotificationAttempt)
        .where(
            NotificationAttempt.incident_id == incident.id,
            NotificationAttempt.status.in_([NotificationStatus.pending, NotificationStatus.delivered]),
        )
        .order_by(desc(NotificationAttempt.sent_at))
    )
    return db.get(User, latest_attempt.user_id) if latest_attempt else None


def _next_attempt_number(db: Session, incident: Incident, user: User, channel: NotificationChannel) -> int:
    current = db.scalar(
        select(func.count(NotificationAttempt.id)).where(
            NotificationAttempt.incident_id == incident.id,
            NotificationAttempt.user_id == user.id,
            NotificationAttempt.channel == channel,
        )
    )
    return int(current or 0) + 1
