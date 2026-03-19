"""Service for admin-managed severity keywords.

Also contains the one-time seed function that migrates the original
hardcoded keyword dicts into the DB on first startup.
"""

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import Severity
from src.core.exceptions.base_exception import NotFoundException
from src.data.repositories.severity_keyword_repository import SeverityKeywordRepository
from src.schemas.severity_keyword_schema import (
    SeverityKeywordBulkCreate,
    SeverityKeywordBulkResponse,
    SeverityKeywordCreate,
    SeverityKeywordResponse,
    SeverityKeywordUpdate,
)

logger = logging.getLogger("ticket.severity_keyword_service")

# ── Default keyword seed data (migrated from the original hardcoded dicts) ────
# Format: (keyword, severity, weight)


class SeverityKeywordService:
    def __init__(self, db: AsyncSession):
        self.repo = SeverityKeywordRepository(db)

    async def list_keywords(
        self, active_only: bool = False
    ) -> list[SeverityKeywordResponse]:
        rows = await self.repo.get_all(active_only=active_only)
        return [SeverityKeywordResponse.model_validate(r) for r in rows]

    async def get_keyword(self, kw_id: int) -> SeverityKeywordResponse:
        kw = await self.repo.get_by_id(kw_id)
        if not kw:
            raise NotFoundException("SeverityKeyword", kw_id)
        return SeverityKeywordResponse.model_validate(kw)

    async def create_keyword(
        self, data: SeverityKeywordCreate
    ) -> SeverityKeywordResponse:
        existing = await self.repo.get_by_keyword(data.keyword)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Keyword '{data.keyword}' already exists (id={existing.id}).",
            )
        kw = await self.repo.create(data.keyword, data.severity, data.weight)
        return SeverityKeywordResponse.model_validate(kw)

    async def bulk_create_keywords(
        self, data: SeverityKeywordBulkCreate
    ) -> SeverityKeywordBulkResponse:
        entries = [
            {"keyword": k.keyword, "severity": k.severity, "weight": k.weight}
            for k in data.keywords
        ]
        inserted = await self.repo.bulk_create(entries)
        skipped = len(data.keywords) - inserted
        return SeverityKeywordBulkResponse(
            inserted=inserted,
            message=f"Inserted {inserted} keyword(s). Skipped {skipped} duplicate(s).",
        )

    async def update_keyword(
        self, kw_id: int, data: SeverityKeywordUpdate
    ) -> SeverityKeywordResponse:
        kw = await self.repo.get_by_id(kw_id)
        if not kw:
            raise NotFoundException("SeverityKeyword", kw_id)
        # Duplicate keyword check if keyword text is changing
        if data.keyword and data.keyword != kw.keyword:
            existing = await self.repo.get_by_keyword(data.keyword)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Keyword '{data.keyword}' already exists (id={existing.id}).",
                )
        updated = await self.repo.update(
            kw,
            keyword=data.keyword,
            severity=data.severity,
            weight=data.weight,
            is_active=data.is_active,
        )
        return SeverityKeywordResponse.model_validate(updated)

    async def delete_keyword(self, kw_id: int) -> None:
        kw = await self.repo.get_by_id(kw_id)
        if not kw:
            raise NotFoundException("SeverityKeyword", kw_id)
        await self.repo.delete(kw)
