"""Comment routes.
File: src/api/rest/routes/comment_routes.py added

Change from previous version:
  POST /{ticket_id}/comments now accepts multipart/form-data so the caller
  can optionally attach image files alongside the comment text.

  The frontend sends:
    Content-Type: multipart/form-data
    content     (form field, optional — empty string is fine if files attached)
    source      (form field, optional, default "portal")
    attachments (one or more file parts, optional)

  Both customer and agent can attach images.
  Max file size and allowed MIME types are enforced inside CommentService
  via the same validate_upload() used for ticket attachments.
"""

from typing import List

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user
from src.constants.sla_constants import CommentSource
from src.core.services.comment_service import CommentService
from src.data.clients.postgres_client import get_db
from src.schemas.comment_schema import CommentCreateRequest, CommentResponse

router = APIRouter(prefix="/tickets", tags=["Comments"])




@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(
    ticket_id:    int,
    # Form fields 
    
    content:      str           = Form(default=""),
    source:       CommentSource = Form(default=CommentSource.PORTAL),
    # Zero or more image attachments
    attachments:  List[UploadFile] = File(default=[]),
    current_user: dict          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
    """
    Add a comment to a ticket.

    Accepts multipart/form-data:
      - content     (str, optional if attachments present)
      - source      (portal | email | agent, default: portal)
      - attachments (images, optional, multiple allowed, max 10 MB each)

    Access rules:
      - Customer:      only on their own tickets
      - Support Agent: only on tickets currently assigned to them
                       (blocked if ticket is escalated and they are old_agent)
      - Team Lead:     any ticket in their team
    """
    data = CommentCreateRequest(content=content, source=source)
    service = CommentService(db)
    return await service.add_comment(
        ticket_id   = ticket_id,
        data        = data,
        author_id   = current_user["user_id"],
        author_role = current_user["role"],
        attachments = attachments or None,
    )


@router.get("/{ticket_id}/comments", response_model=list[CommentResponse])
async def get_comments(
    ticket_id:    int,
    current_user: dict         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """
    Fetch the full comment thread for a ticket.
    All roles see all comments including any attached images.
    """
    service = CommentService(db)
    return await service.get_comments(ticket_id, current_user["role"])