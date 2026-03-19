
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.sla_constants import Severity
from src.data.clients.postgres_client import Base


class SeverityKeyword(Base):
    __tablename__ = "severity_keywords"

    id:       Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The keyword or phrase to match (case-insensitive, stored lowercase)
    keyword:  Mapped[str]   = mapped_column(String(200), nullable=False, unique=True, index=True)

    # Severity bucket this keyword votes for
    severity: Mapped[Severity] = mapped_column(
        SAEnum(Severity, name="severity"),   # reuses existing enum type
        nullable=False,
        index=True,
    )

    # How strongly this keyword contributes to its severity bucket.
    # Matches the original hardcoded weights (0.5 – 3.5 range).
    weight:    Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    is_active: Mapped[bool]  = mapped_column(Boolean, nullable=False, default=True)

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

    def __repr__(self) -> str:
        return f"<SeverityKeyword '{self.keyword}' → {self.severity.value} w={self.weight}>"
