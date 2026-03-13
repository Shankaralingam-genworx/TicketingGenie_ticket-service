"""TicketAudit repository."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import AuditAction
from src.data.models.postgres.ticket_audit_model import TicketAudit


class TicketAuditRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        ticket_id: int,
        action: AuditAction,
        actor_id: int,
        actor_role: str,
        old_value: dict | None = None,
        new_value: dict | None = None,
        notes: str | None = None,
    ) -> TicketAudit:
        entry = TicketAudit(
            ticket_id=ticket_id,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            old_value=old_value,
            new_value=new_value,
            notes=notes,
        )
        self.db.add(entry)
        await self.db.flush()
        return entry
