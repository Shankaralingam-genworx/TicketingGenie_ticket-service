from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
import logging
from src.config.settings import settings
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("ticket.auth")

# Paths that skip authentication entirely
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/sse/tickets"}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Decodes JWT and injects user_id, role, tier, team_id into request.state.

    Token resolution order:
      1. Authorization: Bearer <token>  header
      2. ?token=<value>                 query param  (for SSE/EventSource)
    """

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public endpoints and WebSocket upgrades
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/ws"):
            return await call_next(request)

        # ── 1. Try Authorization header ────────────────────────────────────────
        token: str | None = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()

        # ── 2. Fall back to ?token= query param (SSE / EventSource) ───────────
        if not token:
            token = request.query_params.get("token")

        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or malformed Bearer token"},
            )

        # ── Decode and inject ──────────────────────────────────────────────────
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
            request.state.user_id       = int(payload.get("sub", 0))
            request.state.email         = str(payload.get("email", ""))
            request.state.role          = str(payload.get("role", ""))
            request.state.customer_tier = payload.get("customer_tier")
            request.state.team_id       = payload.get("team_id")
            # org_id — present for org_admin and customer roles; NULL for staff
            request.state.org_id        = payload.get("org_id")

            logger.debug(
                f"Auth OK | user={request.state.user_id} "
                f"role={request.state.role} "
                f"tier={request.state.customer_tier} "
                f"team={request.state.team_id} "
                f"org={request.state.org_id}"
            )

        except ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Token expired"})
        except JWTError:
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})

        return await call_next(request)


def add_auth_middleware(app: FastAPI) -> None:
    app.add_middleware(AuthMiddleware)