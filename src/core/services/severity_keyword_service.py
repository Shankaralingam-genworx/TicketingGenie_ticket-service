"""Admin CRUD for severity keywords used in ticket classification."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.base_exception import AppException, NotFoundException
from src.data.repositories.severity_keyword_repository import SeverityKeywordRepository
from src.schemas.severity_keyword_schema import (
    SeverityKeywordBulkCreate,
    SeverityKeywordBulkResponse,
    SeverityKeywordCreate,
    SeverityKeywordResponse,
    SeverityKeywordUpdate,
)
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class SeverityKeywordService:

    def __init__(self, db: AsyncSession):
        self.repo = SeverityKeywordRepository(db)

    async def list_keywords(
        self, active_only: bool = False
    ) -> list[SeverityKeywordResponse]:
        logger.info("list_keywords_started", active_only=active_only)
        rows = await self.repo.get_all(active_only=active_only)
        logger.info("list_keywords_success", count=len(rows))
        return [SeverityKeywordResponse.model_validate(r) for r in rows]

    async def get_keyword(self, kw_id: int) -> SeverityKeywordResponse:
        kw = await self.repo.get_by_id(kw_id)
        if not kw:
            logger.warning("get_keyword_not_found", kw_id=kw_id)
            raise NotFoundException("SeverityKeyword", kw_id)
        return SeverityKeywordResponse.model_validate(kw)

    async def create_keyword(
        self, data: SeverityKeywordCreate
    ) -> SeverityKeywordResponse:
        logger.info("create_keyword_started", keyword=data.keyword, severity=data.severity)

        existing = await self.repo.get_by_keyword(data.keyword)
        if existing:
            logger.warning("create_keyword_conflict", keyword=data.keyword, existing_id=existing.id)
            raise AppException(
                f"Keyword '{data.keyword}' already exists (id={existing.id}).",
                status_code=409,
            )

        kw = await self.repo.create(data.keyword, data.severity, data.weight)
        logger.info("create_keyword_success", kw_id=kw.id, keyword=kw.keyword)
        return SeverityKeywordResponse.model_validate(kw)

    async def bulk_create_keywords(
        self, data: SeverityKeywordBulkCreate
    ) -> SeverityKeywordBulkResponse:
        logger.info("bulk_create_keywords_started", count=len(data.keywords))

        entries = [
            {"keyword": k.keyword, "severity": k.severity, "weight": k.weight}
            for k in data.keywords
        ]
        inserted = await self.repo.bulk_create(entries)
        skipped  = len(data.keywords) - inserted

        logger.info("bulk_create_keywords_success", inserted=inserted, skipped=skipped)
        return SeverityKeywordBulkResponse(
            inserted=inserted,
            message=f"Inserted {inserted} keyword(s). Skipped {skipped} duplicate(s).",
        )

    async def update_keyword(
        self, kw_id: int, data: SeverityKeywordUpdate
    ) -> SeverityKeywordResponse:
        logger.info("update_keyword_started", kw_id=kw_id)

        kw = await self.repo.get_by_id(kw_id)
        if not kw:
            logger.warning("update_keyword_not_found", kw_id=kw_id)
            raise NotFoundException("SeverityKeyword", kw_id)

        # Block rename to an already-existing keyword
        if data.keyword and data.keyword != kw.keyword:
            existing = await self.repo.get_by_keyword(data.keyword)
            if existing:
                logger.warning(
                    "update_keyword_conflict",
                    kw_id=kw_id,
                    new_keyword=data.keyword,
                    existing_id=existing.id,
                )
                raise AppException(
                    f"Keyword '{data.keyword}' already exists (id={existing.id}).",
                    status_code=409,
                )

        updated = await self.repo.update(
            kw,
            keyword   = data.keyword,
            severity  = data.severity,
            weight    = data.weight,
            is_active = data.is_active,
        )
        logger.info("update_keyword_success", kw_id=kw_id)
        return SeverityKeywordResponse.model_validate(updated)

    async def delete_keyword(self, kw_id: int) -> None:
        logger.info("delete_keyword_started", kw_id=kw_id)

        kw = await self.repo.get_by_id(kw_id)
        if not kw:
            logger.warning("delete_keyword_not_found", kw_id=kw_id)
            raise NotFoundException("SeverityKeyword", kw_id)

        await self.repo.delete(kw)
        logger.info("delete_keyword_success", kw_id=kw_id)