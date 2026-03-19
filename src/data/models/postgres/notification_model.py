from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.data.clients.postgres_client import Base


class NotificationType(str, Enum):
    TICKET_CREATED   = "ticket_created"
    TICKET_ASSIGNED  = "ticket_assigned"
    STATUS_CHANGED   = "status_changed"
    COMMENT_RECEIVED = "comment_received"
    SLA_BREACHED     = "sla_breached"


class NotificationActor(str, Enum):
    CUSTOMER      = "customer"
    SUPPORT_AGENT = "support_agent"
    TEAM_LEAD     = "team_lead"
    SYSTEM        = "system"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    
    recipient_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    recipient_role: Mapped[str] = mapped_column(
        SAEnum(NotificationActor, name="notificationactor"), nullable=False
    )

    
    ticket_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    ticket_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    type: Mapped[str] = mapped_column(
        SAEnum(NotificationType, name="notificationtype"), nullable=False, index=True
    )
    title:   Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    
    ticket: Mapped["Ticket"] = relationship(  
        "Ticket", back_populates="notifications", lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} type={self.type} "
            f"recipient={self.recipient_id} read={self.is_read}>"
        )