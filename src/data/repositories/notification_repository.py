"""
Notification repository — all DB reads and writes for the notifications table.

File path: src/data/repositories/notification_repository.py
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.notification_model import (
    Notification,
    NotificationActor,
    NotificationType,
)


class NotificationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Write ─────────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        recipient_id:   int,
        recipient_role: NotificationActor,
        type:           NotificationType,
        title:          str,
        message:        str,
        ticket_id:      Optional[int] = None,
        ticket_number:  Optional[str] = None,
    ) -> Notification:
        notif = Notification(
            recipient_id   = recipient_id,
            recipient_role = recipient_role,
            type           = type,
            title          = title,
            message        = message,
            ticket_id      = ticket_id,
            ticket_number  = ticket_number,
            is_read        = False,
            created_at     = datetime.now(timezone.utc),
        )
        self.db.add(notif)
        await self.db.flush()
        await self.db.refresh(notif)
        return notif

    async def mark_read(self, notification_id: int, recipient_id: int) -> bool:
        """Mark a single notification read. Returns True if a row was updated."""
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_id == recipient_id,
                Notification.is_read == False,  # noqa: E712
            )
            .values(is_read=True, read_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def mark_all_read(self, recipient_id: int) -> int:
        """Mark every unread notification for a user read. Returns updated count."""
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.is_read == False,  # noqa: E712
            )
            .values(is_read=True, read_at=datetime.now(timezone.utc))
        )
        return result.rowcount

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_for_user(
        self,
        recipient_id: int,
        *,
        unread_only: bool = False,
        limit:       int  = 30,
        offset:      int  = 0,
    ) -> List[Notification]:
        """Fetch notifications for a user, newest first."""
        stmt = (
            select(Notification)
            .where(Notification.recipient_id == recipient_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if unread_only:
            stmt = stmt.where(Notification.is_read == False)  # noqa: E712
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def unread_count(self, recipient_id: int) -> int:
        """Count unread notifications — used for the badge on the bell icon."""
        result = await self.db.execute(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.is_read == False,  # noqa: E712
            )
        )
        return result.scalar_one()