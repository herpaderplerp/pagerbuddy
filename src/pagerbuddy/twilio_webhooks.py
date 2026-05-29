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
from pagerbuddy.models import Incident, NotificationAttempt, NotificationStatus, TimelineEventType, User
from pagerbuddy.timeline import record_event

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])


def twiml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


@router.post("/voice")
def inbound_voice(
    To: str = Form(...),
    From: str | None = Form(None),
    CallSid: str | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    from pagerbuddy.models import Service

    service = db.scalar(select(Service).where(Service.inbound_phone_number == To))
    service_name = service.name if service else "the service"
    settings = get_settings()
    action = f"{settings.public_base_url}/webhooks/twilio/recording-complete"
    transcription = f"{settings.public_base_url}/webhooks/twilio/transcription-complete"
    message = (
        f"You have reached the {service_name} on-call line. "
        "Please leave a detailed message after the tone. Your call is being recorded."
    )
    return twiml(
        f"<Say>{html.escape(message)}</Say>"
        f'<Record action="{html.escape(action)}" method="POST" transcribe="true" '
        f'transcriptionCallback="{html.escape(transcription)}" maxLength="600" />'
    )


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


def _outbound_prompt(incident: Incident) -> str:
    transcription = incident.transcription or f"Transcription pending. Voicemail is available at {incident.recording_url or 'the recording URL'}."
    return (
        f"Incident for {incident.service.name}. Priority {incident.priority.value}. "
        f"Caller {incident.caller_id or 'unknown'}. {transcription[:500]}. "
        "Press 1 to acknowledge. Press 2 to escalate immediately. Press 9 to repeat this message."
    )


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
    prompt = html.escape(_outbound_prompt(incident))
    return twiml(f'<Gather numDigits="1" timeout="10"><Say>{prompt}</Say></Gather><Redirect method="POST" />')


@router.post("/sms")
def inbound_sms(
    From: str = Form(...),
    Body: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    user = db.scalar(select(User).where(User.phone_number == From))
    if user is None:
        return twiml("<Message>No PagerBuddy user is registered for this phone number.</Message>")
    command = Body.strip().upper()
    incident = find_sms_target_incident(db, user)
    if command in {"ACK", "ACKNOWLEDGE"}:
        if incident is None:
            return twiml("<Message>Multiple or no open incidents found. Reply with an incident ID in the API for now.</Message>")
        acknowledge_incident(db, incident, user, "sms")
        db.commit()
        return twiml(f"<Message>Acknowledged incident {incident.id}.</Message>")
    if command == "RESOLVE":
        if incident is None:
            return twiml("<Message>Multiple or no open incidents found. Reply with an incident ID in the API for now.</Message>")
        resolve_incident(db, incident, user, "sms")
        db.commit()
        return twiml(f"<Message>Resolved incident {incident.id}.</Message>")
    return twiml("<Message>Reply ACK to acknowledge or RESOLVE to resolve.</Message>")


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
