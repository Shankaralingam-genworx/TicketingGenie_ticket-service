"""Issue service — admin CRUD for issue types."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.base_exception import NotFoundException
from src.data.repositories.issue_repository import IssueRepository
from src.schemas.issue_schema import IssueCreateRequest, IssueResponse, IssueUpdateRequest


class IssueService:
    def __init__(self, db: AsyncSession):
        self.repo = IssueRepository(db)

    async def create_issue(self, data: IssueCreateRequest) -> IssueResponse:
        issue = await self.repo.create(**data.model_dump())
        return IssueResponse.model_validate(issue)

    async def list_issues(self, active_only: bool = False) -> list[IssueResponse]:
        issues = await self.repo.get_all(active_only=active_only)
        return [IssueResponse.model_validate(i) for i in issues]

    async def get_issue(self, issue_id: int) -> IssueResponse:
        issue = await self.repo.get_by_id(issue_id)
        if not issue:
            raise NotFoundException("Issue", issue_id)
        return IssueResponse.model_validate(issue)

    async def update_issue(self, issue_id: int, data: IssueUpdateRequest) -> IssueResponse:
        issue = await self.repo.get_by_id(issue_id)
        if not issue:
            raise NotFoundException("Issue", issue_id)
        updates = data.model_dump(exclude_none=True)
        issue = await self.repo.update(issue, **updates)
        return IssueResponse.model_validate(issue)

    async def delete_issue(self, issue_id: int) -> None:
        issue = await self.repo.get_by_id(issue_id)
        if not issue:
            raise NotFoundException("Issue", issue_id)
        await self.repo.delete(issue)
