import html
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from pagerbuddy.config import get_settings
from pagerbuddy.database import get_db
from pagerbuddy.escalation import manual_escalate, start_escalation
from pagerbuddy.incidents import (
    acknowledge_incident,
    apply_transcription,
    create_incident_from_recording,
    find_sms_target_incident,
    notify_stakeholders_triggered,
    resolve_incident,
)
from pagerbuddy.models import Incident, NotificationAttempt, NotificationStatus, SystemEvent, TimelineEventType, User
from pagerbuddy.recordings import RecordingDownloadError, download_recording, recording_media_url
from pagerbuddy.timeline import record_event
from pagerbuddy.transcription import LocalTranscriptionError, transcribe_recording

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])


def twiml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


def _caller_allowed(from_number: str | None) -> bool:
    settings = get_settings()
    if not settings.inbound_caller_whitelist_enabled:
        return True
    return from_number in settings.inbound_caller_whitelist


def _record_rejected_inbound_call(
    db: Session,
    to_number: str,
    from_number: str | None,
    call_sid: str | None,
    service_id: uuid.UUID | None,
) -> None:
    db.add(
        SystemEvent(
            event_type="inbound_call_rejected",
            payload={
                "reason": "caller_not_whitelisted",
                "to": to_number,
                "from": from_number,
                "call_sid": call_sid,
                "service_id": str(service_id) if service_id else None,
            },
        )
    )
    db.commit()


