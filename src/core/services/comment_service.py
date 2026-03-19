from datetime import datetime, timezone
from typing import List, Optional
import logging

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import AuditAction
from src.core.exceptions.base_exception import ForbiddenException, NotFoundException
from src.data.repositories.comment_repository import CommentRepository
from src.data.repositories.escalation_repository import EscalationRepository
from src.data.repositories.ticket_audit_repository import TicketAuditRepository
from src.data.repositories.ticket_repository import TicketRepository
from src.utils.gcs_utils import upload_images, COMMENT_PREFIX, generate_signed_url
from src.schemas.comment_schema import CommentCreateRequest, CommentResponse
from src.core.celery.workers.email_tasks import build_comment_email, send_notification_task
from src.core.services.notification_service import NotificationService
from src.data.models.postgres.notification_model import NotificationActor
from src.data.repositories.shared_user_repository import SharedUserRepository

logger = logging.getLogger("ticket.comment")

STAFF_ROLES = {"support_agent", "SUPPORT_AGENT", "team_lead", "TEAM_LEAD", "admin", "ADMIN"}


# ── Attachment helpers ────────────────────────────────────────────────────────

async def _process_comment_attachments(
    attachments: Optional[List[UploadFile]],
) -> list[dict]:
    """Validate and upload comment image attachments to GCS."""
    return await upload_images(attachments, prefix=COMMENT_PREFIX)


def _inject_comment_signed_urls(response: CommentResponse) -> CommentResponse:
    """
    Replace stored GCS URLs with fresh signed URLs (60 min).
    """
    if not response.attachments:
        return response
    signed = []
    for att in response.attachments:
        signed.append(att.model_copy(update={
            "url": generate_signed_url(att.path, expiry_minutes=60)
        }))
    return response.model_copy(update={"attachments": signed})


# ── Service ───────────────────────────────────────────────────────────────────

class CommentService:
    def __init__(self, db: AsyncSession):
        self.db              = db
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

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise NotFoundException("Ticket", ticket_id)

        has_content    = bool(data.content and data.content.strip())
        has_attachment = bool(attachments)
        if not has_content and not has_attachment:
            raise ForbiddenException("A comment must have content or at least one attachment.")

        # ── Role-based access control ─────────────────────────────────────────
        if author_role in ("customer", "CUSTOMER"):
            if ticket.customer_id != author_id:
                raise ForbiddenException("You can only comment on your own tickets.")

        elif author_role in ("support_agent", "SUPPORT_AGENT"):
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
            pass

        # ── Process & upload attachments ──────────────────────────────────────
        saved_attachments = await _process_comment_attachments(attachments)

        # ── Persist comment ───────────────────────────────────────────────────
        comment = await self.comment_repo.create(
            ticket_id   = ticket_id,
            author_id   = author_id,
            author_role = author_role,
            content     = data.content,
            source      = data.source,
            attachments = saved_attachments if saved_attachments else None,
        )

        # ── SLA: first response tracking ──────────────────────────────────────
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
                "comment_id":      comment.id,
                "has_attachments": bool(saved_attachments),
            },
        )

        # ── Notifications ─────────────────────────────────────────────────────
        notif_svc = NotificationService(self.db)
        preview   = (data.content or "")[:80]

        if author_role in ("customer", "CUSTOMER"):
            if ticket.assigned_agent_id:
                agent_email = await SharedUserRepository(self.db).get_user_email(
                    ticket.assigned_agent_id
                )
                if agent_email:
                    send_notification_task.delay(
                        **vars(build_comment_email(
                            to_email      = agent_email,
                            ticket_number = ticket.ticket_number,
                            ticket_title  = ticket.title,
                            author_role   = author_role,
                            comment_body  = data.content or "",
                        )),
                        ticket_number     = ticket.ticket_number,
                        notification_type = "comment_to_agent",
                    )
                await notif_svc.comment_received(
                    recipient_id   = ticket.assigned_agent_id,
                    recipient_role = NotificationActor.SUPPORT_AGENT,
                    ticket_id      = ticket.id,
                    ticket_number  = ticket.ticket_number,
                    ticket_title   = ticket.title,
                    author_role    = author_role,
                    preview        = preview,
                )
        else:
            send_notification_task.delay(
                **vars(build_comment_email(
                    to_email      = ticket.customer_email,
                    ticket_number = ticket.ticket_number,
                    ticket_title  = ticket.title,
                    author_role   = author_role,
                    comment_body  = data.content or "",
                )),
                ticket_number     = ticket.ticket_number,
                notification_type = "comment_to_customer",
            )
            await notif_svc.comment_received(
                recipient_id   = ticket.customer_id,
                recipient_role = NotificationActor.CUSTOMER,
                ticket_id      = ticket.id,
                ticket_number  = ticket.ticket_number,
                ticket_title   = ticket.title,
                author_role    = author_role,
                preview        = preview,
            )

        # ── Return with signed URLs so the poster sees images immediately ─────
        response = CommentResponse.model_validate(comment)
        return _inject_comment_signed_urls(response)

    async def get_comments(self, ticket_id: int, role: str) -> list[CommentResponse]:
        comments = await self.comment_repo.get_by_ticket(ticket_id)
        result = []
        for c in comments:
            response = CommentResponse.model_validate(c)
            result.append(_inject_comment_signed_urls(response))
        return result