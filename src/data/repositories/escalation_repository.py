"""Escalation repository.
File: src/data/repositories/escalation_repository.py
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.escalation_model import Escalation


class EscalationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        ticket_id:                 int,
        old_agent_id:              int,
        reason:                    str = "sla_breach",
        escalated_by:              str = "system",
        notes:                     str | None = None,
        escalated_resolution_mins: int | None = None,
    ) -> Escalation:
        esc = Escalation(
            ticket_id                 = ticket_id,
            old_agent_id              = old_agent_id,
            reason                    = reason,
            escalated_by              = escalated_by,
            notes                     = notes,
            escalated_resolution_mins = escalated_resolution_mins,
            escalated_at              = datetime.now(timezone.utc),
        )
        self.db.add(esc)
        await self.db.flush()
        await self.db.refresh(esc)
        return esc

    async def get_by_ticket(self, ticket_id: int) -> Escalation | None:
        result = await self.db.execute(
            select(Escalation).where(Escalation.ticket_id == ticket_id)
        )
        return result.scalar_one_or_none()

    async def assign_new_agent(
        self,
        escalation:                Escalation,
        new_agent_id:              int,
        escalated_resolution_mins: int | None = None,
    ) -> Escalation:
        """Called when team lead reassigns the escalated ticket."""
        escalation.new_agent_id              = new_agent_id
        escalation.reassigned_at             = datetime.now(timezone.utc)
        escalation.escalated_resolution_mins = escalated_resolution_mins
        await self.db.flush()
        await self.db.refresh(escalation)
        return escalation