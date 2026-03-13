"""
Assignment service.
File: src/core/services/assignment_service.py
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import AuditAction
from src.constants.ticket_constants import TicketStatus
from src.core.celery.workers.email_tasks import build_assignment_email, send_notification_task
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

logger = logging.getLogger("ticket.assignment")


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
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise NotFoundException("Ticket", ticket_id)

        if actor_role == "team_lead":
            if actor_team_id is None or ticket.team_id != actor_team_id:
                raise ForbiddenException(
                    "Team leads can only assign tickets belonging to their team."
                )

        if ticket.is_escalated:
            raise ConflictException(
                "Ticket is escalated. Use POST /tickets/{id}/escalation/reassign "
                "to reassign it to a new agent."
            )

        old_agent = ticket.assigned_agent_id
        updates: dict = {"assigned_agent_id": agent_id}

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
            f"Ticket {ticket.ticket_number} assigned → agent={agent_id} "
            f"by {actor_role}={actor_id}  status={ticket.status.value}"
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
        """
        Team lead reassigns an escalated ticket.

        SLA window resolution order (highest priority first):
          1. data.escalated_response_mins    (lead override per-reassignment)
          2. sla.additional_response_mins    (admin-configured policy default)
          3. sla.response_time_mins          (fallback: use original response window)

        Same priority order for resolution mins.

        After this call:
          ticket.escalated_response_due_at   = NOW + resolved_response_mins
            → new agent's "Start Working" deadline
          ticket.escalated_resolution_due_at = None (set later in start_working)
          ticket.work_started_at             = None (new agent must click Start Working)
          ticket.status                      = ASSIGNED
          escalation.new_agent_id            = data.new_agent_id
        """
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise NotFoundException("Ticket", ticket_id)

        if not ticket.is_escalated:
            raise ConflictException(
                "Ticket is not escalated. Use PATCH /tickets/{id}/assign."
            )

        if actor_role == "team_lead":
            if actor_team_id is None or ticket.team_id != actor_team_id:
                raise ForbiddenException(
                    "Team leads can only reassign tickets in their team."
                )

        escalation = await self.escalation_repo.get_by_ticket(ticket_id)
        if not escalation:
            raise NotFoundException("Escalation record for ticket", ticket_id)

        if data.new_agent_id == escalation.old_agent_id:
            raise ConflictException(
                "Cannot reassign escalated ticket back to the same agent who was escalated."
            )

        # ── Resolve SLA windows ───────────────────────────────────────────────
        # Fetch the ticket's SLA policy to read additional_*_mins defaults
        sla = await self.sla_repo.get_by_id(ticket.sla_id) if ticket.sla_id else None

        # Response window for new agent: override → policy additional → policy normal
        if data.escalated_response_mins is not None:
            response_mins = data.escalated_response_mins
            logger.info(f"[escalation] Using lead-override response window: {response_mins} mins")
        elif sla and sla.additional_response_mins > 0:
            response_mins = sla.additional_response_mins
            logger.info(f"[escalation] Using SLA additional_response_mins: {response_mins} mins")
        elif sla:
            response_mins = sla.response_time_mins
            logger.info(f"[escalation] Falling back to SLA response_time_mins: {response_mins} mins")
        else:
            response_mins = None
            logger.warning(f"[escalation] No SLA found for ticket {ticket.ticket_number} — no response deadline set")

        # Resolution window: stored in escalation record, applied in start_working()
        # We just record the override here if provided
        if data.escalated_resolution_mins is not None:
            resolution_mins_override = data.escalated_resolution_mins
        elif sla and sla.additional_resolution_mins > 0:
            resolution_mins_override = sla.additional_resolution_mins
        elif sla:
            resolution_mins_override = sla.resolution_time_mins
        else:
            resolution_mins_override = None

        # ── Update escalation record ──────────────────────────────────────────
        now = datetime.now(timezone.utc)
        await self.escalation_repo.assign_new_agent(
            escalation,
            new_agent_id              = data.new_agent_id,
            escalated_resolution_mins = resolution_mins_override,
        )

        # ── Build ticket updates ──────────────────────────────────────────────
        updates: dict = {
            "assigned_agent_id":         data.new_agent_id,
            "status":                    TicketStatus.ASSIGNED,
            "work_started_at":           None,             # new agent must click Start Working
            "escalated_resolution_due_at": None,           # set in start_working()
        }

        # Set escalated response deadline (new agent must Start Working by this time)
        if response_mins is not None:
            updates["escalated_response_due_at"] = compute_due_at(response_mins, from_time=now)
            logger.info(
                f"[escalation] escalated_response_due_at={updates['escalated_response_due_at']} "
                f"({response_mins} mins from now)"
            )

        ticket = await self.ticket_repo.update(ticket, **updates)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.REASSIGNED,
            actor_id=actor_id, actor_role=actor_role,
            old_value={"assigned_agent_id": escalation.old_agent_id},
            new_value={
                "assigned_agent_id":          data.new_agent_id,
                "status":                     TicketStatus.ASSIGNED.value,
                "escalated_response_due_at":  ticket.escalated_response_due_at.isoformat()
                                              if ticket.escalated_response_due_at else None,
                "escalated_resolution_mins":  resolution_mins_override,
            },
        )

        await self._send_assignment_notifications(ticket, data.new_agent_id)
        logger.info(
            f"Escalated ticket {ticket.ticket_number} reassigned: "
            f"old_agent={escalation.old_agent_id} → new_agent={data.new_agent_id} | "
            f"escalated_response_due_at={ticket.escalated_response_due_at}"
        )
        return TicketResponse.model_validate(ticket)

    # ── Shared notification helper ────────────────────────────────────────────

    async def _send_assignment_notifications(self, ticket, agent_id: int) -> None:
        agent_email = await self.user_repo.get_user_email(agent_id)
        if agent_email:
            send_notification_task.delay(
                **vars(build_assignment_email(
                    to_email=agent_email,
                    ticket_number=ticket.ticket_number,
                    ticket_title=ticket.title,
                )),
                ticket_number=ticket.ticket_number,
                notification_type="assignment",
            )
        send_notification_task.delay(
            **vars(build_assignment_email(
                to_email=ticket.customer_email,
                ticket_number=ticket.ticket_number,
                ticket_title=ticket.title,
            )),
            ticket_number=ticket.ticket_number,
            notification_type="assignment",
        )
        await self.notif_svc.ticket_assigned(
            recipient_id=ticket.customer_id,
            ticket_id=ticket.id,
            ticket_number=ticket.ticket_number,
            ticket_title=ticket.title,
        )