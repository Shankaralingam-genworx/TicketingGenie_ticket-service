"""
Ticket repository.
File: src/data/repositories/ticket_repository.py

Complete replacement of the existing file.
All original methods are kept UNCHANGED.
Five new methods are added at the bottom for the team-lead filtered views:

  get_team_queue_filtered          – NEW+ACKNOWLEDGED, unassigned
  count_team_queue_filtered        – total count for pagination
  get_team_tickets_filtered        – all team tickets
  count_team_tickets_filtered      – total count for pagination
  get_agent_tickets_filtered       – tickets for one agent (workload detail)
  get_agent_workload_summary       – per-agent × per-status counts
"""

import math
from sqlalchemy import select, or_, case, asc, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.constants.ticket_constants import TicketStatus
from src.data.models.postgres.ticket_model import Ticket
from src.schemas.ticket_filter_schema import TicketFilterParams


# ── Sort-order expressions ────────────────────────────────────────────────────
# Lower number = higher urgency, used with ASC

def _priority_case():
    return case(
        (Ticket.priority == "p1", 1),
        (Ticket.priority == "p2", 2),
        (Ticket.priority == "p3", 3),
        (Ticket.priority == "p4", 4),
        else_=5,
    )

def _severity_case():
    return case(
        (Ticket.severity == "critical", 1),
        (Ticket.severity == "high",     2),
        (Ticket.severity == "medium",   3),
        (Ticket.severity == "low",      4),
        else_=5,
    )


class TicketRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _base_query(self):
        return select(Ticket).options(
            selectinload(Ticket.issue),
            selectinload(Ticket.sla),
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _apply_where(self, stmt, f: TicketFilterParams):
        """Attach search + filter WHERE clauses. No ORDER BY or LIMIT here."""

        if f.search:
            kw = f"%{f.search.strip()}%"
            stmt = stmt.where(
                or_(
                    Ticket.ticket_number.ilike(kw),
                    Ticket.title.ilike(kw),
                    Ticket.description.ilike(kw),
                )
            )

        if f.status:
            stmt = stmt.where(Ticket.status.in_(f.status))

        if f.priority:
            stmt = stmt.where(Ticket.priority.in_(f.priority))

        if f.severity:
            stmt = stmt.where(Ticket.severity.in_(f.severity))

        if f.category:
            cat = f.category.strip()
            if cat.isdigit():
                stmt = stmt.where(Ticket.issue_id == int(cat))
            else:
                # local import avoids circular import at module level
                from src.data.models.postgres.issue_model import Issue
                stmt = stmt.join(Issue, Ticket.issue_id == Issue.id, isouter=True)
                stmt = stmt.where(Issue.name.ilike(f"%{cat}%"))

        return stmt

    def _apply_sort_page(self, stmt, f: TicketFilterParams):
        """Attach ORDER BY + OFFSET/LIMIT."""
        dir_fn = asc if f.sort_dir == "asc" else desc

        if f.sort_by == "remaining_time":
            if f.sort_dir == "asc":
                stmt = stmt.order_by(Ticket.resolution_due_at.asc().nulls_last())
            else:
                stmt = stmt.order_by(Ticket.resolution_due_at.desc().nulls_last())
        elif f.sort_by == "priority":
            stmt = stmt.order_by(dir_fn(_priority_case()))
        elif f.sort_by == "severity":
            stmt = stmt.order_by(dir_fn(_severity_case()))
        else:
            stmt = stmt.order_by(dir_fn(Ticket.created_at))

        offset = (f.page - 1) * f.per_page
        stmt = stmt.offset(offset).limit(f.per_page)
        return stmt

    # ── EXISTING METHODS (all unchanged) ────────────────────────────────────

    async def create(self, **kwargs) -> Ticket:
        ticket = Ticket(**kwargs)
        self.db.add(ticket)
        await self.db.flush()
        await self.db.refresh(ticket)
        return ticket

    async def get_by_id(self, ticket_id: int) -> Ticket | None:
        result = await self.db.execute(
            self._base_query().where(Ticket.id == ticket_id)
        )
        return result.scalar_one_or_none()

    async def get_by_number(self, ticket_number: str) -> Ticket | None:
        result = await self.db.execute(
            self._base_query().where(Ticket.ticket_number == ticket_number)
        )
        return result.scalar_one_or_none()

    async def get_by_customer(self, customer_id: int) -> list[Ticket]:
        result = await self.db.execute(
            self._base_query()
            .where(Ticket.customer_id == customer_id)
            .order_by(Ticket.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_agent(self, agent_id: int) -> list[Ticket]:
        result = await self.db.execute(
            self._base_query()
            .where(Ticket.assigned_agent_id == agent_id)
            .order_by(Ticket.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_team(self, team_id: int, status: list[TicketStatus] | None = None):
        stmt = select(Ticket).where(Ticket.team_id == team_id)
        if status:
            stmt = stmt.where(Ticket.status.in_(status))
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_unassigned_for_team(self, team_id: int) -> list[Ticket]:
        result = await self.db.execute(
            self._base_query()
            .where(
                Ticket.team_id == team_id,
                Ticket.assigned_agent_id.is_(None),
                Ticket.status == TicketStatus.NEW,
            )
            .order_by(Ticket.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_sla_watch(self, team_id: int) -> list[Ticket]:
        result = await self.db.execute(
            self._base_query()
            .where(
                Ticket.team_id == team_id,
                Ticket.status.in_([
                    TicketStatus.NEW,
                    TicketStatus.IN_PROGRESS,
                    TicketStatus.ON_HOLD,
                ]),
            )
            .order_by(Ticket.resolution_due_at.asc().nulls_last())
        )
        return list(result.scalars().all())

    async def get_all(self, status: TicketStatus | None = None) -> list[Ticket]:
        q = self._base_query()
        if status:
            q = q.where(Ticket.status == status)
        result = await self.db.execute(q.order_by(Ticket.created_at.desc()))
        return list(result.scalars().all())

    async def update(self, ticket: Ticket, **kwargs) -> Ticket:
        for k, v in kwargs.items():
            setattr(ticket, k, v)
        await self.db.flush()
        await self.db.refresh(ticket)
        return ticket

    async def count_by_status(self) -> dict[str, int]:
        result = await self.db.execute(select(Ticket.status, Ticket.id))
        rows = result.all()
        counts: dict[str, int] = {}
        for status, _ in rows:
            counts[status] = counts.get(status, 0) + 1
        return counts

    # ── NEW: filtered listing methods for team-lead views ────────────────────

    async def get_team_queue_filtered(
        self, team_id: int, f: TicketFilterParams
    ) -> list[Ticket]:
        """
        Assignment Queue — NEW + ACKNOWLEDGED tickets with no agent assigned.
        The status param from the caller is intentionally ignored here; the
        queue is always these two statuses only.
        """
        stmt = self._base_query().where(
            Ticket.team_id == team_id,
            Ticket.assigned_agent_id.is_(None),
            Ticket.status.in_([TicketStatus.NEW, TicketStatus.ACKNOWLEDGED]),
        )
        # strip status from filter so _apply_where doesn't re-add it
        f_no_status = f.model_copy(update={"status": None})
        stmt = self._apply_where(stmt, f_no_status)
        stmt = self._apply_sort_page(stmt, f_no_status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_team_queue_filtered(
        self, team_id: int, f: TicketFilterParams
    ) -> int:
        stmt = select(func.count()).select_from(Ticket).where(
            Ticket.team_id == team_id,
            Ticket.assigned_agent_id.is_(None),
            Ticket.status.in_([TicketStatus.NEW, TicketStatus.ACKNOWLEDGED]),
        )
        f_no_status = f.model_copy(update={"status": None})
        stmt = self._apply_where(stmt, f_no_status)
        return (await self.db.execute(stmt)).scalar_one()

    async def get_team_tickets_filtered(
        self, team_id: int, f: TicketFilterParams
    ) -> list[Ticket]:
        """All Tickets page — every status, full filter/search/sort/pagination."""
        stmt = self._base_query().where(Ticket.team_id == team_id)
        stmt = self._apply_where(stmt, f)
        stmt = self._apply_sort_page(stmt, f)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_team_tickets_filtered(
        self, team_id: int, f: TicketFilterParams
    ) -> int:
        stmt = select(func.count()).select_from(Ticket).where(Ticket.team_id == team_id)
        stmt = self._apply_where(stmt, f)
        return (await self.db.execute(stmt)).scalar_one()

    async def get_agent_tickets_filtered(
        self, agent_id: int, f: TicketFilterParams
    ) -> list[Ticket]:
        """Agent Workload detail — tickets for one agent with filters."""
        stmt = self._base_query().where(Ticket.assigned_agent_id == agent_id)
        stmt = self._apply_where(stmt, f)
        stmt = self._apply_sort_page(stmt, f)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_agent_tickets_filtered(
        self, agent_id: int, f: TicketFilterParams
    ) -> int:
        """Total count for agent filtered listing (used for pagination)."""
        stmt = select(func.count()).select_from(Ticket).where(
            Ticket.assigned_agent_id == agent_id
        )
        stmt = self._apply_where(stmt, f)
        return (await self.db.execute(stmt)).scalar_one()

    async def get_agent_workload_summary(self, team_id: int) -> list[dict]:
        """
        Returns raw per-agent × per-status counts for the whole team.
        Shape: [{agent_id, status, count}, ...]
        """
        stmt = (
            select(
                Ticket.assigned_agent_id,
                Ticket.status,
                func.count(Ticket.id).label("count"),
            )
            .where(
                Ticket.team_id == team_id,
                Ticket.assigned_agent_id.is_not(None),
            )
            .group_by(Ticket.assigned_agent_id, Ticket.status)
        )
        result = await self.db.execute(stmt)
        return [
            {"agent_id": row[0], "status": row[1], "count": row[2]}
            for row in result.all()
        ]