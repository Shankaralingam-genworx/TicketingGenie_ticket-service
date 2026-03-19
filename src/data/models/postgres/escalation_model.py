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
        UniqueConstraint("ticket_id", name="uq_escalation_ticket"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False, index=True,)
    
    old_agent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    new_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(
        String(50), nullable=False, default="sla_breach")   
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalated_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="system"
    )

  
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    reassigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    escalated_resolution_mins: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ticket: Mapped["Ticket"] = relationship(  # noqa: F821
        "Ticket", back_populates="escalation"
    )

    def __repr__(self) -> str:
        return (
            f"<Escalation id={self.id} ticket_id={self.ticket_id} "
            f"old={self.old_agent_id} new={self.new_agent_id}>"
        )