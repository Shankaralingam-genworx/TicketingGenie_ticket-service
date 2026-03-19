"""
Celery email tasks.

Architecture:
  - ONE generic Celery task  → send_notification_task
  - N builder functions      → one per notification type

Adding a new notification type:
  1. Add a new build_<type>_email() function
  2. Call send_notification_task.delay(**vars(payload), ...)
  No new Celery task needed.

Special tasks:
  - send_ticket_acknowledgement_task  → email + DB transition NEW → ACKNOWLEDGED
  - acknowledge_ticket_status_task    → DB-only (chained by above)

File path: src/core/celery/workers/email_tasks.py
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from src.core.celery.celery_app import celery_app
from src.core.services.email_service import EmailService

logger = logging.getLogger("ticket.tasks.email")


# ─────────────────────────────────────────────────────────────────────────────
# Email payload dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EmailPayload:
    to_email:  str
    subject:   str
    html_body: str
    text_body: str


# ─────────────────────────────────────────────────────────────────────────────
# Generic Celery task — single task for ALL notification emails
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="src.core.celery.workers.email_tasks.send_notification_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="ticket_email",
)
def send_notification_task(
    self,
    *,
    to_email:  str,
    subject:   str,
    html_body: str,
    text_body: str,
    ticket_number:     str = "",
    notification_type: str = "generic",
) -> None:
    """
    Generic email sender task.
    All notification emails go through this single task.
    Callers build the payload via the builder functions below.

    Usage:
        payload = build_status_update_email(...)
        send_notification_task.delay(
            to_email           = payload.to_email,
            subject            = payload.subject,
            html_body          = payload.html_body,
            text_body          = payload.text_body,
            ticket_number      = ticket.ticket_number,
            notification_type  = "status_update",
        )
    """
    try:
        EmailService().send_email(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        logger.info(
            f"[{notification_type}] Email sent | "
            f"ticket={ticket_number} | to={to_email}"
        )
    except Exception as exc:
        logger.error(
            f"[{notification_type}] Email failed | "
            f"ticket={ticket_number} | attempt={self.request.retries + 1} | {exc}"
        )
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# Acknowledgement task — email + DB transition
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="src.core.celery.workers.email_tasks.send_ticket_acknowledgement_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="ticket_email",
)
def send_ticket_acknowledgement_task(
    self,
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
    severity:      str,
    priority:      str,
    priority_overridden: bool,
    ticket_id:     int,
    original_priority:   Optional[str]   = None,
    system_priority:     Optional[str]   = None,
    sla_resolution_hrs:  Optional[float] = None,
) -> None:
    """Sends acknowledgement email then chains → acknowledge_ticket_status_task."""
    try:
        payload = build_acknowledgement_email(
            to_email=to_email,
            ticket_number=ticket_number,
            ticket_title=ticket_title,
            severity=severity,
            priority=priority,
            priority_overridden=priority_overridden,
            original_priority=original_priority,
            system_priority=system_priority,
            sla_resolution_hrs=sla_resolution_hrs,
        )
        EmailService().send_email(
            to_email=payload.to_email,
            subject=payload.subject,
            html_body=payload.html_body,
            text_body=payload.text_body,
        )
        logger.info(f"[acknowledgement] Email sent | ticket={ticket_number} | to={to_email}")
        acknowledge_ticket_status_task.delay(ticket_id=ticket_id)

    except Exception as exc:
        logger.error(
            f"[acknowledgement] Email failed | ticket={ticket_number} | "
            f"attempt={self.request.retries + 1} | {exc}"
        )
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# Team-lead new-ticket notification task
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="src.core.celery.workers.email_tasks.notify_team_lead_new_ticket_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="ticket_email",
)
def notify_team_lead_new_ticket_task(
    self,
    *,
    ticket_id:           int,
    ticket_number:       str,
    ticket_title:        str,
    severity:            str,
    priority:            str,
    customer_email:      str,
    customer_id:         int,
    team_id:             int,
    priority_overridden: bool          = False,
    original_priority:   Optional[str] = None,
    system_priority:     Optional[str] = None,
) -> None:
    """
    Fetches the team lead from the shared DB, then sends:
      - Email to team lead notifying them of the new ticket
      - In-app notification for team lead
    Runs entirely in background so ticket creation stays instant.
    """
    try:
        asyncio.run(_notify_team_lead_new_ticket(
            ticket_id=ticket_id, ticket_number=ticket_number,
            ticket_title=ticket_title, severity=severity,
            priority=priority, customer_email=customer_email,
            customer_id=customer_id, team_id=team_id,
            priority_overridden=priority_overridden,
            original_priority=original_priority,
            system_priority=system_priority,
        ))
    except Exception as exc:
        logger.error(
            f"[team_lead_new_ticket] Failed | ticket={ticket_number} | "
            f"attempt={self.request.retries + 1} | {exc}"
        )
        raise self.retry(exc=exc)


async def _notify_team_lead_new_ticket(
    *, ticket_id, ticket_number, ticket_title, severity, priority,
    customer_email, customer_id, team_id, priority_overridden,
    original_priority, system_priority,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from src.config.settings import settings
    from src.core.services.notification_service import NotificationService
    from src.data.repositories.shared_user_repository import SharedUserRepository

    engine            = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as session:
            lead = await SharedUserRepository(session).get_team_lead_by_team_id(team_id)
            if not lead:
                logger.warning(
                    f"[team_lead_new_ticket] No active lead found for team_id={team_id} "
                    f"| ticket={ticket_number} — skipping"
                )
                return

            # ── Email to team lead ────────────────────────────────────────────
            payload = build_lead_ticket_created_email(
                to_email=lead["email"],
                lead_name=lead["name"],
                ticket_number=ticket_number,
                ticket_title=ticket_title,
                severity=severity,
                priority=priority,
                customer_email=customer_email,
                priority_overridden=priority_overridden,
                original_priority=original_priority,
                system_priority=system_priority,
            )
            EmailService().send_email(
                to_email=payload.to_email,
                subject=payload.subject,
                html_body=payload.html_body,
                text_body=payload.text_body,
            )
            logger.info(
                f"[team_lead_new_ticket] Email sent → {lead['email']} | ticket={ticket_number}"
            )

            # ── In-app notification ───────────────────────────────────────────
            async with session.begin():
                await NotificationService(session).ticket_created_team_lead(
                    recipient_id        = lead["id"],
                    ticket_id           = ticket_id,
                    ticket_number       = ticket_number,
                    ticket_title        = ticket_title,
                    severity            = severity,
                    priority            = priority,
                    priority_overridden = priority_overridden,
                )
            logger.info(
                f"[team_lead_new_ticket] In-app sent → lead_id={lead['id']} | "
                f"ticket={ticket_number}"
            )
    finally:
        await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# DB-only task — NEW → ACKNOWLEDGED
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="src.core.celery.workers.email_tasks.acknowledge_ticket_status_task",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="ticket_email",
)
def acknowledge_ticket_status_task(self, *, ticket_id: int) -> None:
    """Transitions ticket NEW → ACKNOWLEDGED in DB. Guard: skips if not NEW."""
    try:
        asyncio.run(_transition_to_acknowledged(ticket_id))
    except Exception as exc:
        logger.error(
            f"Status update failed | ticket_id={ticket_id} | "
            f"attempt={self.request.retries + 1} | {exc}"
        )
        raise self.retry(exc=exc)


async def _transition_to_acknowledged(ticket_id: int) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import update, select
    from src.constants.ticket_constants import TicketStatus
    from src.constants.sla_constants import AuditAction
    from src.data.models.postgres.ticket_model import Ticket
    from src.data.models.postgres.ticket_audit_model import TicketAudit
    from src.config.settings import settings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(Ticket.status, Ticket.ticket_number).where(Ticket.id == ticket_id)
                )
                row = result.one_or_none()
                if row is None:
                    logger.warning(f"Ticket {ticket_id} not found")
                    return

                current_status, ticket_number = row
                if current_status != TicketStatus.NEW:
                    logger.info(
                        f"Skipping | ticket_id={ticket_id} | "
                        f"status={current_status} (not NEW)"
                    )
                    return

                await session.execute(
                    update(Ticket)
                    .where(Ticket.id == ticket_id)
                    .values(status=TicketStatus.ACKNOWLEDGED)
                )
                session.add(TicketAudit(
                    ticket_id  = ticket_id,
                    action     = AuditAction.STATUS_CHANGED,
                    actor_id   = 0,
                    actor_role = "system",
                    old_value  = {"status": TicketStatus.NEW.value},
                    new_value  = {"status": TicketStatus.ACKNOWLEDGED.value},
                ))
        logger.info(f"Ticket {ticket_id} → ACKNOWLEDGED (ticket_number={ticket_number})")
    finally:
        await engine.dispose()


# ═════════════════════════════════════════════════════════════════════════════
# Email builder functions
# Each returns an EmailPayload ready to pass to send_notification_task.delay()
# ═════════════════════════════════════════════════════════════════════════════

def build_acknowledgement_email(
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
    severity:      str,
    priority:      str,
    priority_overridden: bool,
    original_priority:   Optional[str]   = None,
    system_priority:     Optional[str]   = None,
    sla_resolution_hrs:  Optional[float] = None,
) -> EmailPayload:
    """Ticket received confirmation with optional priority override notice."""

    override_html = override_text = ""
    if priority_overridden and original_priority and system_priority:
        override_html = f"""
        <tr><td colspan="2" style="padding:0 0 16px;">
          <div style="background:#FFF7ED;border:1px solid #FED7AA;
                      border-radius:8px;padding:14px 18px;">
            <p style="margin:0 0 6px;font-size:13px;font-weight:700;color:#C2410C;">
              ⚠ Priority Adjustment Notice
            </p>
            <p style="margin:0;font-size:13px;color:#7C2D12;line-height:1.6;">
              Your requested priority <strong>{original_priority.upper()}</strong> was
              adjusted to <strong>{system_priority.upper()}</strong> by our automated
              severity analysis (<strong>{severity.upper()}</strong> detected).
            </p>
          </div>
        </td></tr>"""
        override_text = (
            f"\n⚠ Priority adjusted: {original_priority.upper()} → "
            f"{system_priority.upper()} ({severity.upper()} severity)\n"
        )

    sla_html = sla_text = ""
    if sla_resolution_hrs is not None:
        hrs = sla_resolution_hrs
        sla_html = f"""
        <tr>
          <td style="padding:6px 0;font-size:13px;color:#64748B;width:160px;">
            Expected resolution</td>
          <td style="padding:6px 0;font-size:13px;color:#0F172A;font-weight:600;">
            Within {hrs:.0f} hour{"s" if hrs != 1 else ""}</td>
        </tr>"""
        sla_text = f"Expected resolution : Within {hrs:.0f} hour{'s' if hrs != 1 else ''}\n"

    sev_colours = {
        "critical": ("#FEF2F2", "#DC2626"), "high": ("#FFF7ED", "#D97706"),
        "medium":   ("#FFFBEB", "#CA8A04"), "low":  ("#F0FDF4", "#16A34A"),
    }
    sev_bg, sev_col = sev_colours.get(severity.lower(), ("#F1F5F9", "#334155"))

    steps_html = "".join(_step(n, t, d) for n, t, d in [
        ("1", "Acknowledgement", "Your ticket is queued and visible to our support team."),
        ("2", "Assignment",      "An agent will be assigned based on severity and availability."),
        ("3", "Resolution",      "The agent will work on your issue and keep you updated."),
    ])

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 0;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">We've received your request</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, your ticket has been submitted successfully.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Severity", f'<span style="font-size:12px;font-weight:700;background:{sev_bg};color:{sev_col};padding:2px 10px;border-radius:100px;text-transform:uppercase;">{severity}</span>')}
      {_row("Priority", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{priority.upper()}</span>')}
      {sla_html}{override_html}
    </table>
    <p style="margin:0 0 12px;font-size:13px;font-weight:700;color:#0F172A;text-transform:uppercase;letter-spacing:0.06em;">What happens next?</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px;">{steps_html}</table>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\nWe've received your support request.\n\n"
        f"Ticket : {ticket_number}\nSubject : {ticket_title}\n"
        f"Severity: {severity.upper()}\nPriority: {priority.upper()}\n"
        f"{sla_text}{override_text}\n— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] We've received your support request",
        html_body=html_body,
        text_body=text_body,
    )


def build_status_update_email(
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
    old_status:    str,
    new_status:    str,
    actor_role:    str,
) -> EmailPayload:
    """Status transition notification."""

    updated_by = actor_role.replace("_", " ").title()

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">Ticket status updated</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, your ticket status was updated by
      our <strong>{updated_by}</strong>.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Previous status", _status_pill(old_status))}
      {_row("New status",      _status_pill(new_status))}
    </table>
    {_status_message_block(new_status)}
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\nYour ticket status was updated by {updated_by}.\n\n"
        f"Ticket  : {ticket_number}\nSubject : {ticket_title}\n"
        f"Before  : {old_status.replace('_',' ').upper()}\n"
        f"After   : {new_status.replace('_',' ').upper()}\n\n"
        f"— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] Your ticket status has been updated",
        html_body=html_body,
        text_body=text_body,
    )


def build_assignment_email(
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
) -> EmailPayload:
    """Agent assigned notification."""

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">
      You've been assigned a support agent</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, a support agent has been assigned to
      your ticket and will begin working on your issue shortly.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Status", _status_pill("assigned"))}
    </table>
    <div style="background:#F0FDF4;border-radius:8px;padding:16px 20px;">
      <p style="margin:0 0 4px;font-size:14px;font-weight:700;color:#15803D;">
        🧑‍💻 What happens next?</p>
      <p style="margin:0;font-size:13px;color:#166534;line-height:1.6;">
        Your agent will review your ticket and reach out via comments if they
        need additional information.</p>
    </div>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\nA support agent has been assigned to your ticket.\n\n"
        f"Ticket  : {ticket_number}\nSubject : {ticket_title}\nStatus  : ASSIGNED\n\n"
        f"Your agent will follow up shortly.\n\n— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] A support agent has been assigned to your ticket",
        html_body=html_body,
        text_body=text_body,
    )


def build_comment_email(
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
    author_role:   str,      # "CUSTOMER" | "SUPPORT_AGENT" | "TEAM_LEAD"
    comment_body:  str,
) -> EmailPayload:
    """
    New comment notification.
    Sent to the *other* party:
      - customer posts → agent receives
      - agent posts    → customer receives
    """
    sender_label = "customer" if author_role.upper() == "CUSTOMER" else "your support agent"
    preview = comment_body[:300] + ("..." if len(comment_body) > 300 else "")

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">New comment on your ticket</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, {sender_label} posted a comment on your ticket.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Posted by", f'<span style="font-size:13px;color:#0F172A;text-transform:capitalize;">{sender_label}</span>')}
    </table>
    <div style="background:#F8FAFC;border-left:3px solid #9D65F5;border-radius:0 8px 8px 0;padding:16px 20px;margin-bottom:28px;">
      <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#94A3B8;">Comment</p>
      <p style="margin:0;font-size:14px;color:#334155;line-height:1.7;">{preview}</p>
    </div>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\n{sender_label.capitalize()} posted a comment on your ticket.\n\n"
        f"Ticket  : {ticket_number}\nSubject : {ticket_title}\n\n"
        f"Comment:\n{comment_body[:300]}\n\n— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] New comment on your ticket",
        html_body=html_body,
        text_body=text_body,
    )


def build_sla_breach_email(
    *,
    to_email:          str,
    lead_name:         str,
    ticket_number:     str,
    ticket_title:      str,
    severity:          str,
    priority:          str,
    customer_email:    str,
    breached_at:       str,
    assigned_agent_id: Optional[int] = None,
) -> EmailPayload:
    """SLA breach alert email for the team lead."""

    agent_html = (
        f'<span style="font-size:13px;font-weight:600;color:#0F172A;">Agent #{assigned_agent_id}</span>'
        if assigned_agent_id
        else '<span style="font-size:13px;font-weight:600;color:#EF4444;">Unassigned</span>'
    )

    sev_colours = {
        "critical": ("#FEF2F2", "#DC2626"), "high": ("#FFF7ED", "#D97706"),
        "medium":   ("#FFFBEB", "#CA8A04"), "low":  ("#F0FDF4", "#16A34A"),
    }
    sev_bg, sev_col = sev_colours.get(severity.lower(), ("#F1F5F9", "#334155"))

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="background:#FEF2F2;border-bottom:2px solid #FCA5A5;padding:18px 40px;">
    <p style="margin:0;font-size:16px;font-weight:800;color:#DC2626;">🚨 SLA Breach — Immediate Action Required</p>
    <p style="margin:4px 0 0;font-size:13px;color:#B91C1C;">A ticket has exceeded its resolution SLA deadline.</p>
  </td></tr>
  <tr><td style="padding:36px 40px 0;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">SLA Breach Alert</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{lead_name}</strong>, a ticket in your team has breached its SLA deadline
      and has been automatically escalated to you for review.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Customer", f'<span style="font-size:13px;color:#0F172A;">{customer_email}</span>')}
      {_row("Severity", f'<span style="font-size:12px;font-weight:700;background:{sev_bg};color:{sev_col};padding:2px 10px;border-radius:100px;text-transform:uppercase;">{severity}</span>')}
      {_row("Priority", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{priority.upper()}</span>')}
      {_row("Assigned agent", agent_html)}
      {_row("Breached at", f'<span style="font-size:13px;font-weight:600;color:#DC2626;">{breached_at}</span>')}
    </table>
    <div style="background:#FEF2F2;border-radius:8px;padding:16px 20px;margin-bottom:32px;">
      <p style="margin:0 0 10px;font-size:14px;font-weight:700;color:#DC2626;">⚡ Recommended Actions</p>
      <ul style="margin:0;padding-left:18px;font-size:13px;color:#B91C1C;line-height:2;">
        <li>Review the ticket immediately in the Team Lead portal</li>
        <li>Reassign to an available agent if unassigned or overloaded</li>
        <li>Communicate an updated resolution timeline to the customer</li>
      </ul>
    </div>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {lead_name},\n\n🚨 SLA BREACH ALERT\n\n"
        f"Ticket    : {ticket_number}\n"
        f"Subject   : {ticket_title}\n"
        f"Customer  : {customer_email}\n"
        f"Severity  : {severity.upper()}\n"
        f"Priority  : {priority.upper()}\n"
        f"Breached  : {breached_at}\n\n"
        f"Immediate action required.\n\n— TicketingGenie SLA Monitor"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"🚨 SLA BREACH [{ticket_number}] — {ticket_title}",
        html_body=html_body,
        text_body=text_body,
    )


def build_lead_ticket_created_email(
    *,
    to_email:            str,
    lead_name:           str,
    ticket_number:       str,
    ticket_title:        str,
    severity:            str,
    priority:            str,
    customer_email:      str,
    priority_overridden: bool          = False,
    original_priority:   Optional[str] = None,
    system_priority:     Optional[str] = None,
) -> EmailPayload:
    """Email to team lead when a new ticket is routed to their team."""
    sev_colours = {
        "critical": ("#FEF2F2", "#DC2626"), "high": ("#FFF7ED", "#D97706"),
        "medium":   ("#FFFBEB", "#CA8A04"), "low":  ("#F0FDF4", "#16A34A"),
    }
    sev_bg, sev_col = sev_colours.get(severity.lower(), ("#F1F5F9", "#334155"))

    override_html = override_text = ""
    if priority_overridden and original_priority and system_priority:
        override_html = f"""
        <tr><td colspan="2" style="padding:0 0 16px;">
          <div style="background:#FFF7ED;border:1px solid #FED7AA;
                      border-radius:8px;padding:14px 18px;">
            <p style="margin:0 0 6px;font-size:13px;font-weight:700;color:#C2410C;">
              ⚠ Priority Auto-Adjusted
            </p>
            <p style="margin:0;font-size:13px;color:#7C2D12;line-height:1.6;">
              Customer requested <strong>{original_priority.upper()}</strong> — system
              overrode to <strong>{system_priority.upper()}</strong> based on
              <strong>{severity.upper()}</strong> severity analysis.
            </p>
          </div>
        </td></tr>"""
        override_text = (
            f"\n⚠ Priority adjusted: {original_priority.upper()} → "
            f"{system_priority.upper()} ({severity.upper()} severity)\n"
        )

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">New ticket for your team</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{lead_name}</strong>, a new support ticket has been routed to your team
      and needs an agent assignment.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Customer", f'<span style="font-size:13px;color:#0F172A;">{customer_email}</span>')}
      {_row("Severity", f'<span style="font-size:12px;font-weight:700;background:{sev_bg};color:{sev_col};padding:2px 10px;border-radius:100px;text-transform:uppercase;">{severity}</span>')}
      {_row("Priority", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{priority.upper()}</span>')}
      {_row("Status", _status_pill("acknowledged"))}
      {override_html}
    </table>
    <div style="background:#EFF6FF;border-radius:8px;padding:16px 20px;">
      <p style="margin:0 0 4px;font-size:14px;font-weight:700;color:#1D4ED8;">📋 Action required</p>
      <p style="margin:0;font-size:13px;color:#1E40AF;line-height:1.6;">
        Please log in to the Team Lead portal and assign an available support agent to this ticket.</p>
    </div>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {lead_name},\n\nA new ticket has been routed to your team.\n\n"
        f"Ticket   : {ticket_number}\nSubject  : {ticket_title}\n"
        f"Customer : {customer_email}\n"
        f"Severity : {severity.upper()}\nPriority : {priority.upper()}\n"
        f"{override_text}\n"
        f"Please assign an agent via the Team Lead portal.\n\n— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] New ticket assigned to your team — action required",
        html_body=html_body,
        text_body=text_body,
    )


def build_agent_assigned_email(
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
    severity:      str,
    priority:      str,
) -> EmailPayload:
    """Email to the support agent when a ticket is assigned to them."""
    sev_colours = {
        "critical": ("#FEF2F2", "#DC2626"), "high": ("#FFF7ED", "#D97706"),
        "medium":   ("#FFFBEB", "#CA8A04"), "low":  ("#F0FDF4", "#16A34A"),
    }
    sev_bg, sev_col = sev_colours.get(severity.lower(), ("#F1F5F9", "#334155"))

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">
      A ticket has been assigned to you</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, your team lead has assigned the following
      support ticket to you. Please review and start working when ready.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Severity", f'<span style="font-size:12px;font-weight:700;background:{sev_bg};color:{sev_col};padding:2px 10px;border-radius:100px;text-transform:uppercase;">{severity}</span>')}
      {_row("Priority", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{priority.upper()}</span>')}
      {_row("Status", _status_pill("assigned"))}
    </table>
    <div style="background:#F0FDF4;border-radius:8px;padding:16px 20px;">
      <p style="margin:0 0 4px;font-size:14px;font-weight:700;color:#15803D;">
        🧑‍💻 Next step</p>
      <p style="margin:0;font-size:13px;color:#166534;line-height:1.6;">
        Log in to the agent portal, open the ticket, and click <strong>Start Working</strong>
        to start the SLA response timer.</p>
    </div>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\nA ticket has been assigned to you.\n\n"
        f"Ticket   : {ticket_number}\nSubject  : {ticket_title}\n"
        f"Severity : {severity.upper()}\nPriority : {priority.upper()}\n\n"
        f"Log in and click 'Start Working' to begin.\n\n— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] Ticket assigned to you — please start working",
        html_body=html_body,
        text_body=text_body,
    )


def build_escalation_customer_notice_email(
    *,
    to_email:       str,
    ticket_number:  str,
    ticket_title:   str,
    current_status: str,
) -> EmailPayload:
    """
    Reassuring email to the customer when their ticket is escalated.
    Does NOT expose SLA or internal escalation details.
    """
    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">We're still on it</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, we wanted to update you on your support ticket.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Current status", _status_pill(current_status))}
    </table>
    <div style="background:#FFF7ED;border-radius:8px;padding:16px 20px;margin-bottom:16px;">
      <p style="margin:0 0 4px;font-size:14px;font-weight:700;color:#C2410C;">
        ⏳ Update on your request</p>
      <p style="margin:0;font-size:13px;color:#7C2D12;line-height:1.6;">
        We are still actively working on your ticket and it has been escalated for
        priority attention. Our team is committed to resolving this as quickly as possible.
        We will send you a further update once there is progress.</p>
    </div>
    <p style="font-size:12px;color:#94A3B8;margin:0;">
      Thank you for your patience. If you have any additional information that may help,
      please add a comment to your ticket.</p>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\nWe wanted to update you on your support ticket.\n\n"
        f"Ticket  : {ticket_number}\nSubject : {ticket_title}\n"
        f"Status  : {current_status.replace('_', ' ').upper()}\n\n"
        f"We are still actively working on your request and it has been escalated "
        f"for priority attention. We will update you as soon as there is progress.\n\n"
        f"Thank you for your patience.\n\n— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] Update on your support request — we're still working on it",
        html_body=html_body,
        text_body=text_body,
    )


def build_escalation_agent_reassignment_email(
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
    severity:      str,
    priority:      str,
    current_status: str,
    response_due_at: Optional[str] = None,
) -> EmailPayload:
    """Email to the new agent after an escalated ticket is reassigned to them."""
    sev_colours = {
        "critical": ("#FEF2F2", "#DC2626"), "high": ("#FFF7ED", "#D97706"),
        "medium":   ("#FFFBEB", "#CA8A04"), "low":  ("#F0FDF4", "#16A34A"),
    }
    sev_bg, sev_col = sev_colours.get(severity.lower(), ("#F1F5F9", "#334155"))
    deadline_html = (
        _row("Response deadline", f'<span style="font-size:13px;font-weight:700;color:#DC2626;">{response_due_at}</span>')
        if response_due_at else ""
    )

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="background:#FEF2F2;border-bottom:2px solid #FCA5A5;padding:14px 40px;">
    <p style="margin:0;font-size:14px;font-weight:800;color:#DC2626;">
      🚨 Escalated ticket reassigned to you — immediate action required</p>
  </td></tr>
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">
      Escalated ticket assigned to you</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, an escalated ticket has been reassigned to you
      by your team lead. SLA timers start when you click <strong>Start Working</strong>.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Severity", f'<span style="font-size:12px;font-weight:700;background:{sev_bg};color:{sev_col};padding:2px 10px;border-radius:100px;text-transform:uppercase;">{severity}</span>')}
      {_row("Priority", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{priority.upper()}</span>')}
      {_row("Current status", _status_pill(current_status))}
      {deadline_html}
    </table>
    <div style="background:#FEF2F2;border-radius:8px;padding:16px 20px;">
      <p style="margin:0 0 10px;font-size:14px;font-weight:700;color:#DC2626;">⚡ What you need to do</p>
      <ul style="margin:0;padding-left:18px;font-size:13px;color:#B91C1C;line-height:2;">
        <li>Review the full ticket history and all prior comments</li>
        <li>Click <strong>Start Working</strong> immediately to stop the response SLA timer</li>
        <li>Keep the customer updated via comments</li>
        <li>Resolve the ticket within the resolution deadline</li>
      </ul>
    </div>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\n🚨 ESCALATED TICKET REASSIGNED TO YOU\n\n"
        f"Ticket   : {ticket_number}\nSubject  : {ticket_title}\n"
        f"Severity : {severity.upper()}\nPriority : {priority.upper()}\n"
        f"Status   : {current_status.replace('_', ' ').upper()}\n"
        + (f"Response deadline : {response_due_at}\n" if response_due_at else "")
        + f"\nClick 'Start Working' immediately to begin SLA tracking.\n\n"
        f"— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"🚨 [{ticket_number}] Escalated ticket reassigned to you — start working now",
        html_body=html_body,
        text_body=text_body,
    )


def build_sla_breach_customer_email(
    *,
    to_email:      str,
    ticket_number: str,
    ticket_title:  str,
    current_status: str,
    is_second_breach: bool = False,
) -> EmailPayload:
    """
    Email to customer on SLA breach/escalation.
    is_second_breach=True gives a slightly different message for the second breach.
    """
    if is_second_breach:
        heading   = "We sincerely apologise for the continued delay"
        body_text = (
            "We are aware that your ticket has experienced an extended delay and we "
            "sincerely apologise. Our team is treating your case as a top priority. "
            "We are taking immediate action to resolve this for you."
        )
        subject_prefix = "Important update"
    else:
        heading   = "We're still working on your ticket"
        body_text = (
            "Your ticket has been escalated for priority attention. "
            "We are committed to resolving this as quickly as possible and will "
            "update you as soon as there is meaningful progress."
        )
        subject_prefix = "Update"

    html_body = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head><body style="margin:0;padding:0;background:#F8FAFC;font-family:'Inter',system-ui,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFC;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#FFF;border-radius:12px;border:1px solid #E2E8F0;overflow:hidden;max-width:600px;width:100%;">
  {_header()}
  <tr><td style="padding:36px 40px 32px;">
    <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#0F172A;">{heading}</p>
    <p style="margin:0 0 28px;font-size:14px;color:#64748B;line-height:1.6;">
      Hi <strong>{to_email}</strong>, here is an update on your open support ticket.</p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:20px 24px;margin-bottom:24px;">
      {_detail_header()}
      {_row("Ticket number", f'<span style="font-size:13px;font-weight:700;color:#2563EB;font-family:monospace;background:#EFF6FF;padding:2px 8px;border-radius:4px;">{ticket_number}</span>')}
      {_row("Subject", f'<span style="font-size:13px;font-weight:600;color:#0F172A;">{ticket_title}</span>')}
      {_row("Current status", _status_pill(current_status))}
    </table>
    <div style="background:#FFF7ED;border-radius:8px;padding:16px 20px;">
      <p style="margin:0 0 4px;font-size:14px;font-weight:700;color:#C2410C;">⏳ Status update</p>
      <p style="margin:0;font-size:13px;color:#7C2D12;line-height:1.6;">{body_text}</p>
    </div>
  </td></tr>
  {_footer()}
</table></td></tr></table></body></html>"""

    text_body = (
        f"Hi {to_email},\n\n{heading}\n\n"
        f"Ticket  : {ticket_number}\nSubject : {ticket_title}\n"
        f"Status  : {current_status.replace('_', ' ').upper()}\n\n"
        f"{body_text}\n\nThank you for your patience.\n\n— TicketingGenie Support"
    )
    return EmailPayload(
        to_email=to_email,
        subject=f"[{ticket_number}] {subject_prefix}: your support ticket",
        html_body=html_body,
        text_body=text_body,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Shared HTML helpers
# ═════════════════════════════════════════════════════════════════════════════

def _header() -> str:
    return """
    <tr><td style="background:linear-gradient(135deg,#9D65F5 0%,#3D1F8C 100%);padding:32px 40px;">
      <p style="margin:0;font-size:22px;font-weight:800;color:#FFF;letter-spacing:-0.02em;">
        Ticketing<span style="color:#C4B5FD;">Genie</span></p>
      <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.75);">Support Ticket System</p>
    </td></tr>"""


def _footer() -> str:
    return """
    <tr><td style="padding:20px 40px 28px;border-top:1px solid #F1F5F9;">
      <p style="margin:0 0 4px;font-size:12px;color:#94A3B8;line-height:1.6;">
        This is an automated message from TicketingGenie. Please do not reply.</p>
      <p style="margin:0;font-size:12px;color:#CBD5E1;">© TicketingGenie · All rights reserved</p>
    </td></tr>"""


def _detail_header() -> str:
    return """
    <tr><td colspan="2" style="padding:0 0 14px;">
      <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:0.08em;
                 text-transform:uppercase;color:#94A3B8;">Ticket Details</p>
    </td></tr>"""


def _row(label: str, value_html: str) -> str:
    return f"""
    <tr>
      <td style="padding:6px 0;font-size:13px;color:#64748B;width:160px;">{label}</td>
      <td style="padding:6px 0;">{value_html}</td>
    </tr>"""


def _step(number: str, title: str, description: str) -> str:
    return f"""
    <tr>
      <td style="padding:6px 0;vertical-align:top;width:32px;">
        <span style="display:inline-flex;align-items:center;justify-content:center;
                      width:24px;height:24px;border-radius:50%;
                      background:#EFF6FF;color:#2563EB;font-size:11px;font-weight:800;">
          {number}</span></td>
      <td style="padding:6px 0 6px 10px;vertical-align:top;">
        <p style="margin:0;font-size:13px;font-weight:600;color:#0F172A;">{title}</p>
        <p style="margin:2px 0 0;font-size:12px;color:#64748B;line-height:1.5;">{description}</p>
      </td>
    </tr>"""


def _status_pill(status: str) -> str:
    colours = {
        "new":          ("#EFF6FF", "#2563EB"),
        "acknowledged": ("#F0FDF4", "#16A34A"),
        "assigned":     ("#EFF6FF", "#2563EB"),
        "in_progress":  ("#FFF7ED", "#D97706"),
        "on_hold":      ("#F8FAFC", "#64748B"),
        "resolved":     ("#F0FDF4", "#15803D"),
        "closed":       ("#F1F5F9", "#334155"),
    }
    bg, col = colours.get(status.lower(), ("#F1F5F9", "#334155"))
    label   = status.replace("_", " ").upper()
    return (
        f'<span style="font-size:12px;font-weight:700;background:{bg};color:{col};'
        f'padding:3px 12px;border-radius:100px;">{label}</span>'
    )


def _status_message_block(new_status: str) -> str:
    messages = {
        "in_progress": ("🔧", "#EFF6FF", "#1D4ED8",
            "Work has begun on your ticket",
            "A support agent is actively working on your issue."),
        "on_hold":     ("⏸", "#FFF7ED", "#C2410C",
            "Your ticket is temporarily on hold",
            "We may need additional info or are waiting on a dependency."),
        "resolved":    ("✅", "#F0FDF4", "#15803D",
            "Your ticket has been resolved",
            "If your issue hasn't been fully resolved, please contact support."),
        "closed":      ("🔒", "#F1F5F9", "#334155",
            "Your ticket has been closed",
            "If you need further assistance, please raise a new ticket."),
    }
    data = messages.get(new_status.lower())
    if not data:
        return ""
    icon, bg, col, title, body = data
    return f"""
    <div style="background:{bg};border-radius:8px;padding:16px 20px;">
      <p style="margin:0 0 4px;font-size:14px;font-weight:700;color:{col};">
        {icon} {title}</p>
      <p style="margin:0;font-size:13px;color:{col};opacity:0.85;line-height:1.6;">
        {body}</p>
    </div>"""