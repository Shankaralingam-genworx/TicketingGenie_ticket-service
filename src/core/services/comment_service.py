"""Comment service.
File: src/core/services/comment_service.py

Changes from previous version
──────────────────────────────
ATTACHMENTS
  add_comment() now accepts a list of UploadFile objects.
  Each file is validated + saved via the same _process_attachments() helper
  used by TicketService.  The resulting list of dicts is stored in
  Comment.attachments (JSON column).
  Both customers and agents can attach images to their comments.

REQ-4  first_response_at is set on first STAFF comment if not already set
       by start_working().

REQ-5  Old escalated agent is blocked from commenting after reassignment.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import uuid
import logging

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import AuditAction
from src.core.exceptions.base_exception import ForbiddenException, NotFoundException
from src.data.repositories.comment_repository import CommentRepository
from src.data.repositories.escalation_repository import EscalationRepository
from src.data.repositories.ticket_audit_repository import TicketAuditRepository
from src.data.repositories.ticket_repository import TicketRepository
from src.schemas.attachment_schema import UploadValidationError, validate_upload
from src.schemas.comment_schema import CommentCreateRequest, CommentResponse

logger = logging.getLogger("ticket.comment")

# Store comment attachments in a sub-folder so they are easy to separate from
# ticket cover-image attachments (both are served by the same StaticFiles mount
# or S3 bucket prefix).
COMMENT_UPLOAD_DIR = Path("uploads/comments")

# Roles whose comments count as an official staff response for SLA tracking
STAFF_ROLES = {"support_agent", "SUPPORT_AGENT", "team_lead", "TEAM_LEAD", "admin", "ADMIN"}


# ── Attachment helper (mirrors _process_attachments in ticket_service) ────────

async def _process_comment_attachments(
    attachments: Optional[List[UploadFile]],
) -> list[dict]:
    """
    Validate, save and return attachment metadata for comment files.

    Uses the same validation rules as ticket attachments (MIME type + 10 MB
    size limit) and the same dict shape so the frontend can reuse the same
    AttachmentGallery component to display them.
    """
    if not attachments:
        return []

    COMMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for file in attachments:
        data       = await file.read()
        size_bytes = len(data)

        try:
            validate_upload(file.filename or "upload", file.content_type, size_bytes)
        except UploadValidationError as exc:
            logger.warning(f"[comment attachment] Skipped '{file.filename}' — {exc}")
            continue

        original_name = file.filename or "upload"
        ext           = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
        stored_name   = f"{uuid.uuid4()}.{ext}"
        file_path     = COMMENT_UPLOAD_DIR / stored_name

        with open(file_path, "wb") as buf:
            buf.write(data)

        rel_path = str(file_path)   # e.g. "uploads/comments/abc123.png"
        url      = f"/{rel_path}"   # e.g. "/uploads/comments/abc123.png"

        results.append({
            "original_name": original_name,
            "stored_name":   stored_name,
            "content_type":  file.content_type,
            "size_bytes":    size_bytes,
            "path":          rel_path,
            "url":           url,
        })

        logger.info(
            f"[comment attachment] Saved '{original_name}' → '{stored_name}' "
            f"({size_bytes} bytes, {file.content_type})"
        )

    return results


# ── Service ───────────────────────────────────────────────────────────────────

class CommentService:
    def __init__(self, db: AsyncSession):
        self.comment_repo    = CommentRepository(db)
        self.ticket_repo     = TicketRepository(db)
        self.escalation_repo = EscalationRepository(db)
        self.audit_repo      = TicketAuditRepository(db)

    async def add_comment(
        self,
        ticket_id:   int,
        data:        CommentCreateRequest,
        author_id:   int,
        author_role: str,
        attachments: Optional[List[UploadFile]] = None,
    ) -> CommentResponse:
        """
        Add a comment (optionally with image attachments) to a ticket.

        Validation:
          - content OR at least one attachment must be present
          - role-based access control (see below)
          - escalated old-agent block (REQ-5)
        """
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise NotFoundException("Ticket", ticket_id)

        # ── Must have content OR attachment ───────────────────────────────────
        has_content    = bool(data.content and data.content.strip())
        has_attachment = bool(attachments)
        if not has_content and not has_attachment:
            raise ForbiddenException("A comment must have content or at least one attachment.")

        # ── Role-based access control ─────────────────────────────────────────

        if author_role in ("customer", "CUSTOMER"):
            if ticket.customer_id != author_id:
                raise ForbiddenException("You can only comment on your own tickets.")

        elif author_role in ("support_agent", "SUPPORT_AGENT"):
            # REQ-5: Block old escalated agent from commenting after reassignment
            if ticket.is_escalated:
                escalation = await self.escalation_repo.get_by_ticket(ticket_id)
                if escalation and escalation.old_agent_id == author_id:
                    raise ForbiddenException(
                        "This ticket has been escalated and reassigned. "
                        "You no longer have comment access."
                    )
            if ticket.assigned_agent_id != author_id:
                raise ForbiddenException("You can only comment on tickets assigned to you.")

        elif author_role in ("team_lead", "TEAM_LEAD"):
            # Team lead can comment on any ticket in their team.
            # team_id scope is enforced at route layer via require_role.
            pass

        # ── Process attachments ───────────────────────────────────────────────
        saved_attachments = await _process_comment_attachments(attachments)

        # ── Create comment ────────────────────────────────────────────────────
        comment = await self.comment_repo.create(
            ticket_id   = ticket_id,
            author_id   = author_id,
            author_role = author_role,
            content     = data.content,
            source      = data.source,
            attachments = saved_attachments if saved_attachments else None,
        )

        # ── REQ-4: Set first_response_at on first staff comment ───────────────
        # Only if not already set (start_working may have set it earlier).
        # Customer comments never count.
        if author_role in STAFF_ROLES and ticket.first_response_at is None:
            now = datetime.now(timezone.utc)
            await self.ticket_repo.update(ticket, first_response_at=now)
            await self.audit_repo.log(
                ticket_id  = ticket_id,
                action     = AuditAction.UPDATED,
                actor_id   = author_id,
                actor_role = author_role,
                new_value  = {"first_response_at": now.isoformat()},
            )

        await self.audit_repo.log(
            ticket_id  = ticket_id,
            action     = AuditAction.COMMENTED,
            actor_id   = author_id,
            actor_role = author_role,
            new_value  = {
                "comment_id":  comment.id,
                "has_attachments": bool(saved_attachments),
            },
        )

        return CommentResponse.model_validate(comment)

    async def get_comments(self, ticket_id: int, role: str) -> list[CommentResponse]:
        comments = await self.comment_repo.get_by_ticket(ticket_id)
        return [CommentResponse.model_validate(c) for c in comments]