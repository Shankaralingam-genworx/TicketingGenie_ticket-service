"""WebSocket endpoint for real-time ticket collaboration."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/ws", tags=["WebSocket"])


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@router.websocket("/tickets")
async def ws_tickets(websocket: WebSocket):
    """WebSocket for real-time ticket updates. Broadcasts to all connected clients."""
    await manager.connect(websocket)
    await websocket.send_text(json.dumps({
        "event": "connected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(json.dumps({
                "event": "echo",
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
