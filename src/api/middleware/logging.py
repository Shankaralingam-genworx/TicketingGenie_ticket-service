"""Request/response logging middleware."""

from fastapi import FastAPI, Request
import logging
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger("ticket.middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()
        logger.info(f"[{req_id}] --> {request.method} {request.url.path}")
        response = await call_next(request)
        ms = (time.perf_counter() - start) * 1000
        logger.info(
            f"[{req_id}] <-- {request.method} {request.url.path} "
            f"| {response.status_code} | {ms:.1f}ms"
        )
        return response


def add_logging_middleware(app: FastAPI) -> None:
    app.add_middleware(LoggingMiddleware)
