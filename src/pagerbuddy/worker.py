import logging
import time

from pagerbuddy.config import get_settings
from pagerbuddy.database import SessionLocal, init_db
from pagerbuddy.escalation import process_due_escalations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pagerbuddy.worker")


def run_once() -> int:
    with SessionLocal() as db:
        processed = process_due_escalations(db)
        db.commit()
        return processed


def main() -> None:
    settings = get_settings()
    init_db()
    logger.info("PagerBuddy worker started")
    while True:
        try:
            processed = run_once()
            if processed:
                logger.info("processed %s due escalation(s)", processed)
        except Exception:
            logger.exception("worker loop failed")
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()

