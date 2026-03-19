from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import require_role
from src.core.services.dashboard_service import DashboardService
from src.data.clients.postgres_client import get_db
from src.schemas.dashboard_schema import DashboardMetrics

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/metrics", response_model=DashboardMetrics)
async def get_metrics(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
  
    service = DashboardService(db)
    return await service.get_metrics()