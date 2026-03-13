from src.core.exceptions.base_exception import NotFoundException
from sqlalchemy.ext.asyncio import AsyncSession
from src.schemas.email_config_schema import EmailConfigCreate,EmailConfigResponse,EmailConfigUpdate
from src.data.repositories.email_config_repository import EmailConfigRepository

class EmailConfigService:
    def __init__(self, db: AsyncSession):
        self.repo = EmailConfigRepository(db)

    async def create(self, data: EmailConfigCreate) -> EmailConfigResponse:
        return EmailConfigResponse.model_validate(await self.repo.create(data))

    async def get_active(self) -> EmailConfigResponse:
        config = await self.repo.get_active()
        if not config:
            raise NotFoundException("EmailConfig", "active")
        return EmailConfigResponse.model_validate(config)

    async def list_all(self) -> list[EmailConfigResponse]:
        return [EmailConfigResponse.model_validate(c) for c in await self.repo.get_all()]

    async def update(self, config_id: int, data: EmailConfigUpdate) -> EmailConfigResponse:
        config = await self.repo.get_by_id(config_id)
        if not config:
            raise NotFoundException("EmailConfig", config_id)
        return EmailConfigResponse.model_validate(await self.repo.update(config, data))

    async def delete(self, config_id: int) -> None:
        config = await self.repo.get_by_id(config_id)
        if not config:
            raise NotFoundException("EmailConfig", config_id)
        await self.repo.delete(config)