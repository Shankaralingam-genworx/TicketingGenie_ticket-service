
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.email_thread_model import EmailThread, EmailThreadStatus


class EmailThreadRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        customer_email:   str,
        message_id:       str,
        subject:          str,
        ticket_id:        Optional[int]       = None,
        status:           EmailThreadStatus   = EmailThreadStatus.ACTIVE,
        rejection_reason: Optional[str]       = None,
    ) -> EmailThread:
        now = datetime.now(timezone.utc)
        thread = EmailThread(
            customer_email   = customer_email,
            message_id       = message_id,
            subject          = subject,
            ticket_id        = ticket_id,
            status           = status,
            rejection_reason = rejection_reason,
            last_email_at    = now,
            created_at       = now,
        )
        self.db.add(thread)
        await self.db.flush()
        await self.db.refresh(thread)
        return thread

    # ── Lookups ───────────────────────────────────────────────────────────────

    async def get_by_message_id(self, message_id: str) -> Optional[EmailThread]:
        result = await self.db.execute(
            select(EmailThread).where(EmailThread.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def get_active_by_ticket(self, ticket_id: int) -> Optional[EmailThread]:
        """Return the active thread linked to a ticket (for composing reply headers)."""
        result = await self.db.execute(
            select(EmailThread).where(
                EmailThread.ticket_id == ticket_id,
                EmailThread.status    == EmailThreadStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def find_by_references(
        self,
        in_reply_to: Optional[str],
        references:  Optional[str],
    ) -> Optional[EmailThread]:
     
        candidates: list[str] = []
        if in_reply_to:
            candidates.append(in_reply_to.strip())
        if references:
            # RFC 5322: space-separated list of Message-IDs, oldest first
            for ref in reversed(references.split()):
                ref = ref.strip()
                if ref and ref not in candidates:
                    candidates.append(ref)

        for msg_id in candidates:
            thread = await self.get_by_message_id(msg_id)
            if thread and thread.status == EmailThreadStatus.ACTIVE:
                return thread

        return None

    # ── Updates ───────────────────────────────────────────────────────────────

    async def touch(self, thread: EmailThread) -> EmailThread:
        """Bump last_email_at when a new reply arrives."""
        thread.last_email_at = datetime.now(timezone.utc)
        await self.db.flush()
        return thread

    async def close(self, thread: EmailThread) -> EmailThread:
        thread.status = EmailThreadStatus.CLOSED
        await self.db.flush()
        return thread

    async def update(self, thread: EmailThread, **kwargs) -> EmailThread:
        for k, v in kwargs.items():
            setattr(thread, k, v)
        await self.db.flush()
        await self.db.refresh(thread)
        return thread