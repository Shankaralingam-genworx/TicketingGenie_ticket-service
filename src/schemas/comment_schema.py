from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from src.constants.sla_constants import CommentSource
from src.schemas.attachment_schema import AttachmentMeta


class CommentCreateRequest(BaseModel):
    # Content is optional so a user can post an image-only comment
    content: str = Field("", min_length=0)
    source:  CommentSource = CommentSource.PORTAL


class CommentResponse(BaseModel):
    id:          int
    ticket_id:   int
    author_id:   int
    author_role: str
    content:     str
    source:      CommentSource
    attachments: Optional[List[AttachmentMeta]] = None
    created_at:  datetime

    model_config = {"from_attributes": True}