from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import require_role
from src.core.services.email_config_service import EmailConfigService
from src.data.clients.postgres_client import get_db
from src.schemas.email_config_schema import EmailConfigCreate, EmailConfigResponse, EmailConfigUpdate


router = APIRouter(prefix="/admin/email-config", tags=["Email Config"])

@router.post("/", response_model=EmailConfigResponse, status_code=status.HTTP_201_CREATED)
async def create(
    data:         EmailConfigCreate,
    _:            dict = Depends(require_role("admin")),
    db:           AsyncSession   = Depends(get_db),
):
    """Create a config — previous active config is auto-deactivated."""
    async with db.begin():
        return await EmailConfigService(db).create(data)


@router.get("/", response_model=list[EmailConfigResponse])
async def list_all(
    _: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await EmailConfigService(db).list_all()


@router.get("/active", response_model=EmailConfigResponse)
async def get_active(
    _:  dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Also called internally by Celery tasks (use INTERNAL_API_KEY there, not JWT)."""
    return await EmailConfigService(db).get_active()


@router.patch("/{config_id}", response_model=EmailConfigResponse)
async def update(
    config_id: int,
    data:      EmailConfigUpdate,
    _:         dict = Depends(require_role("admin")),
    db:        AsyncSession = Depends(get_db),
):
    async with db.begin():
        return await EmailConfigService(db).update(config_id, data)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    config_id: int,
    _:         dict = Depends(require_role("admin")),
    db:        AsyncSession = Depends(get_db),
):
    async with db.begin():
        await EmailConfigService(db).delete(config_id)