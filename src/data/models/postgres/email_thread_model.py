from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.data.clients.postgres_client import Base


class EmailThreadStatus(str, Enum):
    ACTIVE   = "active"    
    CLOSED   = "closed"    
    REJECTED = "rejected"  


class EmailThread(Base):
 
    __tablename__ = "email_threads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    ticket_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tickets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    customer_email: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(
        String(500), nullable=False, unique=True, index=True
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[EmailThreadStatus] = mapped_column(
        SAEnum(EmailThreadStatus, name="emailthreadstatus"),
        nullable=False,
        default=EmailThreadStatus.ACTIVE,
        index=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_email_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship — lazy="noload" keeps it non-blocking in async context
    ticket = relationship("Ticket", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<EmailThread id={self.id} ticket_id={self.ticket_id} "
            f"status={self.status} from={self.customer_email!r}>"
        )