"""IssueResolver repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.issue_resolver_model import IssueResolver


class IssueResolverRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **kwargs) -> IssueResolver:
        resolver = IssueResolver(**kwargs)
        self.db.add(resolver)
        await self.db.flush()
        await self.db.refresh(resolver)
        return resolver

    async def get_by_id(self, resolver_id: int) -> IssueResolver | None:
        result = await self.db.execute(
            select(IssueResolver).where(IssueResolver.id == resolver_id)
        )
        return result.scalar_one_or_none()

    async def get_by_issue(self, issue_id: int) -> list[IssueResolver]:
        result = await self.db.execute(
            select(IssueResolver).where(
                IssueResolver.issue_id == issue_id,
                IssueResolver.is_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def get_all(self) -> list[IssueResolver]:
        result = await self.db.execute(
            select(IssueResolver).order_by(IssueResolver.id)
        )
        return list(result.scalars().all())

    async def delete(self, resolver: IssueResolver) -> None:
        await self.db.delete(resolver)
        await self.db.flush()
