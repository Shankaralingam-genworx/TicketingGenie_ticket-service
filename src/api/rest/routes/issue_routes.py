"""Issue routes — Admin: full CRUD | Others: read only (for ticket form search)."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import get_current_user, require_role
from src.core.services.issue_service import IssueService
from src.data.clients.postgres_client import get_db
from src.schemas.issue_schema import IssueCreateRequest, IssueResponse, IssueUpdateRequest


router = APIRouter(prefix="/issues", tags=["Issues"])

@router.post("/", response_model=IssueResponse, status_code=status.HTTP_201_CREATED)
async def create_issue(
    data: IssueCreateRequest,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueService(db)
    return await service.create_issue(data)


@router.get("/", response_model=list[IssueResponse])
async def list_issues(
    active_only: bool = True,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """All authenticated users can list issues (for the ticket creation form search)."""
    service = IssueService(db)
    return await service.list_issues(active_only=active_only)


@router.get("/{issue_id}", response_model=IssueResponse)
async def get_issue(
    issue_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = IssueService(db)
    return await service.get_issue(issue_id)


@router.put("/{issue_id}", response_model=IssueResponse)
async def update_issue(
    issue_id: int,
    data: IssueUpdateRequest,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueService(db)
    return await service.update_issue(issue_id, data)


@router.delete("/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_issue(
    issue_id: int,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = IssueService(db)
    await service.delete_issue(issue_id)
