"""SLA repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import CustomerTier, Severity
from src.data.models.postgres.sla_model import SLA


class SLARepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **kwargs) -> SLA:
        sla = SLA(**kwargs)
        self.db.add(sla)
        await self.db.flush()
        await self.db.refresh(sla)
        return sla

    async def get_by_id(self, sla_id: int) -> SLA | None:
        result = await self.db.execute(select(SLA).where(SLA.id == sla_id))
        return result.scalar_one_or_none()

    async def get_by_tier_and_severity(
        self, tier: CustomerTier, severity: Severity
    ) -> SLA | None:
        result = await self.db.execute(
            select(SLA).where(
                SLA.customer_tier == tier,
                SLA.severity == severity,
                SLA.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> list[SLA]:
        result = await self.db.execute(
            select(SLA).order_by(SLA.customer_tier, SLA.severity)
        )
        return list(result.scalars().all())

    async def update(self, sla: SLA, **kwargs) -> SLA:
        for k, v in kwargs.items():
            setattr(sla, k, v)
        await self.db.flush()
        await self.db.refresh(sla)
        return sla

    async def delete(self, sla: SLA) -> None:
        await self.db.delete(sla)
        await self.db.flush()
