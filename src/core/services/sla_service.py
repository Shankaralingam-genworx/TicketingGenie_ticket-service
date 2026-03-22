"""Admin CRUD for SLA policies (tier × severity matrix)."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.base_exception import ConflictException, NotFoundException
from src.data.repositories.sla_repository import SLARepository
from src.schemas.sla_schema import SLACreateRequest, SLAResponse, SLAUpdateRequest
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class SLAService:

    def __init__(self, db: AsyncSession):
        self.repo = SLARepository(db)

    async def create_sla(self, data: SLACreateRequest) -> SLAResponse:
        logger.info("create_sla_started", tier=data.customer_tier, severity=data.severity)

        existing = await self.repo.get_by_tier_and_severity(data.customer_tier, data.severity)
        if existing:
            logger.warning(
                "create_sla_conflict",
                tier=data.customer_tier,
                severity=data.severity,
            )
            raise ConflictException(
                f"SLA already exists for tier={data.customer_tier} severity={data.severity}."
            )

        sla = await self.repo.create(**data.model_dump())
        logger.info("create_sla_success", sla_id=sla.id)
        return SLAResponse.model_validate(sla)

    async def list_sla(self) -> list[SLAResponse]:
        logger.info("list_sla_started")
        slas = await self.repo.get_all()
        logger.info("list_sla_success", count=len(slas))
        return [SLAResponse.model_validate(s) for s in slas]

    async def get_sla(self, sla_id: int) -> SLAResponse:
        sla = await self.repo.get_by_id(sla_id)
        if not sla:
            logger.warning("get_sla_not_found", sla_id=sla_id)
            raise NotFoundException("SLA", sla_id)
        return SLAResponse.model_validate(sla)

    async def update_sla(self, sla_id: int, data: SLAUpdateRequest) -> SLAResponse:
        logger.info("update_sla_started", sla_id=sla_id)

        sla = await self.repo.get_by_id(sla_id)
        if not sla:
            logger.warning("update_sla_not_found", sla_id=sla_id)
            raise NotFoundException("SLA", sla_id)

        sla = await self.repo.update(sla, **data.model_dump(exclude_none=True))
        logger.info("update_sla_success", sla_id=sla_id)
        return SLAResponse.model_validate(sla)

    async def delete_sla(self, sla_id: int) -> None:
        logger.info("delete_sla_started", sla_id=sla_id)

        sla = await self.repo.get_by_id(sla_id)
        if not sla:
            logger.warning("delete_sla_not_found", sla_id=sla_id)
            raise NotFoundException("SLA", sla_id)

        await self.repo.delete(sla)
        logger.info("delete_sla_success", sla_id=sla_id)