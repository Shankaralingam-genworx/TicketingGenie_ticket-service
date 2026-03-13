"""
Email Config Routes — admin only.
File: src/api/rest/routes/email_config_routes.py
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user
from src.data.clients.postgres_client import get_db
from src.schemas.email_config_schema import EmailConfigCreate, EmailConfigUpdate, EmailConfigResponse
from src.core.services.email_config_service import EmailConfigService

router = APIRouter(prefix="/admin/email-config", tags=["Email Config"])


def _admin_only(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependency — raises 403 for any role that is not admin."""
    if current_user.get("role") not in ("admin", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


@router.post("/", response_model=EmailConfigResponse, status_code=status.HTTP_201_CREATED)
async def create(
    data:         EmailConfigCreate,
    _:            dict           = Depends(_admin_only),
    db:           AsyncSession   = Depends(get_db),
):
    """Create a config — previous active config is auto-deactivated."""
    async with db.begin():
        return await EmailConfigService(db).create(data)


@router.get("/", response_model=list[EmailConfigResponse])
async def list_all(
    _:  dict         = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    return await EmailConfigService(db).list_all()


@router.get("/active", response_model=EmailConfigResponse)
async def get_active(
    _:  dict         = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Also called internally by Celery tasks (use INTERNAL_API_KEY there, not JWT)."""
    return await EmailConfigService(db).get_active()


@router.patch("/{config_id}", response_model=EmailConfigResponse)
async def update(
    config_id: int,
    data:      EmailConfigUpdate,
    _:         dict         = Depends(_admin_only),
    db:        AsyncSession = Depends(get_db),
):
    async with db.begin():
        return await EmailConfigService(db).update(config_id, data)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    config_id: int,
    _:         dict         = Depends(_admin_only),
    db:        AsyncSession = Depends(get_db),
):
    async with db.begin():
        await EmailConfigService(db).delete(config_id)