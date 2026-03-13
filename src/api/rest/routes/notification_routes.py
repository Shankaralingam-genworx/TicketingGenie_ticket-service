"""
Notification routes — REST CRUD + SSE stream.

File path: src/api/rest/routes/notification_routes.py

Endpoints
---------
GET  /notifications/               → list notifications for current user
GET  /notifications/unread-count   → badge counter (no SSE needed for initial load)
PATCH /notifications/{id}/read     → mark one read
PATCH /notifications/read-all      → mark all read
GET  /notifications/stream         → SSE stream — pushes unread count whenever
                                      something is written to Redis pub/sub channel
                                      "notifications:{user_id}"

SSE design
----------
The Celery tasks (and the comment / ticket service) publish to Redis when they
create a notification.  The browser keeps one EventSource open per session on
/api/v1/notifications/stream.  Every push carries a small JSON payload:

    {
      "event": "notification",
      "unread_count": 3,
      "notification": { ...NotificationResponse fields... }
    }

A keep-alive ping is sent every 20 s so the connection stays alive through
proxies.

Register in app.py:
    from src.api.rest.routes import notification_routes
    app.include_router(notification_routes.router, prefix="/api/v1")
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user
from src.config.settings import settings
from src.data.clients.postgres_client import get_db
from src.data.repositories.notification_repository import NotificationRepository
from src.schemas.notification_schema import (
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)

logger = logging.getLogger("ticket.notifications")
router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = False,
    limit:       int  = 30,
    offset:      int  = 0,
    current_user: dict         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """Return notifications for the logged-in user, newest first."""
    repo = NotificationRepository(db)
    items = await repo.get_for_user(
        current_user["user_id"],
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    unread_count = await repo.unread_count(current_user["user_id"])
    return NotificationListResponse(
        items        = [NotificationResponse.model_validate(n) for n in items],
        total_unread = unread_count,
        total        = len(items),
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    current_user: dict         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """Fast badge counter — used on initial page load."""
    repo = NotificationRepository(db)
    count = await repo.unread_count(current_user["user_id"])
    return UnreadCountResponse(unread_count=count)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def mark_one_read(
    notification_id: int,
    current_user:    dict         = Depends(get_current_user),
    db:              AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    repo    = NotificationRepository(db)
    updated = await repo.mark_read(notification_id, current_user["user_id"])
    if not updated:
        raise HTTPException(status_code=404, detail="Notification not found or already read")
    # Re-fetch to return the updated row
    items = await repo.get_for_user(current_user["user_id"], limit=1, offset=0)
    # find the specific one
    all_items = await repo.get_for_user(current_user["user_id"], limit=200)
    notif = next((n for n in all_items if n.id == notification_id), None)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationResponse.model_validate(notif)


@router.patch("/read-all", response_model=UnreadCountResponse)
async def mark_all_read(
    current_user: dict         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """Mark all unread notifications as read."""
    repo = NotificationRepository(db)
    await repo.mark_all_read(current_user["user_id"])
    return UnreadCountResponse(unread_count=0)


# ─────────────────────────────────────────────────────────────────────────────
# SSE stream
# ─────────────────────────────────────────────────────────────────────────────

async def _sse_event(event: str, data: dict) -> str:
    """Format a single SSE message."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _notification_stream(user_id: int):
    """
    Async generator that:
      1. Sends an immediate ping with the current unread count.
      2. Subscribes to Redis channel  notifications:{user_id}
      3. Yields an SSE event for every message published on that channel.
      4. Sends a keep-alive ping every 20 s.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    # One-off DB session just to get the initial count
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            count = await NotificationRepository(session).unread_count(user_id)
    finally:
        await engine.dispose()

    yield await _sse_event("connected", {
        "unread_count": count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Subscribe to Redis pub/sub
    redis = aioredis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
    pubsub = redis.pubsub()
    channel = f"notifications:{user_id}"
    await pubsub.subscribe(channel)
    logger.info(f"[SSE] User {user_id} subscribed to {channel}")

    try:
        while True:
            # Poll for new messages with a short timeout so we can interleave keep-alives
            message = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                timeout=20.0,
            )

            if message and message.get("type") == "message":
                try:
                    payload = json.loads(message["data"])
                    yield await _sse_event("notification", payload)
                except json.JSONDecodeError:
                    pass
            else:
                # No message in 20 s → send keep-alive ping
                yield await _sse_event("ping", {
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

    except asyncio.CancelledError:
        logger.info(f"[SSE] User {user_id} disconnected from {channel}")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await redis.aclose()


@router.get("/stream")
async def notification_stream(
    current_user: dict = Depends(get_current_user),
):
    """
    SSE endpoint — keep one EventSource open per browser tab.

    Browser usage:
        const es = new EventSource('/api/v1/notifications/stream', {
            headers: { Authorization: `Bearer ${token}` }
        });
        es.addEventListener('notification', (e) => {
            const { unread_count, notification } = JSON.parse(e.data);
            setBadge(unread_count);
            addToDropdown(notification);
        });
        es.addEventListener('ping', () => {});   // ignore keep-alives
    """
    return StreamingResponse(
        _notification_stream(current_user["user_id"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",       # nginx: disable buffering
            "Connection":       "keep-alive",
        },
    )