"""Issue service — admin CRUD for issue types."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.base_exception import NotFoundException
from src.data.repositories.issue_repository import IssueRepository
from src.schemas.issue_schema import IssueCreateRequest, IssueResponse, IssueUpdateRequest
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class IssueService:
    def __init__(self, db: AsyncSession):
        self.repo = IssueRepository(db)

    async def create_issue(self, data: IssueCreateRequest) -> IssueResponse:
        logger.info("create_issue_started")

        issue = await self.repo.create(**data.model_dump())

        logger.info(
            "create_issue_success",
            issue_id=issue.id,
        )

        return IssueResponse.model_validate(issue)

    async def list_issues(self, active_only: bool = False) -> list[IssueResponse]:
        logger.info(
            "list_issues_started",
            active_only=active_only,
        )

        issues = await self.repo.get_all(active_only=active_only)

        logger.info(
            "list_issues_success",
            count=len(issues),
            active_only=active_only,
        )

        return [IssueResponse.model_validate(i) for i in issues]

    async def get_issue(self, issue_id: int) -> IssueResponse:
        logger.info(
            "get_issue_started",
            issue_id=issue_id,
        )

        issue = await self.repo.get_by_id(issue_id)
        if not issue:
            logger.warning(
                "get_issue_not_found",
                issue_id=issue_id,
            )
            raise NotFoundException("Issue", issue_id)

        logger.info(
            "get_issue_success",
            issue_id=issue_id,
        )

        return IssueResponse.model_validate(issue)

    async def update_issue(self, issue_id: int, data: IssueUpdateRequest) -> IssueResponse:
        logger.info(
            "update_issue_started",
            issue_id=issue_id,
        )

        issue = await self.repo.get_by_id(issue_id)
        if not issue:
            logger.warning(
                "update_issue_not_found",
                issue_id=issue_id,
            )
            raise NotFoundException("Issue", issue_id)

        updates = data.model_dump(exclude_none=True)

        issue = await self.repo.update(issue, **updates)

        logger.info(
            "update_issue_success",
            issue_id=issue_id,
            updated_fields=list(updates.keys()),
        )

        return IssueResponse.model_validate(issue)

    async def delete_issue(self, issue_id: int) -> None:
        logger.info(
            "delete_issue_started",
            issue_id=issue_id,
        )

        issue = await self.repo.get_by_id(issue_id)
        if not issue:
            logger.warning(
                "delete_issue_not_found",
                issue_id=issue_id,
            )
            raise NotFoundException("Issue", issue_id)

        await self.repo.delete(issue)

        logger.info(
            "delete_issue_success",
            issue_id=issue_id,
        )