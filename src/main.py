"""Entry point for the Ticket Service."""

import logging
import logging.config
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src.data.clients.postgres_client import create_tables


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


setup_logging()
logger = logging.getLogger("ticket.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Ticketing Genie — Ticket Service...")

    await create_tables()
    logger.info("Database tables created / verified.")

    logger.info("Ticket Service ready.")
    yield

    logger.info("Ticket Service shutting down.")


# Import after logging setup to avoid circular imports
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