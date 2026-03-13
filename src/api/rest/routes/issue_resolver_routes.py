"""IssueResolver routes — Admin: full CRUD | Team Lead: read only."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import require_role
from src.core.services.issue_resolver_service import IssueResolverService
from src.data.clients.postgres_client import get_db
from src.schemas.issue_resolver_schema import IssueResolverCreateRequest, IssueResolverResponse

router = APIRouter(prefix="/issue-resolvers", tags=["Issue Resolvers"])


@router.post("/", response_model=IssueResolverResponse, status_code=status.HTTP_201_CREATED)
async def create_resolver(
    data: IssueResolverCreateRequest,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueResolverService(db)
    return await service.create_resolver(data)


@router.get("/", response_model=list[IssueResolverResponse])
async def list_resolvers(
    current_user: dict = Depends(require_role("admin", "team_lead")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueResolverService(db)
    return await service.list_resolvers()


@router.get("/by-issue/{issue_id}", response_model=list[IssueResolverResponse])
async def get_resolvers_by_issue(
    issue_id: int,
    current_user: dict = Depends(require_role("admin", "team_lead")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueResolverService(db)
    return await service.get_resolvers_by_issue(issue_id)


@router.get("/{resolver_id}", response_model=IssueResolverResponse)
async def get_resolver(
    resolver_id: int,
    current_user: dict = Depends(require_role("admin", "team_lead")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueResolverService(db)
    return await service.get_resolver(resolver_id)


@router.delete("/{resolver_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resolver(
    resolver_id: int,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueResolverService(db)
    await service.delete_resolver(resolver_id)
