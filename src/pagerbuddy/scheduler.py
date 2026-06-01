import logging
import smtplib
import time
from email.message import EmailMessage

from sqlalchemy import select

from pagerbuddy.config import get_settings
from pagerbuddy.database import SessionLocal, init_db
from pagerbuddy.models import Schedule, SystemEvent, User, UserRole
from pagerbuddy.schedules import detect_schedule_gaps

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pagerbuddy.scheduler")


def _send_admin_gap_email(subject: str, body: str) -> None:
    settings = get_settings()
    recipients = settings.admin_alert_email_list
    if not recipients:
        with SessionLocal() as db:
            recipients = [user.email for user in db.scalars(select(User).where(User.role == UserRole.admin)).all()]
    if not recipients:
        logger.warning("schedule gap detected but no admin recipients are configured")
        return
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    if settings.smtp_host == "dry-run":
        logger.info("dry-run admin gap email: %s\n%s", subject, body)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_username and settings.smtp_password:
            smtp.starttls()
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def run_once() -> int:
    count = 0
    with SessionLocal() as db:
        schedules = db.scalars(select(Schedule)).all()
        for schedule in schedules:
            gaps = detect_schedule_gaps(schedule, db=db)
            if not gaps:
                continue
            count += len(gaps)
            gap_lines = "\n".join(f"- {gap.start.isoformat()} to {gap.end.isoformat()}" for gap in gaps)
            for gap in gaps:
                db.add(
                    SystemEvent(
                        event_type="schedule_gap_detected",
                        payload={
                            "schedule_id": str(schedule.id),
                            "schedule_name": schedule.name,
                            "start": gap.start.isoformat(),
                            "end": gap.end.isoformat(),
                        },
                    )
                )
            _send_admin_gap_email(
                f"PagerBuddy schedule gap detected: {schedule.name}",
                f"Schedule {schedule.name} has uncovered windows in the next 30 days:\n\n{gap_lines}",
            )
        db.commit()
    return count


def main() -> None:
    settings = get_settings()
    init_db()
    logger.info("PagerBuddy scheduler started")
    while True:
        try:
            gaps = run_once()
            if gaps:
                logger.warning("detected %s schedule gap window(s)", gaps)
        except Exception:
            logger.exception("scheduler loop failed")
        time.sleep(settings.scheduler_poll_seconds)


if __name__ == "__main__":
    main()
