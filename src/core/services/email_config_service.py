from src.core.exceptions.base_exception import NotFoundException
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas.email_config_schema import (
    EmailConfigCreate,
    EmailConfigResponse,
    EmailConfigUpdate,
)
from src.data.repositories.email_config_repository import EmailConfigRepository
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class EmailConfigService:
    def __init__(self, db: AsyncSession):
        self.repo = EmailConfigRepository(db)

    async def create(self, data: EmailConfigCreate) -> EmailConfigResponse:
        logger.info("email_config_create_started")

        try:
            config = await self.repo.create(data)

            logger.info(
                "email_config_create_success",
                config_id=config.id,
            )

            return EmailConfigResponse.model_validate(config)

        except Exception as e:
            logger.exception(
                "email_config_create_failed",
                error=str(e),
            )
            raise

    async def get_active(self) -> EmailConfigResponse:
        logger.info("email_config_get_active_started")

        try:
            config = await self.repo.get_active()

            if not config:
                logger.warning("email_config_active_not_found")
                raise NotFoundException("EmailConfig", "active")

            logger.info(
                "email_config_get_active_success",
                config_id=config.id,
            )

            return EmailConfigResponse.model_validate(config)

        except Exception as e:
            logger.exception(
                "email_config_get_active_failed",
                error=str(e),
            )
            raise

    async def list_all(self) -> list[EmailConfigResponse]:
        logger.info("email_config_list_started")

        try:
            configs = await self.repo.get_all()

            logger.info(
                "email_config_list_success",
                count=len(configs),
            )

            return [EmailConfigResponse.model_validate(c) for c in configs]

        except Exception as e:
            logger.exception(
                "email_config_list_failed",
                error=str(e),
            )
            raise

    async def update(self, config_id: int, data: EmailConfigUpdate) -> EmailConfigResponse:
        logger.info(
            "email_config_update_started",
            config_id=config_id,
        )

        try:
            config = await self.repo.get_by_id(config_id)

            if not config:
                logger.warning(
                    "email_config_update_not_found",
                    config_id=config_id,
                )
                raise NotFoundException("EmailConfig", config_id)

            updated = await self.repo.update(config, data)

            logger.info(
                "email_config_update_success",
                config_id=config_id,
            )

            return EmailConfigResponse.model_validate(updated)

        except Exception as e:
            logger.exception(
                "email_config_update_failed",
                config_id=config_id,
                error=str(e),
            )
            raise

    async def delete(self, config_id: int) -> None:
        logger.info(
            "email_config_delete_started",
            config_id=config_id,
        )

        try:
            config = await self.repo.get_by_id(config_id)

            if not config:
                logger.warning(
                    "email_config_delete_not_found",
                    config_id=config_id,
                )
                raise NotFoundException("EmailConfig", config_id)

            await self.repo.delete(config)

            logger.info(
                "email_config_delete_success",
                config_id=config_id,
            )

        except Exception as e:
            logger.exception(
                "email_config_delete_failed",
                config_id=config_id,
                error=str(e),
            )
            raise