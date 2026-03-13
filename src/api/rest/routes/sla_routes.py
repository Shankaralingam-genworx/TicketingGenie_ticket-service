"""SLA routes — Admin: full CRUD | Team Lead: read only."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import require_role
from src.core.services.sla_service import SLAService
from src.data.clients.postgres_client import get_db
from src.schemas.sla_schema import SLACreateRequest, SLAResponse, SLAUpdateRequest

router = APIRouter(prefix="/sla", tags=["SLA"])


@router.post("/", response_model=SLAResponse, status_code=status.HTTP_201_CREATED)
async def create_sla(
    data: SLACreateRequest,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = SLAService(db)
    return await service.create_sla(data)


@router.get("/", response_model=list[SLAResponse])
async def list_sla(
    current_user: dict = Depends(require_role("admin", "team_lead")),
    db: AsyncSession = Depends(get_db),
):
    service = SLAService(db)
    return await service.list_sla()


@router.get("/{sla_id}", response_model=SLAResponse)
async def get_sla(
    sla_id: int,
    current_user: dict = Depends(require_role("admin", "team_lead")),
    db: AsyncSession = Depends(get_db),
):
    service = SLAService(db)
    return await service.get_sla(sla_id)


@router.put("/{sla_id}", response_model=SLAResponse)
async def update_sla(
    sla_id: int,
    data: SLAUpdateRequest,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = SLAService(db)
    return await service.update_sla(sla_id, data)


@router.delete("/{sla_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sla(
    sla_id: int,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    service = SLAService(db)
    await service.delete_sla(sla_id)
