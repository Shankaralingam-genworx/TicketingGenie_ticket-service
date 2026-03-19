"""
Notification service — creates in-app notifications and publishes SSE events.

File path: src/core/services/notification_service.py

This is the single place that:
  1. Writes a row to the notifications table.
  2. Publishes to Redis so the SSE stream can push to the browser immediately.

Called from:
  - TicketService.create_ticket          → TICKET_CREATED   (customer + team_lead)
  - AssignmentService.assign_ticket      → TICKET_ASSIGNED  (customer + agent)
  - AssignmentService.reassign_escalated → TICKET_REASSIGNED (new agent)
  - TicketService.update_status          → STATUS_CHANGED   (customer)
  - CommentService.add_comment           → COMMENT_RECEIVED (agent ↔ customer)
  - sla_tasks._send_breach_notifications → SLA_BREACHED     (team_lead)
  - sla_tasks._send_breach_notifications → TICKET_ESCALATED (customer)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.data.models.postgres.notification_model import (
    NotificationActor,
    NotificationType,
)
from src.data.repositories.notification_repository import NotificationRepository
from src.schemas.notification_schema import NotificationResponse

logger = logging.getLogger("ticket.notification_service")


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db   = db
        self.repo = NotificationRepository(db)

    # ── Generic create + publish ──────────────────────────────────────────────

    async def notify(
        self,
        *,
        recipient_id:   int,
        recipient_role: NotificationActor,
        type:           NotificationType,
        title:          str,
        message:        str,
        ticket_id:      Optional[int] = None,
        ticket_number:  Optional[str] = None,
    ) -> NotificationResponse:
        """
        Write in-app notification and publish SSE event.
        Safe to call inside an open DB transaction — it only flushes, not commits.
        """
        notif = await self.repo.create(
            recipient_id   = recipient_id,
            recipient_role = recipient_role,
            type           = type,
            title          = title,
            message        = message,
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

        response = NotificationResponse.model_validate(notif)
        await self._publish(recipient_id, response)
        return response

    async def _publish(self, user_id: int, notif: NotificationResponse) -> None:
        """Publish to Redis so the open SSE stream for this user gets the event."""
        try:
            unread = await self.repo.unread_count(user_id)
            payload = json.dumps({
                "unread_count":  unread,
                "notification":  notif.model_dump(mode="json"),
            })
            redis = aioredis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
            async with redis:
                await redis.publish(f"notifications:{user_id}", payload)
        except Exception as e:
            # Never crash ticket/comment operations over a notification failure
            logger.warning(f"[NotificationService] Redis publish failed: {e}")

    # ── Convenience methods — one per NotificationType ────────────────────────

    async def ticket_created(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """In-app notification for the customer who raised the ticket."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.TICKET_CREATED,
            title          = f"Ticket {ticket_number} received",
            message        = (
                f"Your support request \"{ticket_title}\" has been received "
                f"and is being processed. We'll keep you updated."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def team_lead_new_ticket(
        self,
        *,
        recipient_id:  int,       # team lead user_id
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
        severity:      str,
        priority:      str,
    ) -> None:
        """In-app notification for the team lead when a new ticket lands in their queue."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.TEAM_LEAD,
            type           = NotificationType.TICKET_CREATED,
            title          = f"New ticket {ticket_number} in your queue",
            message        = (
                f"A new {severity.upper()} severity ticket \"{ticket_title}\" "
                f"(priority: {priority.upper()}) has been assigned to your team "
                f"and is awaiting agent assignment."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def ticket_assigned(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """In-app notification for the customer when an agent is assigned."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.TICKET_ASSIGNED,
            title          = f"Agent assigned to {ticket_number}",
            message        = (
                f"A support agent has been assigned to your ticket \"{ticket_title}\" "
                f"and will begin working on it shortly."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def agent_ticket_assigned(
        self,
        *,
        recipient_id:  int,       # support agent user_id
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """In-app notification for the support agent who was assigned the ticket."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.SUPPORT_AGENT,
            type           = NotificationType.TICKET_ASSIGNED,
            title          = f"Ticket {ticket_number} assigned to you",
            message        = (
                f"You have been assigned ticket \"{ticket_title}\". "
                f"Click 'Start Working' to begin and start the response timer."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def agent_ticket_reassigned(
        self,
        *,
        recipient_id:  int,       # new agent user_id after escalation
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """In-app notification for the new agent after escalation reassignment."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.SUPPORT_AGENT,
            type           = NotificationType.TICKET_REASSIGNED,
            title          = f"Escalated ticket {ticket_number} reassigned to you",
            message        = (
                f"An escalated ticket \"{ticket_title}\" has been reassigned to you. "
                f"Click 'Start Working' immediately — a new response timer has started."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def status_changed(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
        old_status:    str,
        new_status:    str,
    ) -> None:
        """In-app notification for the customer when ticket status changes."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.STATUS_CHANGED,
            title          = f"{ticket_number} status → {new_status.replace('_', ' ').upper()}",
            message        = (
                f"Your ticket \"{ticket_title}\" status changed from "
                f"{old_status.replace('_', ' ').upper()} to "
                f"{new_status.replace('_', ' ').upper()}."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def comment_received(
        self,
        *,
        recipient_id:   int,
        recipient_role: NotificationActor,
        ticket_id:      int,
        ticket_number:  str,
        ticket_title:   str,
        author_role:    str,
        preview:        str,        # first 80 chars of comment
    ) -> None:
        sender = "customer" if author_role.upper() == "CUSTOMER" else "support agent"
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = recipient_role,
            type           = NotificationType.COMMENT_RECEIVED,
            title          = f"New comment on {ticket_number}",
            message        = (
                f"A {sender} posted a comment on \"{ticket_title}\": "
                f"\"{preview[:80]}{'...' if len(preview) > 80 else ''}\""
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def customer_escalated(
        self,
        *,
        recipient_id:  int,       # customer user_id
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """
        In-app notification for the customer when their ticket is escalated.
        Message is reassuring — does NOT expose internal SLA details.
        """
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.TICKET_ESCALATED,
            title          = f"Update on ticket {ticket_number}",
            message        = (
                f"We are still actively working on your ticket \"{ticket_title}\". "
                f"Our team is prioritising your case and you will hear from us soon."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def sla_breached(
        self,
        *,
        recipient_id:  int,      # team lead user_id
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
        severity:      str,
        priority:      str,
        breached_at:   datetime,
    ) -> None:
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.TEAM_LEAD,
            type           = NotificationType.SLA_BREACHED,
            title          = f"🚨 SLA Breached — {ticket_number}",
            message        = (
                f"Ticket \"{ticket_title}\" breached its SLA at "
                f"{breached_at.strftime('%Y-%m-%d %H:%M UTC')}. "
                f"Severity: {severity.upper()}, Priority: {priority.upper()}. "
                f"Immediate review required."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    # ── New convenience methods ───────────────────────────────────────────────

    async def ticket_created_team_lead(
        self,
        *,
        recipient_id:        int,     # team lead user_id
        ticket_id:           int,
        ticket_number:       str,
        ticket_title:        str,
        severity:            str,
        priority:            str,
        priority_overridden: bool = False,
    ) -> None:
        """In-app notification for team lead when a new ticket lands in their team."""
        override_note = (
            f" (priority was auto-adjusted to {priority.upper()} by severity analysis)"
            if priority_overridden else ""
        )
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.TEAM_LEAD,
            type           = NotificationType.TICKET_CREATED,
            title          = f"New ticket in your team: {ticket_number}",
            message        = (
                f"Ticket \"{ticket_title}\" has been submitted with "
                f"{severity.upper()} severity{override_note}. "
                f"Please assign it to an agent."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def ticket_assigned_agent(
        self,
        *,
        recipient_id:  int,     # support agent user_id
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """In-app notification for the support agent when they are assigned a ticket."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.SUPPORT_AGENT,
            type           = NotificationType.TICKET_ASSIGNED,
            title          = f"Ticket {ticket_number} assigned to you",
            message        = (
                f"You have been assigned ticket \"{ticket_title}\". "
                f"Click 'Start Working' when you begin."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def ticket_escalated_customer(
        self,
        *,
        recipient_id:  int,     # customer user_id
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
        current_status: str,
    ) -> None:
        """
        In-app notification sent to the customer when their ticket is escalated.
        Reassures them that work is continuing without exposing internal escalation.
        """
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.STATUS_CHANGED,
            title          = f"Update on your ticket {ticket_number}",
            message        = (
                f"We're still actively working on \"{ticket_title}\". "
                f"Our team is giving it priority attention and you'll be notified "
                f"as soon as there's an update."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def sla_breached_customer(
        self,
        *,
        recipient_id:  int,     # customer user_id
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """
        In-app notification sent to the customer when an escalated ticket
        breaches SLA again (second breach — Q3/Q4 scenarios).
        """
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.SLA_BREACHED,
            title          = f"Continued work on your ticket {ticket_number}",
            message        = (
                f"Your ticket \"{ticket_title}\" is taking longer than expected. "
                f"We sincerely apologise for the delay. Our team is actively "
                f"reviewing it and you will be updated shortly."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    # ── New: team lead notified when a ticket arrives for their team ──────────

    async def ticket_created_lead(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
        severity:      str,
        priority:      str,
    ) -> None:
        """Notify team lead when a new ticket is routed to their team."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.TEAM_LEAD,
            type           = NotificationType.TICKET_ASSIGNED,
            title          = f"New ticket {ticket_number} for your team",
            message        = (
                f"A new support request \"{ticket_title}\" "
                f"({severity.upper()} severity, {priority.upper()} priority) "
                f"has been routed to your team. Please assign an agent."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    # ── New: support agent notified when a ticket is assigned to them ─────────

    async def ticket_assigned_agent(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """Notify support agent when a ticket is assigned to them."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.SUPPORT_AGENT,
            type           = NotificationType.TICKET_ASSIGNED,
            title          = f"Ticket {ticket_number} assigned to you",
            message        = (
                f"You have been assigned support ticket \"{ticket_title}\". "
                f"Please review and click \"Start Working\" when ready."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    # ── New: customer notified when their ticket is escalated ─────────────────

    async def escalation_notice_customer(
        self,
        *,
        recipient_id:   int,
        ticket_id:      int,
        ticket_number:  str,
        ticket_title:   str,
        current_status: str,
    ) -> None:
        """
        Notify customer that their ticket has been escalated.
        Message deliberately reassuring — does NOT expose internal SLA details.
        """
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.TICKET_ESCALATED,
            title          = f"Update on your ticket {ticket_number}",
            message        = (
                f"We are still actively working on your ticket \"{ticket_title}\". "
                f"It has been escalated for priority attention. "
                f"Current status: {current_status.replace('_', ' ').upper()}. "
                f"We will update you as soon as there is progress."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    # ── New: new agent notified after escalation reassignment ─────────────────

    async def escalation_reassigned_agent(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """Notify the new agent that an escalated ticket has been reassigned to them."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.SUPPORT_AGENT,
            type           = NotificationType.TICKET_REASSIGNED,
            title          = f"Escalated ticket {ticket_number} reassigned to you",
            message        = (
                f"The escalated ticket \"{ticket_title}\" has been reassigned to you. "
                f"Please review the full ticket history and click \"Start Working\" "
                f"immediately — SLA timers are now active."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )