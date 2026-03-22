"""Comment creation and retrieval with attachment support."""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import AuditAction
from src.core.exceptions.base_exception import ForbiddenException, NotFoundException
from src.data.repositories.comment_repository import CommentRepository
from src.data.repositories.escalation_repository import EscalationRepository
from src.data.repositories.ticket_audit_repository import TicketAuditRepository
from src.data.repositories.ticket_repository import TicketRepository
from src.data.repositories.shared_user_repository import SharedUserRepository
from src.data.models.postgres.notification_model import NotificationActor
from src.utils.gcs_utils import upload_images, COMMENT_PREFIX, generate_signed_url
from src.schemas.comment_schema import CommentCreateRequest, CommentResponse
from src.core.celery.workers.email_tasks import build_comment_email, send_notification_task
from src.core.services.notification_service import NotificationService
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")

STAFF_ROLES = {"support_agent", "SUPPORT_AGENT", "team_lead", "TEAM_LEAD", "admin", "ADMIN"}


# ── Attachment helpers ────────────────────────

async def _process_comment_attachments(
    attachments: Optional[List[UploadFile]],
) -> list[dict]:
    """Validate and upload comment attachments to GCS."""
    return await upload_images(attachments, prefix=COMMENT_PREFIX)


def _inject_comment_signed_urls(response: CommentResponse) -> CommentResponse:
    """Replace stored GCS paths with 60-min signed URLs."""
    if not response.attachments:
        return response
    signed = [
        att.model_copy(update={"url": generate_signed_url(att.path, expiry_minutes=60)})
        for att in response.attachments
    ]
    return response.model_copy(update={"attachments": signed})


# ── Service ─────────────────────────

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
        logger.info(
            "comment_create_started",
            ticket_id=ticket_id,
            author_id=author_id,
            author_role=author_role,
            has_attachments=bool(attachments),
        )

        try:
            ticket = await self.ticket_repo.get_by_id(ticket_id)
            if not ticket:
                logger.warning(
                    "comment_ticket_not_found",
                    ticket_id=ticket_id,
                )
                raise NotFoundException("Ticket", ticket_id)

            has_content    = bool(data.content and data.content.strip())
            has_attachment = bool(attachments)

            if not has_content and not has_attachment:
                logger.warning(
                    "comment_empty_rejected",
                    ticket_id=ticket_id,
                    author_id=author_id,
                )
                raise ForbiddenException(
                    "A comment must have content or at least one attachment."
                )

            # ── Role validation ──
            if author_role in ("customer", "CUSTOMER"):
                if ticket.customer_id != author_id:
                    logger.warning(
                        "comment_forbidden_customer",
                        ticket_id=ticket_id,
                        author_id=author_id,
                    )
                    raise ForbiddenException("You can only comment on your own tickets.")

            elif author_role in ("support_agent", "SUPPORT_AGENT"):
                if ticket.is_escalated:
                    escalation = await self.escalation_repo.get_by_ticket(ticket_id)
                    if escalation and escalation.old_agent_id == author_id:
                        logger.warning(
                            "comment_forbidden_escalated_agent",
                            ticket_id=ticket_id,
                            author_id=author_id,
                        )
                        raise ForbiddenException(
                            "This ticket has been escalated and reassigned."
                        )

                if ticket.assigned_agent_id != author_id:
                    logger.warning(
                        "comment_forbidden_unassigned_agent",
                        ticket_id=ticket_id,
                        author_id=author_id,
                    )
                    raise ForbiddenException(
                        "You can only comment on tickets assigned to you."
                    )

            # ── Attachments ──
            saved_attachments = await _process_comment_attachments(attachments)

            if saved_attachments:
                logger.info(
                    "comment_attachments_uploaded",
                    ticket_id=ticket_id,
                    count=len(saved_attachments),
                )

            comment = await self.comment_repo.create(
                ticket_id   = ticket_id,
                author_id   = author_id,
                author_role = author_role,
                content     = data.content,
                source      = data.source,
                attachments = saved_attachments or None,
            )

            # ── SLA first response ──
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

                logger.info(
                    "comment_first_response_recorded",
                    ticket_id=ticket_id,
                    timestamp=now.isoformat(),
                )

            await self.audit_repo.log(
                ticket_id  = ticket_id,
                action     = AuditAction.COMMENTED,
                actor_id   = author_id,
                actor_role = author_role,
                new_value  = {
                    "comment_id": comment.id,
                    "has_attachments": bool(saved_attachments),
                },
            )

            await self._send_comment_notifications(ticket, data, author_role, author_id)

            logger.info(
                "comment_create_success",
                ticket_id=ticket_id,
                comment_id=comment.id,
                has_attachments=bool(saved_attachments),
            )

            response = CommentResponse.model_validate(comment)
            return _inject_comment_signed_urls(response)

        except Exception as e:
            logger.exception(
                "comment_create_failed",
                ticket_id=ticket_id,
                author_id=author_id,
                error=str(e),
            )
            raise

    async def get_comments(self, ticket_id: int, role: str) -> list[CommentResponse]:
        logger.info(
            "comments_fetch_started",
            ticket_id=ticket_id,
            role=role,
        )

        try:
            comments = await self.comment_repo.get_by_ticket(ticket_id)

            result = [
                _inject_comment_signed_urls(CommentResponse.model_validate(c))
                for c in comments
            ]

            logger.info(
                "comments_fetch_success",
                ticket_id=ticket_id,
                count=len(result),
            )

            return result

        except Exception as e:
            logger.exception(
                "comments_fetch_failed",
                ticket_id=ticket_id,
                error=str(e),
            )
            raise
        
    # ── Notification helper ──────────────
    async def _send_comment_notifications(
        self, ticket, data: CommentCreateRequest, author_role: str, author_id: int
    ) -> None:
        logger.info(
            "comment_notifications_started",
            ticket_id=ticket.id,
            author_id=author_id,
            author_role=author_role,
        )

        notif_svc = NotificationService(self.db)
        preview   = (data.content or "")[:80]

        try:
            if author_role in ("customer", "CUSTOMER"):
                if ticket.assigned_agent_id:
                    logger.info(
                        "notify_agent_for_comment",
                        ticket_id=ticket.id,
                        agent_id=ticket.assigned_agent_id,
                    )

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
                logger.info(
                    "notify_customer_for_comment",
                    ticket_id=ticket.id,
                    customer_id=ticket.customer_id,
                )

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

            logger.info(
                "comment_notifications_sent",
                ticket_id=ticket.id,
            )

        except Exception as e:
            logger.exception(
                "comment_notifications_failed",
                ticket_id=ticket.id,
                error=str(e),
            )

    async def enhance_comment(self, comment_text: str, role: str = "customer") -> str:
        """
        Enhance a comment using Groq LLM with role-specific enhancement.

        Role-based enhancement:
        - Customer: Polite, clear, issue-focused tone
        - Support Agent: Professional, concise, internal communication
        - Team Lead: Authoritative, strategic, professional tone

        Args:
            comment_text: The raw comment text to enhance
            role: User role (customer, support_agent, team_lead)

        Returns:
            Enhanced comment text. If enhancement fails, returns original text.
        """
        from src.control.agents.comment_enhancer import get_groq_client

        logger.info(
            "comment_enhancement_started",
            text_length=len(comment_text),
            role=role,
        )

        try:
            groq = get_groq_client()
            enhanced = await groq.enhance_comment(comment_text, role=role)

            if enhanced:
                logger.info(
                    "comment_enhancement_success",
                    original_length=len(comment_text),
                    enhanced_length=len(enhanced),
                    role=role,
                )
                return enhanced

            logger.warning("comment_enhancement_failed_returning_original", role=role)
            return comment_text

        except Exception as e:
            logger.exception(
                "comment_enhancement_exception",
                error=str(e),
                role=role,
            )
            # Return original text if enhancement fails
            return comment_text