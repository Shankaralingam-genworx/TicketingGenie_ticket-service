"""
Notification Pydantic schemas.

File path: src/schemas/notification_schema.py
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from src.data.models.postgres.notification_model import NotificationActor, NotificationType


class NotificationResponse(BaseModel):
    id:             int
    recipient_id:   int
    recipient_role: NotificationActor
    ticket_id:      Optional[int]      = None
    ticket_number:  Optional[str]      = None
    type:           NotificationType
    title:          str
    message:        str
    is_read:        bool
    read_at:        Optional[datetime] = None
    created_at:     datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    items:        List[NotificationResponse]
    total_unread: int
    total:        int


class UnreadCountResponse(BaseModel):
    unread_count: int