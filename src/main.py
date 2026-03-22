"""Entry point for the Ticket Service."""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src.init_db import main as init_database
from src.data.clients.postgres_client import create_tables
from src.observability.logging.logger import setup_logging, get_logger

# Setup structured logging
setup_logging()
logger = get_logger(__name__).bind(service="ticket-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("service_starting")

    await create_tables()
    logger.info("db_tables_ready")

    await init_database()

    logger.info("service_ready")
    yield

    logger.info("service_stopping")


# Import after logging setup
from src.api.rest.app import create_app  # noqa: E402

app = create_app()
app.router.lifespan_context = lifespan


if __name__ == "__main__":
    from src.config.settings import settings

    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        reload=settings.APP_ENV == "development",
    )