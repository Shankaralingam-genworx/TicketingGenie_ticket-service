"""Health check route."""

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    return {"status": "ok", "service": "ticketing-ticket-service"}
