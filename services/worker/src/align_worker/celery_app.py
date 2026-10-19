from __future__ import annotations

import os

from celery import Celery
from celery.schedules import schedule

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
queue_prefix = os.environ.get("REDIS_QUEUE_PREFIX", "align-dev")
general_queue = f"{queue_prefix}.general"
parser_queue = f"{queue_prefix}.parser"

celery_app = Celery(
    "align",
    broker=redis_url,
    include=["align_worker.tasks"],
)
celery_app.conf.update(
    task_default_queue=general_queue,
    task_default_exchange=general_queue,
    task_default_routing_key=general_queue,
    task_routes={
        "align.foundation_smoke": {"queue": general_queue},
        "align.execute_job": {"queue": general_queue},
        "align.parse_context": {"queue": parser_queue},
        "align.reap_expired_leases": {"queue": general_queue},
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=False,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 300, "global_keyprefix": f"{queue_prefix}:"},
    task_soft_time_limit=int(os.environ.get("STAGE_SOFT_SECONDS", "120")),
    task_time_limit=int(os.environ.get("STAGE_HARD_SECONDS", "150")),
    result_backend=None,
    beat_schedule={
        "reap-expired-operations-and-job-leases": {
            "task": "align.reap_expired_leases",
            "schedule": schedule(float(os.environ.get("REAPER_INTERVAL_SECONDS", "60"))),
            "options": {"queue": general_queue},
        }
    },
    timezone="UTC",
    enable_utc=True,
)

