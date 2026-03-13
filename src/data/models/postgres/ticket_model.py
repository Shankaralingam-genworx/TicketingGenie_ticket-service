"""Ticket ORM model.
File: src/data/models/postgres/ticket_model.py

New columns for escalation SLA tracking:
  escalated_response_due_at   — set when team lead reassigns escalated ticket.
                                 New agent must click Start Working before this.
  escalated_resolution_due_at — set when new agent clicks Start Working after
                                 escalation. New agent must resolve before this.

These are separate from response_due_at / resolution_due_at so the original
SLA audit trail is never overwritten. The Celery monitor checks BOTH pairs:
  Normal:     response_due_at / resolution_due_at    (is_escalated=False)
  Escalated:  escalated_response_due_at / escalated_resolution_due_at (is_escalated=True)
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants.priority_constants import Priority
from src.constants.sla_constants import CustomerTier, Severity
from src.constants.ticket_constants import TicketSource, TicketStatus
from src.data.clients.postgres_client import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id:            Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    title:         Mapped[str] = mapped_column(String(255), nullable=False)
    description:   Mapped[str] = mapped_column(Text, nullable=False)

    customer_id:    Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    customer_email: Mapped[str] = mapped_column(String, nullable=False)
    customer_tier:  Mapped[CustomerTier] = mapped_column(
        Enum(CustomerTier, name="customertier"), nullable=False
    )

    issue_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("issues.id", ondelete="SET NULL"), nullable=True
    )

    customer_priority:               Mapped[Priority] = mapped_column(Enum(Priority, name="priority"), nullable=False)
    priority:                        Mapped[Priority] = mapped_column(Enum(Priority, name="priority_eff"), nullable=False)
    priority_overridden:             Mapped[bool]     = mapped_column(Boolean, default=False, nullable=False)
    priority_override_justification: Mapped[str | None] = mapped_column(Text, nullable=True)

    severity: Mapped[Severity] = mapped_column(Enum(Severity, name="severity"), nullable=False)

    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticketstatus"),
        default=TicketStatus.NEW, nullable=False, index=True,
    )
    source: Mapped[TicketSource] = mapped_column(
        Enum(TicketSource, name="ticketsource"),
        default=TicketSource.PORTAL, nullable=False,
    )

    team_id:           Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assigned_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    sla_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True
    )

    # ── Normal SLA deadlines ──────────────────────────────────────────────────
    # Set at ticket creation and start_working respectively.
    # Never overwritten after escalation — preserved for audit.
    response_due_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Escalation SLA deadlines ──────────────────────────────────────────────
    # escalated_response_due_at   : set when lead reassigns → new agent must accept by this time
    # escalated_resolution_due_at : set when new agent clicks Start Working after escalation
    escalated_response_due_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Key timestamps ────────────────────────────────────────────────────────
    work_started_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at:         Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Escalation flags ──────────────────────────────────────────────────────
    is_escalated: Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    issue:    Mapped["Issue"]              = relationship("Issue", back_populates="tickets", lazy="selectin")  # noqa: F821
    sla:      Mapped["SLA"]               = relationship("SLA",   back_populates="tickets", lazy="selectin")  # noqa: F821
    comments: Mapped[list["Comment"]]     = relationship("Comment",      back_populates="ticket", lazy="select",  cascade="all, delete-orphan")  # noqa: F821
    audit_logs: Mapped[list["TicketAudit"]] = relationship("TicketAudit", back_populates="ticket", lazy="select",  cascade="all, delete-orphan")  # noqa: F821
    notifications: Mapped[list["Notification"]] = relationship("Notification", back_populates="ticket", lazy="noload", cascade="all, delete-orphan")  # noqa: F821
    escalation: Mapped["Escalation | None"] = relationship("Escalation", back_populates="ticket", lazy="selectin", uselist=False, cascade="all, delete-orphan")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Ticket {self.ticket_number} status={self.status}>"