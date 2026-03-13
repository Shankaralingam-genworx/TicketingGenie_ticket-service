"""IssueResolver service — maps issues to responsible teams."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.base_exception import NotFoundException
from src.data.repositories.issue_repository import IssueRepository
from src.data.repositories.issue_resolver_repository import IssueResolverRepository
from src.schemas.issue_resolver_schema import (
    IssueResolverCreateRequest,
    IssueResolverResponse,
)


class IssueResolverService:
    def __init__(self, db: AsyncSession):
        self.repo = IssueResolverRepository(db)
        self.issue_repo = IssueRepository(db)

    async def create_resolver(self, data: IssueResolverCreateRequest) -> IssueResolverResponse:
        issue = await self.issue_repo.get_by_id(data.issue_id)
        if not issue:
            raise NotFoundException("Issue", data.issue_id)
        resolver = await self.repo.create(**data.model_dump())
        return IssueResolverResponse.model_validate(resolver)

    async def list_resolvers(self) -> list[IssueResolverResponse]:
        resolvers = await self.repo.get_all()
        return [IssueResolverResponse.model_validate(r) for r in resolvers]

    async def get_resolver(self, resolver_id: int) -> IssueResolverResponse:
        resolver = await self.repo.get_by_id(resolver_id)
        if not resolver:
            raise NotFoundException("IssueResolver", resolver_id)
        return IssueResolverResponse.model_validate(resolver)

    async def get_resolvers_by_issue(self, issue_id: int) -> list[IssueResolverResponse]:
        resolvers = await self.repo.get_by_issue(issue_id)
        return [IssueResolverResponse.model_validate(r) for r in resolvers]

    async def delete_resolver(self, resolver_id: int) -> None:
        resolver = await self.repo.get_by_id(resolver_id)
        if not resolver:
            raise NotFoundException("IssueResolver", resolver_id)
        await self.repo.delete(resolver)
