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

            # ── Diagnostic: log all active tickets and their SLA state ─────────
            # Uses ORM query (not raw SQL) to avoid enum cast issues with asyncpg.
            diag_result = await session.execute(
                select(Ticket).where(
                    Ticket.status.in_(ACTIVE)
                ).order_by(Ticket.id.desc()).limit(20)
            )
            diag_rows = diag_result.scalars().all()
            logger.info(f"[sla_monitor] Diagnostic — active tickets: {len(diag_rows)}")
            for r in diag_rows:
                logger.info(
                    f"  ticket={r.ticket_number} status={r.status} "
                    f"is_escalated={r.is_escalated} "
                    f"agent={r.assigned_agent_id} "
                    f"work_started={r.work_started_at} "
                    f"first_resp={r.first_response_at} "
                    f"resp_due={r.response_due_at} "
                    f"resol_due={r.resolution_due_at} "
                    f"now={now.isoformat()} "
                    f"resp_breached={r.response_due_at is not None and r.response_due_at <= now} "
                    f"resol_breached={r.resolution_due_at is not None and r.resolution_due_at <= now}"
                )

            # ── Q1: Normal response breach ────────────────────────────────────
            # Covers two sub-cases:
            #   Q1a — agent was assigned but never clicked Start Working in time
            #   Q1b — ticket was never even assigned (lead didn't act in time)
            # Both result in escalation so the team lead must take action.
            r1 = await session.execute(
                select(Ticket).where(
                    Ticket.is_escalated == False,               # noqa: E712
                    Ticket.response_due_at.is_not(None),
                    Ticket.response_due_at <= now,
                    Ticket.first_response_at.is_(None),         # never responded
                    Ticket.status.in_(ACTIVE),
                    # NOTE: intentionally no filter on assigned_agent_id —
                    # unassigned tickets that breach response SLA also escalate.
                )
            )
            for ticket in r1.scalars().all():
                breach_note = (
                    f"Agent did not start working within response window. "
                    f"response_due_at={ticket.response_due_at.isoformat()}"
                    if ticket.assigned_agent_id
                    else
                    f"Ticket was never assigned within response window. "
                    f"response_due_at={ticket.response_due_at.isoformat()}"
                )
                ok = await _escalate_ticket(
                    ticket=ticket, now=now,
                    reason="response_sla_breach",
                    notes=breach_note,
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
                        customer_id=ticket.customer_id,
                        customer_email=ticket.customer_email,
                        team_id=ticket.team_id,
                        assigned_agent_id=ticket.assigned_agent_id,
                        breached_at_iso=now.isoformat(),
                    )

            # ── Q2: Normal resolution breach ──────────────────────────────────
            # Agent clicked Start Working but didn't resolve within resolution window.
            # work_started_at IS NOT NULL is the single condition that confirms the
            # agent started — first_response_at check is redundant and removed to
            # avoid missed escalations.
            r2 = await session.execute(
                select(Ticket).where(
                    Ticket.is_escalated == False,               # noqa: E712
                    Ticket.work_started_at.is_not(None),
                    Ticket.resolution_due_at.is_not(None),
                    Ticket.resolution_due_at <= now,
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
                        customer_id=ticket.customer_id,
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
                # Guard: only notify ONCE — check if we already logged this breach type
                # for this ticket. If a SLA_BREACHED audit entry with
                # breach_type="escalated_response_sla_breach" exists, skip entirely.
                from sqlalchemy import text as _text2
                already = await session.execute(
                    _text2(
                        "SELECT id FROM ticket_audits "
                        "WHERE ticket_id = :tid "
                        "  AND action = 'sla_breached' "
                        "  AND new_value->>'breach_type' = 'escalated_response_sla_breach' "
                        "LIMIT 1"
                    ),
                    {"tid": ticket.id},
                )
                if already.fetchone():
                    logger.info(
                        f"[sla_monitor] Q3 already notified for {ticket.ticket_number} — skipping"
                    )
                    continue

                # First time — log audit entry and fire notification
                await audit_repo.log(
                    ticket_id=ticket.id,
                    action=_AuditAction().SLA_BREACHED,
                    actor_id=0, actor_role="system",
                    old_value={},
                    new_value={
                        "breach_type":               "escalated_response_sla_breach",
                        "escalated_response_due_at": ticket.escalated_response_due_at.isoformat(),
                        "notified_at":               now.isoformat(),
                    },
                )
                counts["escalated_response_breaches"] += 1
                _notify_sla_breach.delay(
                    ticket_id=ticket.id, ticket_number=ticket.ticket_number,
                    ticket_title=ticket.title, breach_type="escalated_response",
                    severity=_ev(ticket.severity), priority=_ev(ticket.priority),
                    customer_id=ticket.customer_id,
                    customer_email=ticket.customer_email,
                    team_id=ticket.team_id,
                    assigned_agent_id=ticket.assigned_agent_id,
                    breached_at_iso=now.isoformat(),
                )
                logger.warning(
                    f"[sla_monitor] Escalated response breach (first notification): "
                    f"{ticket.ticket_number} | new_agent={ticket.assigned_agent_id}"
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
                # Guard: only notify ONCE — same pattern as Q3
                from sqlalchemy import text as _text3
                already4 = await session.execute(
                    _text3(
                        "SELECT id FROM ticket_audits "
                        "WHERE ticket_id = :tid "
                        "  AND action = 'sla_breached' "
                        "  AND new_value->>'breach_type' = 'escalated_resolution_sla_breach' "
                        "LIMIT 1"
                    ),
                    {"tid": ticket.id},
                )
                if already4.fetchone():
                    logger.info(
                        f"[sla_monitor] Q4 already notified for {ticket.ticket_number} — skipping"
                    )
                    continue

                # First time — log and notify
                await audit_repo.log(
                    ticket_id=ticket.id,
                    action=_AuditAction().SLA_BREACHED,
                    actor_id=0, actor_role="system",
                    old_value={},
                    new_value={
                        "breach_type":                  "escalated_resolution_sla_breach",
                        "escalated_resolution_due_at":  ticket.escalated_resolution_due_at.isoformat(),
                        "notified_at":                  now.isoformat(),
                    },
                )
                counts["escalated_resolution_breaches"] += 1
                _notify_sla_breach.delay(
                    ticket_id=ticket.id, ticket_number=ticket.ticket_number,
                    ticket_title=ticket.title, breach_type="escalated_resolution",
                    severity=_ev(ticket.severity), priority=_ev(ticket.priority),
                    customer_id=ticket.customer_id,
                    customer_email=ticket.customer_email,
                    team_id=ticket.team_id,
                    assigned_agent_id=ticket.assigned_agent_id,
                    breached_at_iso=now.isoformat(),
                )
                logger.warning(
                    f"[sla_monitor] Escalated resolution breach (first notification): "
                    f"{ticket.ticket_number} | new_agent={ticket.assigned_agent_id}"
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
    from src.data.models.postgres.escalation_model import Escalation
    from sqlalchemy import select as _select

    try:
        # Guard: skip if escalation row already exists (unique constraint on ticket_id).
        # This prevents a crash on the next Celery scan after a partial failure.
        existing = await escalation_repo.get_by_ticket(ticket.id)
        if existing:
            logger.warning(
                f"[sla_monitor] Escalation row already exists for {ticket.ticket_number} "
                f"(id={ticket.id}) — marking ticket is_escalated=True and skipping row create."
            )
            await ticket_repo.update(ticket, is_escalated=True, escalated_at=now)
            return False  # don't double-notify

        await escalation_repo.create(
            ticket_id    = ticket.id,
            old_agent_id = ticket.assigned_agent_id or 0,   # 0 = unassigned
            reason       = reason,
            escalated_by = "system",
            notes        = notes,
        )
        await ticket_repo.update(ticket, is_escalated=True, escalated_at=now)
        await audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.ESCALATED,
            actor_id=0, actor_role="system",
            old_value={"is_escalated": False},
            new_value={
                "is_escalated": True,
                "old_agent_id": ticket.assigned_agent_id,
                "escalated_at": now.isoformat(),
                "reason":       reason,
            },
        )
        logger.info(
            f"[sla_monitor] Escalated {ticket.ticket_number} | "
            f"reason={reason} | old_agent={ticket.assigned_agent_id}"
        )
        return True
    except Exception as exc:
        logger.error(
            f"[sla_monitor] Failed to escalate ticket={ticket.ticket_number} "
            f"id={ticket.id}: {exc}",
            exc_info=True,
        )
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
    severity, priority, customer_id, customer_email, team_id, assigned_agent_id, breached_at_iso,
) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_send_breach_notifications(
            ticket_id=ticket_id, ticket_number=ticket_number,
            ticket_title=ticket_title, breach_type=breach_type,
            severity=severity, priority=priority,
            customer_id=customer_id, customer_email=customer_email,
            team_id=team_id,
            assigned_agent_id=assigned_agent_id, breached_at_iso=breached_at_iso,
        ))
    except Exception as exc:
        logger.error(f"[sla_breach] notification failed | ticket={ticket_number} | {exc}")
        raise self.retry(exc=exc)
    finally:
        loop.close()


async def _send_breach_notifications(
    *, ticket_id, ticket_number, ticket_title, breach_type,
    severity, priority, customer_id, customer_email, team_id,
    assigned_agent_id, breached_at_iso,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from src.config.settings import settings
    from src.core.celery.workers.email_tasks import (
        build_sla_breach_email,
        build_escalation_customer_notice_email,
        build_sla_breach_customer_email,
        send_notification_task,
    )
    from src.core.services.notification_service import NotificationService
    from src.data.repositories.shared_user_repository import SharedUserRepository

    breached_at       = datetime.fromisoformat(breached_at_iso)
    breached_at_label = breached_at.strftime("%Y-%m-%d %H:%M UTC")

    # ── Resolve team lead from shared DB (no HTTP call) ───────────────────────
    lead_email, lead_name, lead_id = "support-lead@company.com", "Team Lead", 0
    engine            = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as session:
            if team_id:
                lead = await SharedUserRepository(session).get_team_lead_by_team_id(team_id)
                if lead:
                    lead_email = lead["email"]
                    lead_name  = lead["name"]
                    lead_id    = lead["id"]
                else:
                    logger.warning(
                        f"[sla_breach] No active lead for team_id={team_id} | "
                        f"ticket={ticket_number} — using fallback"
                    )

            # ── Email to team lead ────────────────────────────────────────────
            payload = build_sla_breach_email(
                to_email=lead_email, lead_name=lead_name,
                ticket_number=ticket_number, ticket_title=ticket_title,
                severity=severity, priority=priority,
                customer_email=customer_email, breached_at=breached_at_label,
                assigned_agent_id=assigned_agent_id,
            )
            send_notification_task.delay(
                to_email=payload.to_email, subject=payload.subject,
                html_body=payload.html_body, text_body=payload.text_body,
                ticket_number=ticket_number,
                notification_type=f"sla_{breach_type}_breach_lead",
            )

            # ── In-app for team lead ──────────────────────────────────────────
            # Do NOT call session.begin() here — AsyncSession auto-starts a transaction
            # on first use. Calling begin() again raises "A transaction is already begun".
            # Just call the service methods directly and commit once at the end.
            await NotificationService(session).sla_breached(
                recipient_id=lead_id, ticket_id=ticket_id,
                ticket_number=ticket_number, ticket_title=ticket_title,
                severity=severity, priority=priority, breached_at=breached_at,
            )

            # ── Customer notifications (email + in-app) ───────────────────────
            # Q1/Q2 (first breach): reassuring "still working on it" message
            # Q3/Q4 (second breach on escalated ticket): apology update
            is_second_breach = breach_type in ("escalated_response", "escalated_resolution")

            if is_second_breach:
                cust_payload = build_sla_breach_customer_email(
                    to_email        = customer_email,
                    ticket_number   = ticket_number,
                    ticket_title    = ticket_title,
                    current_status  = "in_progress",
                    is_second_breach= True,
                )
            else:
                cust_payload = build_escalation_customer_notice_email(
                    to_email       = customer_email,
                    ticket_number  = ticket_number,
                    ticket_title   = ticket_title,
                    current_status = "in_progress",
                )

            send_notification_task.delay(
                to_email=cust_payload.to_email, subject=cust_payload.subject,
                html_body=cust_payload.html_body, text_body=cust_payload.text_body,
                ticket_number=ticket_number,
                notification_type=f"sla_{breach_type}_breach_customer",
            )

            # ── In-app for customer ───────────────────────────────────────────
            if is_second_breach:
                await NotificationService(session).sla_breached_customer(
                    recipient_id  = customer_id,
                    ticket_id     = ticket_id,
                    ticket_number = ticket_number,
                    ticket_title  = ticket_title,
                )
            else:
                await NotificationService(session).ticket_escalated_customer(
                    recipient_id   = customer_id,
                    ticket_id      = ticket_id,
                    ticket_number  = ticket_number,
                    ticket_title   = ticket_title,
                    current_status = "in_progress",
                )

            # Single commit covers all in-app notifications written above
            await session.commit()

    finally:
        await engine.dispose()