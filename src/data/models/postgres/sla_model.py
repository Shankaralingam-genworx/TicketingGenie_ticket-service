"""SLA ORM model — response/resolution time matrix keyed by tier + severity.
File: src/data/models/postgres/sla_model.py

New columns:
  additional_response_mins    — extra response window given to the NEW agent
                                after an escalation reassignment. The clock
                                starts when the team lead calls reassign.
  additional_resolution_mins  — extra resolution window given to the NEW agent.
                                The clock starts when the new agent clicks
                                "Start Working" on the escalated ticket.

Both are configured by admin in the SLA policy table — they apply automatically
to any ticket that uses this SLA policy when it gets escalated.
The team lead can still override them per-reassignment via
EscalationReassignRequest.escalated_response_mins /
EscalationReassignRequest.escalated_resolution_mins if needed.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants.sla_constants import CustomerTier, Severity
from src.data.clients.postgres_client import Base


class SLA(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        UniqueConstraint("customer_tier", "severity", name="uq_sla_tier_severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    customer_tier: Mapped[CustomerTier] = mapped_column(
        Enum(CustomerTier, name="customertier"), nullable=False
    )
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), nullable=False
    )

    # ── Normal SLA windows ────────────────────────────────────────────────────
    # response_time_mins   : from ticket creation → agent must click Start Working
    # resolution_time_mins : from Start Working   → agent must resolve
    response_time_mins:   Mapped[float] = mapped_column(Float, nullable=False)
    resolution_time_mins: Mapped[float] = mapped_column(Float, nullable=False)

    # ── Escalation SLA windows (admin-configured) ────────────────────────────
    # additional_response_mins   : from escalation reassignment → new agent must
    #                              click Start Working
    # additional_resolution_mins : from new agent's Start Working → must resolve
    # Both default to 0 which means "use same as normal SLA" if not configured.
    additional_response_mins:   Mapped[float] = mapped_column(Float, nullable=False, default=0)
    additional_resolution_mins: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    tickets: Mapped[list["Ticket"]] = relationship(  # noqa: F821
        "Ticket", back_populates="sla", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<SLA tier={self.customer_tier} severity={self.severity}>"