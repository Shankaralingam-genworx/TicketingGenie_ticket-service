from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.data.models.postgres.email_config_model import EmailConfig
from src.schemas.email_config_schema import EmailConfigCreate,EmailConfigUpdate

class EmailConfigRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: EmailConfigCreate) -> EmailConfig:
        await self._deactivate_all()
        config = EmailConfig(**data.model_dump())
        self.db.add(config)
        await self.db.flush()
        await self.db.refresh(config)
        return config

    async def get_active(self) -> EmailConfig | None:
        result = await self.db.execute(
            select(EmailConfig).where(EmailConfig.is_active == True)  # noqa: E712
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, config_id: int) -> EmailConfig | None:
        result = await self.db.execute(
            select(EmailConfig).where(EmailConfig.id == config_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> list[EmailConfig]:
        result = await self.db.execute(
            select(EmailConfig).order_by(EmailConfig.created_at.desc())
        )
        return list(result.scalars().all())

    async def update(self, config: EmailConfig, data: EmailConfigUpdate) -> EmailConfig:
        for field, value in data.model_dump(exclude_none=True).items():
            if field == "is_active" and value is True:
                await self._deactivate_all()
            setattr(config, field, value)
        await self.db.flush()
        await self.db.refresh(config)
        return config

    async def delete(self, config: EmailConfig) -> None:
        await self.db.delete(config)
        await self.db.flush()

    async def _deactivate_all(self) -> None:
        await self.db.execute(update(EmailConfig).values(is_active=False))
