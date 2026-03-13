"""IssueResolver ORM model — maps an issue type to the responsible team.

Cross-service ref: team_id → teams.id (auth service, same DB).
Stored as plain Integer — no ForeignKey() declaration to avoid
NoReferencedTableError on create_all (teams is not in this Base).
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.clients.postgres_client import Base


class IssueResolver(Base):
    __tablename__ = "issue_resolvers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Intra-service FK — safe to declare
    issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )

    # Cross-service ref → auth service: teams.id  (plain int, no FK declaration)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)

    team_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # Intra-service relationship
    issue: Mapped["Issue"] = relationship(  # noqa: F821
        "Issue", back_populates="resolvers", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<IssueResolver issue={self.issue_id} team={self.team_id}>"