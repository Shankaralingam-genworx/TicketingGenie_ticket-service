"""
Celery application instance.
File path: src/core/celery/celery_app.py

Development (Windows) — single command runs worker + beat together:
    celery -A src.core.celery.celery_app worker --loglevel=info -Q email --pool=solo --beat

Production — run worker and beat separately:
    celery -A src.core.celery.celery_app worker --loglevel=info -Q email
    celery -A src.core.celery.celery_app beat   --loglevel=info
"""

from celery import Celery
from src.config.settings import settings

celery_app = Celery(
    "ticketing_genie",
    broker  = settings.CELERY_BROKER_URL,
    backend = settings.CELERY_RESULT_BACKEND,
    include = [
        "src.core.celery.workers.email_tasks",
        "src.core.celery.workers.sla_tasks",
        "src.core.celery.workers.email_intake_task",
    ],
)

celery_app.conf.update(
    task_serializer   = "json",
    accept_content    = ["json"],
    result_serializer = "json",
    timezone          = "UTC",
    enable_utc        = True,

    # ── All tasks on the single "email" queue ─────────────────────────────────
    # Keeps dev simple — one worker command handles everything.
    # In production you can split into separate queues per task group.
    task_routes = {
        "src.core.celery.workers.email_tasks.*":       {"queue": "email"},
        "src.core.celery.workers.sla_tasks.*":         {"queue": "email"},
        "src.core.celery.workers.email_intake_task.*": {"queue": "email"},
    },

    task_acks_late                     = True,
    task_reject_on_worker_lost         = True,
    broker_connection_retry_on_startup = True,   # silences Celery 6.0 warning

    # ── Beat schedule ─────────────────────────────────────────────────────────
    beat_schedule = {
        # Support inbox poll — every EMAIL_POLL_INTERVAL seconds (default 60)
        "poll-support-inbox": {
            "task":     "src.core.celery.workers.email_intake_task.poll_support_inbox_task",
            "schedule": float(getattr(settings, "EMAIL_POLL_INTERVAL", 60)),
        },
        # SLA breach monitor — every 1 minutes
        "check-sla-breaches": {
            "task":     "src.core.celery.workers.sla_tasks.check_sla_breaches_task",
            "schedule": 60.0,
        },
    },
)