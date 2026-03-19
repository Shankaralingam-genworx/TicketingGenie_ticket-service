from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from src.data.clients.postgres_client import Base

class EmailConfig(Base):
    __tablename__ = "email_configs"

    id:   Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False,
                                      doc="Label e.g. 'Production Inbox'")

    # Email address is stored alongside the password so the admin can configure
    # the inbox without touching the server environment variables.
    email:    Mapped[str] = mapped_column(String(255), nullable=False,
                                          doc="Support inbox address e.g. support@company.com")
    password: Mapped[str] = mapped_column(String(255), nullable=False,
                                          doc="Gmail / Outlook app password")

    smtp_from_name:     Mapped[str] = mapped_column(String(120), nullable=False,
                                                    default="Support Team")
    imap_folder:        Mapped[str] = mapped_column(String(100), nullable=False,
                                                    default="INBOX")
    poll_interval_secs: Mapped[int] = mapped_column(Integer, nullable=False,
                                                    default=60)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )