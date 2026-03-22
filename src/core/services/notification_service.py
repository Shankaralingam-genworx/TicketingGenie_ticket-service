"""In-app notification creation and SSE publishing via Redis."""

import json
from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.data.models.postgres.notification_model import NotificationActor, NotificationType
from src.data.repositories.notification_repository import NotificationRepository
from src.schemas.notification_schema import NotificationResponse
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class NotificationService:

    def __init__(self, db: AsyncSession):
        self.db   = db
        self.repo = NotificationRepository(db)

    # ── Core ──────────────────────────────────────────────────────────────────

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
        """Persist notification and push SSE event to Redis."""
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
        """Push notification to Redis SSE channel. Never raises — failure is logged only."""
        try:
            unread  = await self.repo.unread_count(user_id)
            payload = json.dumps({
                "unread_count": unread,
                "notification": notif.model_dump(mode="json"),
            })
            redis = aioredis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
            async with redis:
                await redis.publish(f"notifications:{user_id}", payload)
        except Exception as exc:
            # Non-fatal — never block ticket/comment flows over a notification failure
            logger.warning("notification_publish_failed", user_id=user_id, error=str(exc))

    # ── Customer notifications ────────────────────────────────────────────────

    async def ticket_created(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """Notify customer when their ticket is received."""
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

    async def ticket_assigned(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """Notify customer when an agent is assigned to their ticket."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.TICKET_ASSIGNED,
            title          = f"Agent assigned to {ticket_number}",
            message        = (
                f"A support agent has been assigned to \"{ticket_title}\" "
                f"and will begin working on it shortly."
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
        """Notify customer when ticket status changes."""
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

    async def customer_escalated(
        self,
        *,
        recipient_id:   int,
        ticket_id:      int,
        ticket_number:  str,
        ticket_title:   str,
        current_status: str,
    ) -> None:
        """
        Notify customer when their ticket is escalated.
        Deliberately reassuring — does not expose internal SLA details.
        """
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.CUSTOMER,
            type           = NotificationType.TICKET_ESCALATED,
            title          = f"Update on your ticket {ticket_number}",
            message        = (
                f"We are still actively working on \"{ticket_title}\". "
                f"It has been escalated for priority attention. "
                f"Current status: {current_status.replace('_', ' ').upper()}. "
                f"We will update you as soon as there is progress."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def sla_breached_customer(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """Notify customer when their ticket breaches SLA (apologetic, no internals)."""
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

    async def comment_received(
        self,
        *,
        recipient_id:   int,
        recipient_role: NotificationActor,
        ticket_id:      int,
        ticket_number:  str,
        ticket_title:   str,
        author_role:    str,
        preview:        str,
    ) -> None:
        """Notify recipient when a comment is posted on a ticket."""
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

    # ── Team lead notifications ───────────────────────────────────────────────

    async def team_lead_new_ticket(
        self,
        *,
        recipient_id:        int,
        ticket_id:           int,
        ticket_number:       str,
        ticket_title:        str,
        severity:            str,
        priority:            str,
        priority_overridden: bool = False,
    ) -> None:
        """Notify team lead when a new ticket lands in their team queue."""
        override_note = (
            f" (priority auto-adjusted to {priority.upper()} by severity analysis)"
            if priority_overridden else ""
        )
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.TEAM_LEAD,
            type           = NotificationType.TICKET_CREATED,
            title          = f"New ticket {ticket_number} for your team",
            message        = (
                f"Ticket \"{ticket_title}\" submitted with {severity.upper()} severity"
                f"{override_note}. Please assign it to an agent."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def sla_breached(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
        severity:      str,
        priority:      str,
        breached_at:   datetime,
    ) -> None:
        """Notify team lead when a ticket breaches SLA."""
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

    # ── Agent notifications ───────────────────────────────────────────────────

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
                f"You have been assigned \"{ticket_title}\". "
                f"Click 'Start Working' when you begin."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )

    async def escalation_reassigned_agent(
        self,
        *,
        recipient_id:  int,
        ticket_id:     int,
        ticket_number: str,
        ticket_title:  str,
    ) -> None:
        """Notify agent when an escalated ticket is reassigned to them."""
        await self.notify(
            recipient_id   = recipient_id,
            recipient_role = NotificationActor.SUPPORT_AGENT,
            type           = NotificationType.TICKET_REASSIGNED,
            title          = f"Escalated ticket {ticket_number} reassigned to you",
            message        = (
                f"The escalated ticket \"{ticket_title}\" has been reassigned to you. "
                f"Review the full history and click 'Start Working' immediately — "
                f"SLA timers are now active."
            ),
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
        )