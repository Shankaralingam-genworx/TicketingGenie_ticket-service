from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import require_role
from src.core.services.severity_keyword_service import SeverityKeywordService
from src.data.clients.postgres_client import get_db
from src.schemas.severity_keyword_schema import (
    SeverityKeywordBulkCreate,
    SeverityKeywordBulkResponse,
    SeverityKeywordCreate,
    SeverityKeywordResponse,
    SeverityKeywordUpdate,
)

router = APIRouter(prefix="/admin/severity-keywords", tags=["Severity Keywords"])


@router.get("/", response_model=list[SeverityKeywordResponse])
async def list_keywords(
    active_only: bool          = Query(False, description="Return only active keywords"),
    _:           dict          = Depends(require_role("admin")),
    db:          AsyncSession  = Depends(get_db),
):
    """List all severity keywords. Pass ?active_only=true to filter inactive ones."""
    return await SeverityKeywordService(db).list_keywords(active_only=active_only)


@router.post("/", response_model=SeverityKeywordResponse, status_code=201)
async def create_keyword(
    data: SeverityKeywordCreate,
    _:    dict         = Depends(require_role("admin")),
    db:   AsyncSession = Depends(get_db),
):
    """Create a single severity keyword."""
    async with db.begin():
        return await SeverityKeywordService(db).create_keyword(data)


@router.post("/bulk", response_model=SeverityKeywordBulkResponse, status_code=201)
async def bulk_create_keywords(
    data: SeverityKeywordBulkCreate,
    _:    dict         = Depends(require_role("admin")),
    db:   AsyncSession = Depends(get_db),
):
    """Bulk-create severity keywords. Duplicate keywords are silently skipped."""
    async with db.begin():
        return await SeverityKeywordService(db).bulk_create_keywords(data)


@router.get("/{kw_id}", response_model=SeverityKeywordResponse)
async def get_keyword(
    kw_id: int,
    _:     dict         = Depends(require_role("admin")),
    db:    AsyncSession = Depends(get_db),
):
    return await SeverityKeywordService(db).get_keyword(kw_id)


@router.patch("/{kw_id}", response_model=SeverityKeywordResponse)
async def update_keyword(
    kw_id: int,
    data:  SeverityKeywordUpdate,
    _:     dict         = Depends(require_role("admin")),
    db:    AsyncSession = Depends(get_db),
):
    """
    Update a keyword's text, severity bucket, weight, or active flag.
    All fields are optional — only provided fields are changed.
    """
    async with db.begin():
        return await SeverityKeywordService(db).update_keyword(kw_id, data)


@router.delete("/{kw_id}", status_code=204)
async def delete_keyword(
    kw_id: int,
    _:     dict         = Depends(require_role("admin")),
    db:    AsyncSession = Depends(get_db),
):
    async with db.begin():
        await SeverityKeywordService(db).delete_keyword(kw_id)