@router.post("/voice")
def inbound_voice(
    To: str = Form(...),
    From: str | None = Form(None),
    CallSid: str | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    from pagerbuddy.models import Service

    service = db.scalar(select(Service).where(Service.inbound_phone_number == To))
    if not _caller_allowed(From):
        _record_rejected_inbound_call(db, To, From, CallSid, service.id if service else None)
        return twiml("<Say>This phone number is not approved to open incidents.</Say><Hangup />")

    service_name = service.name if service else "the service"
    settings = get_settings()
    action = f"{settings.public_base_url}/webhooks/twilio/recording-complete"
    transcription = f"{settings.public_base_url}/webhooks/twilio/transcription-complete"
    message = (
        f"You have reached the {service_name} on-call line. "
        "Please leave a detailed message after the tone. Your call is being recorded."
    )
    if settings.local_transcription_enabled:
        record_options = f'<Record action="{html.escape(action)}" method="POST" transcribe="false" maxLength="600" />'
    else:
        record_options = (
            f'<Record action="{html.escape(action)}" method="POST" transcribe="true" '
            f'transcriptionCallback="{html.escape(transcription)}" maxLength="600" />'
        )
    return twiml(f"<Say>{html.escape(message)}</Say>{record_options}")


@router.post("/recording-complete")
def recording_complete(
    To: str = Form(...),
    From: str | None = Form(None),
    CallSid: str | None = Form(None),
    RecordingSid: str | None = Form(None),
    RecordingUrl: str | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    try:
        incident = create_incident_from_recording(db, To, From, CallSid, RecordingSid, RecordingUrl)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    settings = get_settings()
    local_path = None
    if settings.store_recordings_locally and RecordingUrl:
        try:
            local_path = download_recording(RecordingUrl, RecordingSid, settings)
            record_event(
                db,
                incident.id,
                TimelineEventType.recording_received,
                {
                    "recording_sid": RecordingSid,
                    "local_path": str(local_path),
                    "storage": "local",
                },
            )
        except RecordingDownloadError as exc:
            record_event(
                db,
                incident.id,
                TimelineEventType.notification_failed,
                {
                    "recording_sid": RecordingSid,
                    "recording_url": RecordingUrl,
                    "storage": "local",
                    "error": str(exc),
                },
            )
    if settings.local_transcription_enabled and local_path is not None:
        try:
            transcription = transcribe_recording(local_path, settings)
            if transcription:
                apply_transcription(db, RecordingSid, transcription)
            else:
                record_event(
                    db,
                    incident.id,
                    TimelineEventType.transcription_received,
                    {"recording_sid": RecordingSid, "source": "local", "empty": True},
                )
        except LocalTranscriptionError as exc:
            record_event(
                db,
                incident.id,
                TimelineEventType.notification_failed,
                {
                    "recording_sid": RecordingSid,
                    "storage": "local",
                    "follow_up": "local_transcription",
                    "error": str(exc),
                },
            )
    notify_stakeholders_triggered(db, incident)
    start_escalation(db, incident)
    db.commit()
    return twiml("<Say>Thank you. The on-call responder has been notified.</Say>")


@router.post("/transcription-complete")
def transcription_complete(
    RecordingSid: str | None = Form(None),
    TranscriptionText: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        incident = apply_transcription(db, RecordingSid, TranscriptionText)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return {"status": "ok", "incident_id": str(incident.id)}


def _outbound_intro(incident: Incident) -> str:
    return (
        f"Incident for {incident.service.name}. Priority {incident.priority.value}. "
        f"Caller {incident.caller_id or 'unknown caller'}. "
    )


def _outbound_action_prompt() -> str:
    return "Press 1 to acknowledge. Press 2 to escalate immediately. Press 9 to repeat this message."


def _outbound_message_twiml(incident: Incident) -> str:
    intro = f"<Say>{html.escape(_outbound_intro(incident))}</Say>"
    action_prompt = f'<Gather numDigits="1" timeout="10"><Say>{html.escape(_outbound_action_prompt())}</Say></Gather>'
    if incident.recording_url:
        return f"{intro}<Say>Playing voicemail now.</Say><Play>{html.escape(recording_media_url(incident.recording_url))}</Play>{action_prompt}"
    fallback = incident.transcription or "Voicemail recording is unavailable."
    return f"{intro}<Say>{html.escape(fallback[:900])}</Say>{action_prompt}"


def _parse_sms_command(body: str) -> tuple[str, uuid.UUID | None, str | None]:
    parts = body.strip().split()
    if not parts:
        return "", None, None
    command = parts[0].upper()
    if len(parts) == 1:
        return command, None, None
    raw_incident_id = parts[1].removeprefix("#")
    try:
        return command, uuid.UUID(raw_incident_id), None
    except ValueError:
        return command, None, "Incident ID must be a UUID."


@router.api_route("/outbound-response", methods=["GET", "POST"], include_in_schema=False)
def outbound_response(
    incident_id: uuid.UUID = Query(...),
    user_id: uuid.UUID = Query(...),
    Digits: str | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    incident = db.get(Incident, incident_id)
    user = db.get(User, user_id)
    if incident is None or user is None:
        return twiml("<Say>Incident not found.</Say>")
    if Digits == "1":
        acknowledge_incident(db, incident, user, "phone_call")
        db.commit()
        return twiml("<Say>Incident acknowledged. Goodbye.</Say><Hangup />")
    if Digits == "2":
        manual_escalate(db, incident, str(user.id), "phone_call")
        db.commit()
        return twiml("<Say>Incident escalated. Goodbye.</Say><Hangup />")
    return twiml(f"{_outbound_message_twiml(incident)}<Redirect method=\"POST\" />")


@router.post("/sms")
def inbound_sms(
    From: str = Form(...),
    Body: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    user = db.scalar(select(User).where(User.phone_number == From))
    if user is None:
        return twiml("<Message>No PagerBuddy user is registered for this phone number.</Message>")
    command, incident_id, parse_error = _parse_sms_command(Body)
    if command in {"ACK", "ACKNOWLEDGE"}:
        if parse_error:
            return twiml(f"<Message>{html.escape(parse_error)}</Message>")
        incident = find_sms_target_incident(db, user, incident_id)
        if incident is None:
            return twiml("<Message>Multiple or no open incidents found. Reply ACK &lt;incident ID&gt;.</Message>")
        acknowledge_incident(db, incident, user, "sms")
        db.commit()
        return twiml(f"<Message>Acknowledged incident {incident.id}.</Message>")
    if command == "RESOLVE":
        if parse_error:
            return twiml(f"<Message>{html.escape(parse_error)}</Message>")
        incident = find_sms_target_incident(db, user, incident_id)
        if incident is None:
            return twiml("<Message>Multiple or no open incidents found. Reply RESOLVE &lt;incident ID&gt;.</Message>")
        resolve_incident(db, incident, user, "sms")
        db.commit()
        return twiml(f"<Message>Resolved incident {incident.id}.</Message>")
    return twiml("<Message>Reply ACK &lt;incident ID&gt; to acknowledge or RESOLVE &lt;incident ID&gt; to resolve.</Message>")


@router.post("/status")
def notification_status(
    MessageSid: str | None = Form(None),
    MessageStatus: str | None = Form(None),
    CallSid: str | None = Form(None),
    CallStatus: str | None = Form(None),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    provider_id = MessageSid or CallSid
    provider_status = (MessageStatus or CallStatus or "").lower()
    if not provider_id:
        return {"status": "ignored"}

    attempt = db.scalar(select(NotificationAttempt).where(NotificationAttempt.provider_message_id == provider_id))
    if attempt is None:
        return {"status": "unknown_attempt"}

    failed_statuses = {"failed", "undelivered", "busy", "no-answer", "canceled"}
    delivered_statuses = {"sent", "delivered", "answered", "completed"}
    if provider_status in failed_statuses:
        attempt.status = NotificationStatus.failed
        attempt.error = provider_status
        record_event(
            db,
            attempt.incident_id,
            TimelineEventType.notification_failed,
            {
                "user_id": str(attempt.user_id),
                "channel": attempt.channel.value,
                "attempt_number": attempt.attempt_number,
                "provider_status": provider_status,
            },
        )
    elif provider_status in delivered_statuses:
        attempt.status = NotificationStatus.delivered
    db.commit()
    return {"status": "ok"}
