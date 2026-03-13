"""Issue repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.issue_model import Issue


class IssueRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **kwargs) -> Issue:
        issue = Issue(**kwargs)
        self.db.add(issue)
        await self.db.flush()
        await self.db.refresh(issue)
        return issue

    async def get_by_id(self, issue_id: int) -> Issue | None:
        result = await self.db.execute(select(Issue).where(Issue.id == issue_id))
        return result.scalar_one_or_none()

    async def get_all(self, active_only: bool = False) -> list[Issue]:
        q = select(Issue)
        if active_only:
            q = q.where(Issue.is_active.is_(True))
        result = await self.db.execute(q.order_by(Issue.name))
        return list(result.scalars().all())

    async def update(self, issue: Issue, **kwargs) -> Issue:
        for k, v in kwargs.items():
            setattr(issue, k, v)
        await self.db.flush()
        await self.db.refresh(issue)
        return issue

    async def delete(self, issue: Issue) -> None:
        await self.db.delete(issue)
        await self.db.flush()
