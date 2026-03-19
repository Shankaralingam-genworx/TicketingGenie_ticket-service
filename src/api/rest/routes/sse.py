"""Server-Sent Events endpoint for real-time ticket updates."""
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json

router = APIRouter(prefix="/sse", tags=["SSE"])


async def ticket_event_stream():
    """Yields a keep-alive ping every 15 seconds. Extend with real events."""
    while True:
        data = json.dumps({
            "event": "ping",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        yield f"data: {data}\n\n"
        await asyncio.sleep(15)


@router.get("/tickets")
async def sse_tickets():
    """
    SSE stream for ticket events.
    Connect with EventSource in the browser to receive live updates.
    No authentication required so browsers can connect without extra headers.
    """
    return StreamingResponse(ticket_event_stream(), media_type="text/event-stream")
