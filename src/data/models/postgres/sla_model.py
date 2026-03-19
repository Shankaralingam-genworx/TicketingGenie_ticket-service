from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants.sla_constants import Severity
from src.data.clients.postgres_client import Base

class SLA(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        # Unique on the tier name string + severity — one policy per (tier, severity) pair.
        UniqueConstraint("customer_tier", "severity", name="uq_sla_tier_severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    customer_tier: Mapped[str] = mapped_column(String(100), nullable=False)

    customer_tier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), nullable=False
    )

    response_time_mins:   Mapped[float] = mapped_column(Float, nullable=False)
    resolution_time_mins: Mapped[float] = mapped_column(Float, nullable=False)

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