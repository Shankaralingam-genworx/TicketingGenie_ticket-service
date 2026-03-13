"""SLA utility helpers.
File: src/utils/sla_utils.py

compute_due_at now accepts an optional from_time parameter.
  - When from_time is None  → deadline is computed from NOW (used at ticket creation
    for response_due_at)
  - When from_time is given → deadline is computed from that moment (used in
    start_working() so resolution_due_at = work_started_at + resolution_time_mins)
"""

from datetime import datetime, timedelta, timezone


def compute_due_at(minutes: float, from_time: datetime | None = None) -> datetime:
    """Return UTC datetime that is `minutes` from from_time (or now)."""
    base = from_time if from_time is not None else datetime.now(timezone.utc)
    return base + timedelta(minutes=minutes)


def is_sla_breached(due_at: datetime | None) -> bool:
    """Return True if the due date has passed."""
    if due_at is None:
        return False
    return datetime.now(timezone.utc) > due_at