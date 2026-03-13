"""Global error handler middleware."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core.exceptions.base_exception import AppException

logger = logging.getLogger("ticket.errors")


def add_error_handlers(app: FastAPI) -> None:

    @app.exception_handler(AppException)
    async def app_exc_handler(request: Request, exc: AppException):
        logger.warning(
            f"AppException [{exc.status_code}]: {exc.message} | {request.url.path}"
        )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def generic_exc_handler(request: Request, exc: Exception):
        logger.error(f"Unhandled error on {request.url.path}: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
