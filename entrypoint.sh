#!/bin/bash
set -e

# Start Celery worker in background
celery -A src.core.celery.celery_app worker \
  --loglevel=info \
  -Q ticket_email,sla \
  --concurrency=4 &

# Start Celery beat in background
celery -A src.core.celery.celery_app beat \
  --loglevel=info \
  --scheduler celery.beat.PersistentScheduler &

# Start FastAPI
exec uvicorn src.main:app --host 0.0.0.0 --port 8002