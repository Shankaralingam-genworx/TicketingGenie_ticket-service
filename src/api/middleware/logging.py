import time
import uuid

import structlog
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class LoggingMiddleware(BaseHTTPMiddleware):
    """Structured request logging with request tracing."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())

        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()

        logger.info(
            "request_start",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query),
        )

        try:
            response = await call_next(request)

        except Exception as e:
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                error=str(e),
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "request_end",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

        structlog.contextvars.clear_contextvars()

        return response


def add_logging_middleware(app: FastAPI) -> None:
    app.add_middleware(LoggingMiddleware)