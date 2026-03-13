"""Comment repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.comment_model import Comment


class CommentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **kwargs) -> Comment:
        comment = Comment(**kwargs)
        self.db.add(comment)
        await self.db.flush()
        await self.db.refresh(comment)
        return comment

    async def get_by_ticket(self, ticket_id: int) -> list[Comment]:
        result = await self.db.execute(
            select(Comment)
            .where(Comment.ticket_id == ticket_id)
            .order_by(Comment.created_at)
        )
        return list(result.scalars().all())