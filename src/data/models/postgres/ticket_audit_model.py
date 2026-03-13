"""TicketAudit ORM model — immutable audit trail for every ticket event.

Cross-service ref: actor_id → users.id (auth service, same DB).
Stored as plain Integer — no ForeignKey() declaration to avoid
NoReferencedTableError on create_all (users is not in this Base).
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants.sla_constants import AuditAction
from src.data.clients.postgres_client import Base


class TicketAudit(Base):
    __tablename__ = "ticket_audits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Intra-service FK — safe to declare
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="auditaction"), nullable=False
    )

    # Cross-service ref → auth service: users.id  (plain int, no FK declaration)
    actor_id: Mapped[int] = mapped_column(Integer, nullable=False)

    actor_role: Mapped[str] = mapped_column(String(30), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # Intra-service relationship
    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="audit_logs")  # noqa: F821