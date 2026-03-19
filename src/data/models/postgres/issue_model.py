"""Issue ORM model — categories of problems a customer can raise a ticket for."""

from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.constants.issue_constants import IssueCategory
from src.data.clients.postgres_client import Base


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    category: Mapped[IssueCategory] = mapped_column(
        Enum(IssueCategory, name="issuecategory"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    # Relationships
    resolvers: Mapped[list["IssueResolver"]] = relationship(  # noqa: F821
        "IssueResolver", back_populates="issue", lazy="selectin"
    )
    tickets: Mapped[list["Ticket"]] = relationship(  # noqa: F821
        "Ticket", back_populates="issue", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Issue id={self.id} name={self.name!r}>"
