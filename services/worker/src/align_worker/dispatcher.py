from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from align_api.models import OutboxEvent
from align_worker.celery_app import celery_app, general_queue, parser_queue
from align_worker.database import SessionFactory

logger = logging.getLogger("align.outbox")


def dispatch_once(limit: int = 50) -> int:
    now = datetime.now(UTC)
    sent = 0
    with SessionFactory.begin() as db:
        events = db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.delivered_at.is_(None),
                (OutboxEvent.next_attempt_at.is_(None) | (OutboxEvent.next_attempt_at <= now)),
            )
            .order_by(OutboxEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        for event in events:
            task_name, argument_key, queue = _task_for_event(event.event_type)
            if task_name is None:
                event.attempt_count += 1
                delay = min(300, 2 ** min(event.attempt_count, 8))
                event.next_attempt_at = now + timedelta(seconds=delay)
                logger.warning("unsupported_outbox_event", extra={"event_id": str(event.id), "event_type": event.event_type})
                continue
            subject_id = event.payload_json.get(argument_key)
            if not isinstance(subject_id, str):
                event.attempt_count += 1
                event.next_attempt_at = now + timedelta(minutes=5)
                logger.error("outbox_payload_invalid", extra={"event_id": str(event.id)})
                continue
            try:
                celery_app.send_task(task_name, kwargs={argument_key: subject_id}, queue=queue)
            except Exception:
                event.attempt_count += 1
                delay = min(300, 2 ** min(event.attempt_count, 8))
                event.next_attempt_at = now + timedelta(seconds=delay)
                logger.exception("outbox_delivery_failed", extra={"event_id": str(event.id)})
                continue
            event.delivered_at = datetime.now(UTC)
            event.attempt_count += 1
            sent += 1
    return sent


def _task_for_event(event_type: str) -> tuple[str | None, str, str]:
    mapping = {
        "operation.foundation_smoke": ("align.foundation_smoke", "operation_id", general_queue),
        "job.execute": ("align.execute_job", "job_id", general_queue),
        "job.parse_context": ("align.parse_context", "job_id", parser_queue),
        "job.parse_rubric": ("align.parse_rubric", "job_id", parser_queue),
    }
    return mapping.get(event_type, (None, "", general_queue))


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch committed ALIGN outbox IDs to Celery.")
    parser.add_argument("--once", action="store_true", help="deliver one batch and exit")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("APP_LOG_LEVEL", "INFO"))
    while True:
        sent = dispatch_once()
        if args.once:
            return
        time.sleep(0.25 if sent else 2.0)


if __name__ == "__main__":
    main()
