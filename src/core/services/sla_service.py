"""SLA service — admin CRUD for SLA policies."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions.base_exception import ConflictException, NotFoundException
from src.data.repositories.sla_repository import SLARepository
from src.schemas.sla_schema import SLACreateRequest, SLAResponse, SLAUpdateRequest


class SLAService:
    def __init__(self, db: AsyncSession):
        self.repo = SLARepository(db)

    async def create_sla(self, data: SLACreateRequest) -> SLAResponse:
        existing = await self.repo.get_by_tier_and_severity(data.customer_tier, data.severity)
        if existing:
            raise ConflictException(
                f"SLA already exists for tier={data.customer_tier} severity={data.severity}."
            )
        sla = await self.repo.create(**data.model_dump())
        return SLAResponse.model_validate(sla)

    async def list_sla(self) -> list[SLAResponse]:
        slas = await self.repo.get_all()
        return [SLAResponse.model_validate(s) for s in slas]

    async def get_sla(self, sla_id: int) -> SLAResponse:
        sla = await self.repo.get_by_id(sla_id)
        if not sla:
            raise NotFoundException("SLA", sla_id)
        return SLAResponse.model_validate(sla)

    async def update_sla(self, sla_id: int, data: SLAUpdateRequest) -> SLAResponse:
        sla = await self.repo.get_by_id(sla_id)
        if not sla:
            raise NotFoundException("SLA", sla_id)
        sla = await self.repo.update(sla, **data.model_dump(exclude_none=True))
        return SLAResponse.model_validate(sla)

    async def delete_sla(self, sla_id: int) -> None:
        sla = await self.repo.get_by_id(sla_id)
        if not sla:
            raise NotFoundException("SLA", sla_id)
        await self.repo.delete(sla)
