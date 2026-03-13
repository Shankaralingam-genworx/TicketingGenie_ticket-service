"""Escalation ORM model.
File: src/data/models/postgres/escalation_model.py

One row per ticket escalation event.
A ticket can only ever be escalated ONCE — enforced by the unique constraint
on ticket_id.

Full escalation lifecycle
─────────────────────────
1. Agent is assigned ticket and clicks "Start Working"
   → ticket.work_started_at = NOW, resolution_due_at computed from SLA
2. Celery beat task (check_resolution_sla) runs periodically.
   It finds tickets where:
     • resolution_due_at < NOW
     • status NOT IN (resolved, closed)
     • is_escalated = False
     • work_started_at IS NOT NULL  (agent started but didn't finish)
3. For each such ticket:
   • ticket.is_escalated  = True
   • ticket.escalated_at  = NOW
   • Escalation row created with old_agent_id
   • Audit log: ESCALATED
4. Team lead sees escalated badge on ticket in their queue.
   Lead opens it, picks a new agent and optional custom SLA window, calls
   POST /tickets/{id}/escalation/reassign.
5. AssignmentService.reassign_escalated_ticket():
   • escalation.new_agent_id      = data.new_agent_id
   • escalation.reassigned_at     = NOW
   • ticket.assigned_agent_id     = new agent
   • ticket.work_started_at       = None  (new agent must click Start Working)
   • ticket.status                = ASSIGNED
   • ticket.resolution_due_at     = NOW + escalated_resolution_mins (if given)
6. New agent clicks "Start Working" → work_started_at set again, fresh timer.
7. Old agent's comment rights are blocked in comment_service because
   ticket.assigned_agent_id ≠ old_agent_id and escalation.old_agent_id == them.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.clients.postgres_client import Base


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (
        # Hard constraint: a ticket can only be escalated once
        UniqueConstraint("ticket_id", name="uq_escalation_ticket"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Ticket that was escalated (intra-service FK — safe)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Agent who was originally assigned and failed to resolve in time
    # Cross-service ref → auth service users.id  (plain int, no FK declaration)
    old_agent_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Agent the ticket is reassigned to by the team lead (null until reassigned)
    new_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Why escalated — "sla_breach" (auto) or "manual" (team lead forced it)
    reason: Mapped[str] = mapped_column(
        String(50), nullable=False, default="sla_breach"
    )

    # Optional human-readable notes from the team lead
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "system" for automatic Celery escalation, or user_id string for manual
    escalated_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="system"
    )

    # Timestamps
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    reassigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Custom resolution window (minutes) given to the new agent by the team lead.
    # If NULL the team lead didn't override — frontend should show "no new deadline".
    escalated_resolution_mins: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ORM relationship back to Ticket (uselist=False on Ticket side makes this 1-to-1)
    ticket: Mapped["Ticket"] = relationship(  # noqa: F821
        "Ticket", back_populates="escalation"
    )

    def __repr__(self) -> str:
        return (
            f"<Escalation id={self.id} ticket_id={self.ticket_id} "
            f"old={self.old_agent_id} new={self.new_agent_id}>"
        )