from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import get_current_user
from src.constants.sla_constants import CommentSource
from src.core.services.comment_service import CommentService
from src.data.clients.postgres_client import get_db
from src.schemas.comment_schema import (
    CommentCreateRequest,
    CommentResponse,
    CommentEnhanceRequest,
    CommentEnhanceResponse,
)
from typing import List

router = APIRouter(prefix="/tickets", tags=["Comments"])

@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(
    ticket_id:    int,
    
    
    content:      str           = Form(default=""),
    source:       CommentSource = Form(default=CommentSource.PORTAL),
    attachments:  List[UploadFile] = File(default=[]),
    current_user: dict          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
   
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
    
    service = CommentService(db)
    return await service.get_comments(ticket_id, current_user["role"])


@router.post("/{ticket_id}/comments/enhance", response_model=CommentEnhanceResponse, status_code=200)
async def enhance_comment(
    ticket_id:    int,
    request:      CommentEnhanceRequest,
    current_user: dict         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
   
    service = CommentService(db)
    enhanced = await service.enhance_comment(
        request.content,
        role=current_user["role"]
    )
    return CommentEnhanceResponse(enhanced_content=enhanced)