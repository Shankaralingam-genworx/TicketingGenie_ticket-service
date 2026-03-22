"""Ticket assignment and escalation reassignment."""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import AuditAction
from src.constants.ticket_constants import TicketStatus
from src.core.celery.workers.email_tasks import (
    build_assignment_email,
    build_agent_assigned_email,
    build_escalation_agent_reassignment_email,
    send_notification_task,
)
from src.core.exceptions.base_exception import (
    ConflictException, ForbiddenException, NotFoundException,
)
from src.core.services.notification_service import NotificationService
from src.data.repositories.escalation_repository import EscalationRepository
from src.data.repositories.shared_user_repository import SharedUserRepository
from src.data.repositories.sla_repository import SLARepository
from src.data.repositories.ticket_audit_repository import TicketAuditRepository
from src.data.repositories.ticket_repository import TicketRepository
from src.schemas.ticket_schema import EscalationReassignRequest, TicketResponse
from src.utils.sla_utils import compute_due_at
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


class AssignmentService:

    def __init__(self, db: AsyncSession):
        self.ticket_repo     = TicketRepository(db)
        self.audit_repo      = TicketAuditRepository(db)
        self.escalation_repo = EscalationRepository(db)
        self.sla_repo        = SLARepository(db)
        self.notif_svc       = NotificationService(db)
        self.user_repo       = SharedUserRepository(db)

    # ── Normal assignment ─────────────────────────────────────────────────────

    async def assign_ticket(
        self,
        ticket_id:     int,
        agent_id:      int,
        actor_id:      int,
        actor_role:    str,
        actor_team_id: int | None = None,
    ) -> TicketResponse:
        logger.info(
            "assign_ticket_started",
            ticket_id=ticket_id,
            agent_id=agent_id,
            actor_id=actor_id,
            actor_role=actor_role,
        )

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            logger.warning("assign_ticket_not_found", ticket_id=ticket_id)
            raise NotFoundException("Ticket", ticket_id)

        # Team leads can only assign tickets within their own team
        if actor_role == "team_lead":
            if actor_team_id is None or ticket.team_id != actor_team_id:
                logger.warning(
                    "assign_ticket_forbidden_team_lead",
                    actor_id=actor_id,
                    ticket_team_id=ticket.team_id,
                    actor_team_id=actor_team_id,
                )
                raise ForbiddenException(
                    "Team leads can only assign tickets belonging to their team."
                )

        # Escalated tickets must go through the escalation reassign flow
        if ticket.is_escalated:
            logger.warning("assign_ticket_is_escalated", ticket_id=ticket_id)
            raise ConflictException(
                "Ticket is escalated. Use POST /tickets/{id}/escalation/reassign "
                "to reassign it to a new agent."
            )

        old_agent = ticket.assigned_agent_id
        updates: dict = {"assigned_agent_id": agent_id}

        # Auto-advance status on first assignment
        if ticket.status in (TicketStatus.NEW, TicketStatus.ACKNOWLEDGED):
            updates["status"] = TicketStatus.ASSIGNED

        ticket = await self.ticket_repo.update(ticket, **updates)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.ASSIGNED,
            actor_id=actor_id, actor_role=actor_role,
            old_value={"assigned_agent_id": old_agent},
            new_value={"assigned_agent_id": agent_id, "status": ticket.status.value},
        )

        await self._send_assignment_notifications(ticket, agent_id)

        logger.info(
            "assignment_success",
            ticket_number=ticket.ticket_number,
            agent_id=agent_id,
            status=ticket.status.value,
        )
        return TicketResponse.model_validate(ticket)

    # ── Escalation reassignment ───────────────────────────────────────────────

    async def reassign_escalated_ticket(
        self,
        ticket_id:     int,
        data:          EscalationReassignRequest,
        actor_id:      int,
        actor_role:    str,
        actor_team_id: int | None = None,
    ) -> TicketResponse:
        logger.info(
            "reassign_escalated_ticket_started",
            ticket_id=ticket_id,
            new_agent_id=data.new_agent_id,
            actor_id=actor_id,
            actor_role=actor_role,
        )

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            logger.warning("reassign_escalated_ticket_not_found", ticket_id=ticket_id)
            raise NotFoundException("Ticket", ticket_id)

        if not ticket.is_escalated:
            logger.warning("reassign_escalated_ticket_not_escalated", ticket_id=ticket_id)
            raise ConflictException(
                "Ticket is not escalated. Use PATCH /tickets/{id}/assign."
            )

        if actor_role == "team_lead":
            if actor_team_id is None or ticket.team_id != actor_team_id:
                logger.warning(
                    "reassign_escalated_ticket_forbidden_team_lead",
                    actor_id=actor_id,
                    ticket_team_id=ticket.team_id,
                    actor_team_id=actor_team_id,
                )
                raise ForbiddenException(
                    "Team leads can only reassign tickets in their team."
                )

        escalation = await self.escalation_repo.get_by_ticket(ticket_id)
        if not escalation:
            logger.warning("reassign_escalated_ticket_no_escalation_record", ticket_id=ticket_id)
            raise NotFoundException("Escalation record for ticket", ticket_id)

        # Prevent routing back to the agent who was escalated away from
        if data.new_agent_id == escalation.old_agent_id:
            logger.warning(
                "reassign_escalated_ticket_same_agent",
                ticket_id=ticket_id,
                old_agent_id=escalation.old_agent_id,
            )
            raise ConflictException(
                "Cannot reassign escalated ticket back to the same agent who was escalated."
            )

        # Resolve SLA windows: lead override → policy additional → policy normal → None
        sla = await self.sla_repo.get_by_id(ticket.sla_id) if ticket.sla_id else None

        if data.escalated_response_mins is not None:
            response_mins = data.escalated_response_mins
            logger.info("escalation_response_window_override", response_mins=response_mins)
        elif sla and sla.additional_response_mins > 0:
            response_mins = sla.additional_response_mins
            logger.info("escalation_response_window_sla_additional", response_mins=response_mins)
        elif sla:
            response_mins = sla.response_time_mins
            logger.info("escalation_response_window_sla_fallback", response_mins=response_mins)
        else:
            response_mins = None
            logger.warning("escalation_response_window_missing", ticket_id=ticket_id)

        if data.escalated_resolution_mins is not None:
            resolution_mins_override = data.escalated_resolution_mins
        elif sla and sla.additional_resolution_mins > 0:
            resolution_mins_override = sla.additional_resolution_mins
        elif sla:
            resolution_mins_override = sla.resolution_time_mins
        else:
            resolution_mins_override = None

        now = datetime.now(timezone.utc)
        await self.escalation_repo.assign_new_agent(
            escalation,
            new_agent_id              = data.new_agent_id,
            escalated_resolution_mins = resolution_mins_override,
        )

        updates: dict = {
            "assigned_agent_id":           data.new_agent_id,
            "status":                      TicketStatus.ASSIGNED,
            "work_started_at":             None,   # new agent must click Start Working
            "escalated_resolution_due_at": None,   # set when agent starts working
        }

        if response_mins is not None:
            updates["escalated_response_due_at"] = compute_due_at(response_mins, from_time=now)

        ticket = await self.ticket_repo.update(ticket, **updates)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.REASSIGNED,
            actor_id=actor_id, actor_role=actor_role,
            old_value={"assigned_agent_id": escalation.old_agent_id},
            new_value={
                "assigned_agent_id":         data.new_agent_id,
                "status":                    TicketStatus.ASSIGNED.value,
                "escalated_response_due_at": (
                    ticket.escalated_response_due_at.isoformat()
                    if ticket.escalated_response_due_at else None
                ),
                "escalated_resolution_mins": resolution_mins_override,
            },
        )

        await self._send_assignment_notifications(ticket, data.new_agent_id, is_escalation=True)

        logger.info(
            "reassign_escalated_ticket_success",
            ticket_number=ticket.ticket_number,
            old_agent_id=escalation.old_agent_id,
            new_agent_id=data.new_agent_id,
            escalated_response_due_at=ticket.escalated_response_due_at,
        )
        return TicketResponse.model_validate(ticket)

    # ── Notification helper ───────────────────────────────────────────────────

    async def _send_assignment_notifications(
        self, ticket, agent_id: int, is_escalation: bool = False
    ) -> None:
        """Send email + in-app notifications to both agent and customer."""
        severity = ticket.severity.value if hasattr(ticket.severity, "value") else str(ticket.severity)
        priority = ticket.priority.value if hasattr(ticket.priority, "value") else str(ticket.priority)

        agent_email = await self.user_repo.get_user_email(agent_id)
        if agent_email:
            if is_escalation:
                response_due_str = (
                    ticket.escalated_response_due_at.strftime("%Y-%m-%d %H:%M UTC")
                    if ticket.escalated_response_due_at else None
                )
                agent_payload = build_escalation_agent_reassignment_email(
                    to_email        = agent_email,
                    ticket_number   = ticket.ticket_number,
                    ticket_title    = ticket.title,
                    severity        = severity,
                    priority        = priority,
                    current_status  = ticket.status.value,
                    response_due_at = response_due_str,
                )
                notification_type = "escalation_agent"
            else:
                agent_payload = build_agent_assigned_email(
                    to_email      = agent_email,
                    ticket_number = ticket.ticket_number,
                    ticket_title  = ticket.title,
                    severity      = severity,
                    priority      = priority,
                )
                notification_type = "assignment_agent"

            send_notification_task.delay(
                **vars(agent_payload),
                ticket_number     = ticket.ticket_number,
                notification_type = notification_type,
            )

        await self.notif_svc.ticket_assigned_agent(
            recipient_id  = agent_id,
            ticket_id     = ticket.id,
            ticket_number = ticket.ticket_number,
            ticket_title  = ticket.title,
        )

        send_notification_task.delay(
            **vars(build_assignment_email(
                to_email      = ticket.customer_email,
                ticket_number = ticket.ticket_number,
                ticket_title  = ticket.title,
            )),
            ticket_number     = ticket.ticket_number,
            notification_type = "assignment_customer",
        )

        await self.notif_svc.ticket_assigned(
            recipient_id  = ticket.customer_id,
            ticket_id     = ticket.id,
            ticket_number = ticket.ticket_number,
            ticket_title  = ticket.title,
        )