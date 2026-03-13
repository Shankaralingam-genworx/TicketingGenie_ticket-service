"""FastAPI application factory for the Ticket Service.

File path: src/api/rest/app.py

Changes from original:
  - Added import of notification_routes
  - Added app.include_router(notification_routes.router, prefix="/api/v1")
"""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from src.api.middleware.auth_client_middleware import add_auth_middleware
from src.api.middleware.cors import add_cors_middleware
from src.api.middleware.error_handler import add_error_handlers
from src.api.middleware.logging import add_logging_middleware
from src.api.rest.routes import (
    comment_routes,
    dashboard_routes,
    health,
    issue_resolver_routes,
    issue_routes,
    notification_routes,  
    sla_routes,
    sse,
    ticket_routes,
    websocket,
    email_config_routes
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ticketing Genie — Ticket Service",
        description="Support ticket management: creation, lifecycle, SLA, comments, audit.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    add_error_handlers(app)
    add_auth_middleware(app)
    add_logging_middleware(app)
    add_cors_middleware(app)

    # Routers
    app.include_router(health.router)
    app.include_router(ticket_routes.router,           prefix="/api/v1")
    app.include_router(issue_routes.router,            prefix="/api/v1")
    app.include_router(issue_resolver_routes.router,   prefix="/api/v1")
    app.include_router(sla_routes.router,              prefix="/api/v1")
    app.include_router(comment_routes.router,          prefix="/api/v1")
    app.include_router(dashboard_routes.router,        prefix="/api/v1")
    app.include_router(notification_routes.router,     prefix="/api/v1")  # ← NEW
    app.include_router(email_config_routes.router,     prefix="/api/v1") 
    app.include_router(sse.router)
    app.include_router(websocket.router)

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema["components"]["securitySchemes"] = {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
        for path in schema.get("paths", {}).values():
            for op in path.values():
                op["security"] = [{"bearerAuth": []}]
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi
    return app