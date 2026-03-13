"""
Notification service — creates in-app notifications and publishes SSE events.

File path: src/core/services/notification_service.py

This is the single place that:
  1. Writes a row to the notifications table.
  2. Publishes to Redis so the SSE stream can push to the browser immediately.

Called from:
  - TicketService.create_ticket          → TICKET_CREATED   (customer)
  - TicketService.assign_ticket          → TICKET_ASSIGNED   (customer)
  - TicketService.update_status          → STATUS_CHANGED    (customer)
  - CommentService.add_comment           → COMMENT_RECEIVED  (agent ↔ customer)
  - sla_tasks._send_breach_notifications → SLA_BREACHED      (team_lead)
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