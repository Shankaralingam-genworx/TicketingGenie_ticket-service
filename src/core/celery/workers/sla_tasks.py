"""
SLA monitor Celery task — normal + escalated breach detection.
File: src/core/celery/workers/sla_tasks.py

Four breach scenarios checked every 5 minutes
══════════════════════════════════════════════

NORMAL TICKET (is_escalated=False):
  1. Response breach  — response_due_at < NOW AND first_response_at IS NULL
     Agent assigned but never clicked Start Working in time → escalate to lead

  2. Resolution breach — resolution_due_at < NOW AND work_started_at IS NOT NULL
     Agent started but didn't resolve in time → escalate to lead

ESCALATED TICKET (is_escalated=True, already reassigned to new agent):
  3. Escalated response breach — escalated_response_due_at < NOW
                                  AND first_response_at IS NULL
     New agent hasn't clicked Start Working within additional response window
     → notify lead (no further auto-escalation, lead handles manually)

  4. Escalated resolution breach — escalated_resolution_due_at < NOW
                                    AND work_started_at IS NOT NULL
     New agent started but didn't resolve within additional resolution window
     → notify lead (no further auto-escalation)

Note: Scenarios 3 + 4 do NOT create another Escalation row (max 1 per ticket).
They only send notifications to the team lead.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from src.core.celery.celery_app import celery_app

logger = logging.getLogger("ticket.tasks.sla")


# ─────────────────────────────────────────────────────────────────────────────
# Celery beat entry point
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="src.core.celery.workers.sla_tasks.check_sla_breaches_task",
    bind=True, max_retries=3, default_retry_delay=60, queue="sla",
)
def check_sla_breaches_task(self) -> dict:
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run_sla_check())
        logger.info(
            f"[sla_monitor] scan complete | "
            f"response_breaches={result['response_breaches']} | "
            f"resolution_breaches={result['resolution_breaches']} | "
            f"escalated_response_breaches={result['escalated_response_breaches']} | "
            f"escalated_resolution_breaches={result['escalated_resolution_breaches']}"
        )
        return result
    except Exception as exc:
        logger.error(f"[sla_monitor] failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main async scan
# ─────────────────────────────────────────────────────────────────────────────

async def _run_sla_check() -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select

    from src.config.settings import settings
    from src.constants.ticket_constants import TicketStatus
    from src.data.models.postgres.ticket_model import Ticket
    from src.data.repositories.ticket_repository import TicketRepository
    from src.data.repositories.ticket_audit_repository import TicketAuditRepository
    from src.data.repositories.escalation_repository import EscalationRepository

    ACTIVE = [
        TicketStatus.NEW, TicketStatus.ACKNOWLEDGED, TicketStatus.ASSIGNED,
        TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.ON_HOLD,
    ]

    engine            = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    counts = dict(
        response_breaches=0, resolution_breaches=0,
        escalated_response_breaches=0, escalated_resolution_breaches=0,
    )

    try:
        async with AsyncSessionLocal() as session:
            now             = datetime.now(timezone.utc)
            ticket_repo     = TicketRepository(session)
            audit_repo      = TicketAuditRepository(session)
            escalation_repo = EscalationRepository(session)

            # ── Q1: Normal response breach ────────────────────────────────────
            # Agent assigned but never clicked Start Working within response window
            r1 = await session.execute(
                select(Ticket).where(
                    Ticket.is_escalated == False,               # noqa: E712
                    Ticket.response_due_at.is_not(None),
                    Ticket.response_due_at <= now,
                    Ticket.first_response_at.is_(None),         # never responded
                    Ticket.assigned_agent_id.is_not(None),
                    Ticket.status.in_(ACTIVE),
                )
            )
            for ticket in r1.scalars().all():
                ok = await _escalate_ticket(
                    ticket=ticket, now=now,
                    reason="response_sla_breach",
                    notes=(
                        f"Agent did not start working within response window. "
                        f"response_due_at={ticket.response_due_at.isoformat()}"
                    ),
                    ticket_repo=ticket_repo,
                    audit_repo=audit_repo,
                    escalation_repo=escalation_repo,
                )
                if ok:
                    counts["response_breaches"] += 1
                    _notify_sla_breach.delay(
                        ticket_id=ticket.id, ticket_number=ticket.ticket_number,
                        ticket_title=ticket.title, breach_type="response",
                        severity=_ev(ticket.severity), priority=_ev(ticket.priority),
                        customer_email=ticket.customer_email,
                        team_id=ticket.team_id,
                        assigned_agent_id=ticket.assigned_agent_id,
                        breached_at_iso=now.isoformat(),
                    )

            # ── Q2: Normal resolution breach ──────────────────────────────────
            # Agent started but didn't resolve in time
            r2 = await session.execute(
                select(Ticket).where(
                    Ticket.is_escalated == False,               # noqa: E712
                    Ticket.work_started_at.is_not(None),
                    Ticket.resolution_due_at.is_not(None),
                    Ticket.resolution_due_at <= now,
                    Ticket.first_response_at.is_not(None),
                    Ticket.assigned_agent_id.is_not(None),
                    Ticket.status.in_(ACTIVE),
                )
            )
            for ticket in r2.scalars().all():
                ok = await _escalate_ticket(
                    ticket=ticket, now=now,
                    reason="resolution_sla_breach",
                    notes=(
                        f"Agent started but did not resolve within resolution window. "
                        f"work_started_at={ticket.work_started_at.isoformat()} "
                        f"resolution_due_at={ticket.resolution_due_at.isoformat()}"
                    ),
                    ticket_repo=ticket_repo,
                    audit_repo=audit_repo,
                    escalation_repo=escalation_repo,
                )
                if ok:
                    counts["resolution_breaches"] += 1
                    _notify_sla_breach.delay(
                        ticket_id=ticket.id, ticket_number=ticket.ticket_number,
                        ticket_title=ticket.title, breach_type="resolution",
                        severity=_ev(ticket.severity), priority=_ev(ticket.priority),
                        customer_email=ticket.customer_email,
                        team_id=ticket.team_id,
                        assigned_agent_id=ticket.assigned_agent_id,
                        breached_at_iso=now.isoformat(),
                    )

            # ── Q3: Escalated response breach ─────────────────────────────────
            # New agent (after escalation reassignment) hasn't clicked Start Working
            # within the additional response window.
            # Does NOT create another Escalation row — just notifies lead.
            r3 = await session.execute(
                select(Ticket).where(
                    Ticket.is_escalated == True,                # noqa: E712
                    Ticket.escalated_response_due_at.is_not(None),
                    Ticket.escalated_response_due_at <= now,
                    Ticket.first_response_at.is_(None),         # new agent hasn't started
                    Ticket.assigned_agent_id.is_not(None),
                    Ticket.status.in_(ACTIVE),
                )
            )
            for ticket in r3.scalars().all():
                # Only notify — no new escalation row (max 1 per ticket)
                await audit_repo.log(
                    ticket_id=ticket.id,
                    action=_AuditAction().SLA_BREACHED,
                    actor_id=0, actor_role="system",
                    old_value={},
                    new_value={
                        "breach_type":               "escalated_response_sla_breach",
                        "escalated_response_due_at": ticket.escalated_response_due_at.isoformat(),
                        "checked_at":                now.isoformat(),
                    },
                )
                counts["escalated_response_breaches"] += 1
                _notify_sla_breach.delay(
                    ticket_id=ticket.id, ticket_number=ticket.ticket_number,
                    ticket_title=ticket.title, breach_type="escalated_response",
                    severity=_ev(ticket.severity), priority=_ev(ticket.priority),
                    customer_email=ticket.customer_email,
                    team_id=ticket.team_id,
                    assigned_agent_id=ticket.assigned_agent_id,
                    breached_at_iso=now.isoformat(),
                )
                logger.warning(
                    f"[sla_monitor] Escalated response breach: {ticket.ticket_number} | "
                    f"new_agent={ticket.assigned_agent_id}"
                )

            # ── Q4: Escalated resolution breach ───────────────────────────────
            # New agent started but didn't resolve within additional resolution window.
            # Notify lead — no new escalation row.
            r4 = await session.execute(
                select(Ticket).where(
                    Ticket.is_escalated == True,                # noqa: E712
                    Ticket.work_started_at.is_not(None),
                    Ticket.escalated_resolution_due_at.is_not(None),
                    Ticket.escalated_resolution_due_at <= now,
                    Ticket.first_response_at.is_not(None),
                    Ticket.assigned_agent_id.is_not(None),
                    Ticket.status.in_(ACTIVE),
                )
            )
            for ticket in r4.scalars().all():
                await audit_repo.log(
                    ticket_id=ticket.id,
                    action=_AuditAction().SLA_BREACHED,
                    actor_id=0, actor_role="system",
                    old_value={},
                    new_value={
                        "breach_type":                  "escalated_resolution_sla_breach",
                        "escalated_resolution_due_at":  ticket.escalated_resolution_due_at.isoformat(),
                        "checked_at":                   now.isoformat(),
                    },
                )
                counts["escalated_resolution_breaches"] += 1
                _notify_sla_breach.delay(
                    ticket_id=ticket.id, ticket_number=ticket.ticket_number,
                    ticket_title=ticket.title, breach_type="escalated_resolution",
                    severity=_ev(ticket.severity), priority=_ev(ticket.priority),
                    customer_email=ticket.customer_email,
                    team_id=ticket.team_id,
                    assigned_agent_id=ticket.assigned_agent_id,
                    breached_at_iso=now.isoformat(),
                )
                logger.warning(
                    f"[sla_monitor] Escalated resolution breach: {ticket.ticket_number} | "
                    f"new_agent={ticket.assigned_agent_id}"
                )

            await session.commit()

    finally:
        await engine.dispose()

    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Escalation helper (normal tickets only — creates Escalation row)
# ─────────────────────────────────────────────────────────────────────────────

async def _escalate_ticket(*, ticket, now, reason, notes,
                            ticket_repo, audit_repo, escalation_repo) -> bool:
    from src.constants.sla_constants import AuditAction
    try:
        await escalation_repo.create(
            ticket_id=ticket.id, old_agent_id=ticket.assigned_agent_id,
            reason=reason, escalated_by="system", notes=notes,
        )
        await ticket_repo.update(ticket, is_escalated=True, escalated_at=now)
        await audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.ESCALATED,
            actor_id=0, actor_role="system",
            old_value={"is_escalated": False},
            new_value={
                "is_escalated": True, "old_agent_id": ticket.assigned_agent_id,
                "escalated_at": now.isoformat(), "reason": reason,
            },
        )
        logger.info(
            f"[sla_monitor] Escalated {ticket.ticket_number} | "
            f"reason={reason} | old_agent={ticket.assigned_agent_id}"
        )
        return True
    except Exception as exc:
        logger.error(f"[sla_monitor] Failed to escalate id={ticket.id}: {exc}", exc_info=True)
        return False


def _ev(val) -> str:
    return val.value if hasattr(val, "value") else str(val)


# Lazy import wrapper to avoid circular import at module load time
class _AuditAction:
    @property
    def SLA_BREACHED(self):
        from src.constants.sla_constants import AuditAction
        return AuditAction.SLA_BREACHED


# ─────────────────────────────────────────────────────────────────────────────
# Notification task
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="src.core.celery.workers.sla_tasks._notify_sla_breach",
    bind=True, max_retries=3, default_retry_delay=30, queue="sla",
)
def _notify_sla_breach(
    self, *, ticket_id, ticket_number, ticket_title, breach_type,
    severity, priority, customer_email, team_id, assigned_agent_id, breached_at_iso,
) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_send_breach_notifications(
            ticket_id=ticket_id, ticket_number=ticket_number,
            ticket_title=ticket_title, breach_type=breach_type,
            severity=severity, priority=priority,
            customer_email=customer_email, team_id=team_id,
            assigned_agent_id=assigned_agent_id, breached_at_iso=breached_at_iso,
        ))
    except Exception as exc:
        logger.error(f"[sla_breach] notification failed | ticket={ticket_number} | {exc}")
        raise self.retry(exc=exc)
    finally:
        loop.close()


async def _send_breach_notifications(
    *, ticket_id, ticket_number, ticket_title, breach_type,
    severity, priority, customer_email, team_id,
    assigned_agent_id, breached_at_iso,
) -> None:
    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from src.config.settings import settings
    from src.core.celery.workers.email_tasks import build_sla_breach_email, send_notification_task
    from src.core.services.notification_service import NotificationService

    breached_at       = datetime.fromisoformat(breached_at_iso)
    breached_at_label = breached_at.strftime("%Y-%m-%d %H:%M UTC")

    lead_email, lead_name, lead_id = "support-lead@company.com", "Team Lead", 0
    if team_id:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{settings.AUTH_SERVICE_URL}/api/v1/teams/{team_id}/lead"
                )
                if resp.status_code == 200:
                    d = resp.json()
                    lead_email = d.get("email", lead_email)
                    lead_name  = d.get("name",  lead_name)
                    lead_id    = d.get("id",     lead_id)
        except Exception as e:
            logger.warning(f"[sla_breach] Could not fetch lead: {e}")

    payload = build_sla_breach_email(
        to_email=lead_email, lead_name=lead_name,
        ticket_number=ticket_number, ticket_title=ticket_title, severity=severity, priority=priority,
        customer_email=customer_email, breached_at=breached_at_label,
        assigned_agent_id=assigned_agent_id,
    )
    send_notification_task.delay(
        to_email=payload.to_email, subject=payload.subject,
        html_body=payload.html_body, text_body=payload.text_body,
        ticket_number=ticket_number,
        notification_type=f"sla_{breach_type}_breach",
    )

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await NotificationService(session).sla_breached(
                    recipient_id=lead_id, ticket_id=ticket_id,
                    ticket_number=ticket_number, ticket_title=ticket_title,
                    severity=severity, priority=priority, breached_at=breached_at,
                )
    finally:
        await engine.dispose()