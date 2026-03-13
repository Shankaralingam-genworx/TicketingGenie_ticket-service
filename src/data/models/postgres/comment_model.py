"""Comment ORM model.
File: src/data/models/postgres/comment_model.py

Change from previous version:
  attachments — JSON column storing a list of AttachmentMeta-compatible dicts.
  Mirrors the same shape used on the Ticket model so the same frontend
  AttachmentGallery / Lightbox component can render both.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants.sla_constants import CommentSource
from src.data.clients.postgres_client import Base


class Comment(Base):
    __tablename__ = "comments"

    id:          Mapped[int]           = mapped_column(primary_key=True, autoincrement=True)
    ticket_id:   Mapped[int]           = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    author_id:   Mapped[int]           = mapped_column(Integer, nullable=False)
    author_role: Mapped[str]           = mapped_column(String(30), nullable=False)
    content:     Mapped[str]           = mapped_column(Text, nullable=False)
    source:      Mapped[CommentSource] = mapped_column(
        Enum(CommentSource, name="commentsource"),
        default=CommentSource.PORTAL,
        nullable=False,
    )

    # List of AttachmentMeta dicts — same shape as Ticket.attachments.
    # NULL when the comment has no attachments; [] is never stored (use NULL).
    attachments: Mapped[list | None]   = mapped_column(JSON, nullable=True, default=None)

    created_at:  Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="comments")  # noqa: F821