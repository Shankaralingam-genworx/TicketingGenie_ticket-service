"""IssueResolver service — maps issues to responsible teams."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.base_exception import NotFoundException
from src.data.repositories.issue_repository import IssueRepository
from src.data.repositories.issue_resolver_repository import IssueResolverRepository
from src.schemas.issue_resolver_schema import (
    IssueResolverCreateRequest,
    IssueResolverResponse,
)
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class IssueResolverService:
    def __init__(self, db: AsyncSession):
        self.repo = IssueResolverRepository(db)
        self.issue_repo = IssueRepository(db)

    async def create_resolver(self, data: IssueResolverCreateRequest) -> IssueResolverResponse:
        logger.info(
            "create_resolver_started",
            issue_id=data.issue_id,
        )

        issue = await self.issue_repo.get_by_id(data.issue_id)
        if not issue:
            logger.warning(
                "create_resolver_issue_not_found",
                issue_id=data.issue_id,
            )
            raise NotFoundException("Issue", data.issue_id)

        resolver = await self.repo.create(**data.model_dump())

        logger.info(
            "create_resolver_success",
            resolver_id=resolver.id,
            issue_id=data.issue_id,
        )

        return IssueResolverResponse.model_validate(resolver)

    async def list_resolvers(self) -> list[IssueResolverResponse]:
        logger.info("list_resolvers_started")

        resolvers = await self.repo.get_all()

        logger.info(
            "list_resolvers_success",
            count=len(resolvers),
        )

        return [IssueResolverResponse.model_validate(r) for r in resolvers]

    async def get_resolver(self, resolver_id: int) -> IssueResolverResponse:
        logger.info(
            "get_resolver_started",
            resolver_id=resolver_id,
        )

        resolver = await self.repo.get_by_id(resolver_id)
        if not resolver:
            logger.warning(
                "get_resolver_not_found",
                resolver_id=resolver_id,
            )
            raise NotFoundException("IssueResolver", resolver_id)

        logger.info(
            "get_resolver_success",
            resolver_id=resolver_id,
        )

        return IssueResolverResponse.model_validate(resolver)

    async def get_resolvers_by_issue(self, issue_id: int) -> list[IssueResolverResponse]:
        logger.info(
            "get_resolvers_by_issue_started",
            issue_id=issue_id,
        )

        resolvers = await self.repo.get_by_issue(issue_id)

        logger.info(
            "get_resolvers_by_issue_success",
            issue_id=issue_id,
            count=len(resolvers),
        )

        return [IssueResolverResponse.model_validate(r) for r in resolvers]

    async def delete_resolver(self, resolver_id: int) -> None:
        logger.info(
            "delete_resolver_started",
            resolver_id=resolver_id,
        )

        resolver = await self.repo.get_by_id(resolver_id)
        if not resolver:
            logger.warning(
                "delete_resolver_not_found",
                resolver_id=resolver_id,
            )
            raise NotFoundException("IssueResolver", resolver_id)

        await self.repo.delete(resolver)

        logger.info(
            "delete_resolver_success",
            resolver_id=resolver_id,
        )