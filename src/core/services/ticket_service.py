"""Ticket lifecycle orchestration — create, read, status transitions, SLA tracking."""

import math
import random
import string
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.celery.workers.email_tasks import (
    build_status_update_email,
    send_notification_task,
    send_ticket_acknowledgement_task,
    notify_team_lead_new_ticket_task,
)
from src.core.exceptions.base_exception import (
    ForbiddenException,
    InvalidTransitionException,
    NotFoundException,
)
from src.core.services.notification_service import NotificationService
from src.constants.priority_constants import SEVERITY_TO_PRIORITY, Priority
from src.constants.sla_constants import AuditAction, Severity
from src.constants.ticket_constants import (
    TicketSource,
    TicketStatus,
    VALID_TRANSITIONS,
    AGENT_ALLOWED_TARGET_STATUSES,
)
from src.control.agents.severity_agent import SeverityAgent
from src.data.repositories.escalation_repository import EscalationRepository
from src.data.repositories.issue_repository import IssueRepository
from src.data.repositories.issue_resolver_repository import IssueResolverRepository
from src.data.repositories.sla_repository import SLARepository
from src.data.repositories.ticket_audit_repository import TicketAuditRepository
from src.data.repositories.ticket_repository import TicketRepository
from src.schemas.ticket_filter_schema import TicketFilterParams
from src.schemas.ticket_schema import (
    AgentStatusCount,
    AgentWorkloadResponse,
    PaginatedTicketResponse,
    TicketResponse,
)
from src.utils.gcs_utils import _inject_signed_urls, upload_images, TICKET_PREFIX
from src.utils.sla_utils import compute_due_at
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="ticket-service")


# ── Helpers ───────────────────

async def _process_attachments(attachments: Optional[List[UploadFile]]) -> list[dict]:
    """Validate and upload ticket attachments to GCS."""
    return await upload_images(attachments, prefix=TICKET_PREFIX)


def _generate_ticket_number() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    month  = datetime.now(timezone.utc).strftime("%Y%m")
    return f"TKT-{month}-{suffix}"


# ── Service ──────────────────────
class TicketService:

    def __init__(self, db: AsyncSession):
        self.db             = db
        self.ticket_repo    = TicketRepository(db)
        self.issue_repo     = IssueRepository(db)
        self.sla_repo       = SLARepository(db)
        self.resolver_repo  = IssueResolverRepository(db)
        self.audit_repo     = TicketAuditRepository(db)
        self.severity_agent = SeverityAgent(db)

    # ── Create ──────────────────────────

    async def create_ticket(
        self,
        issue_id:     int,
        title:        str,
        description:  str,
        priority:     Priority,
        current_user: dict,
        attachments:  List[UploadFile] = None,
    ) -> TicketResponse:
        customer_id   = current_user["user_id"]
        customer_tier = (current_user.get("customer_tier") or "smb").lower()
        org_id        = current_user.get("org_id")

        logger.info(
            "create_ticket_started",
            customer_id=customer_id,
            issue_id=issue_id,
            customer_tier=customer_tier,
        )

        issue = await self.issue_repo.get_by_id(issue_id)
        if not issue:
            logger.warning("create_ticket_issue_not_found", issue_id=issue_id)
            raise NotFoundException("Issue", issue_id)

        severity = await self.severity_agent.detect(
            issue_name=issue.name, title=title, description=description,
        )
        logger.info("create_ticket_severity_detected", severity=severity, customer_id=customer_id)

        # Enterprise tier gets a severity bump for faster SLA response
        if customer_tier == "enterprise":
            if severity == Severity.HIGH:     severity = Severity.CRITICAL
            elif severity == Severity.MEDIUM: severity = Severity.HIGH
            elif severity == Severity.LOW:    severity = Severity.MEDIUM

        system_priority     = SEVERITY_TO_PRIORITY[severity]
        customer_priority   = priority
        priority_overridden = system_priority != customer_priority
        justification: str | None = None

        if priority_overridden:
            justification = (
                f"System overrode customer-requested priority '{customer_priority}' "
                f"to '{system_priority}' based on {severity.upper()} severity analysis."
            )
            logger.info(
                "create_ticket_priority_overridden",
                from_priority=customer_priority,
                to_priority=system_priority,
                severity=severity,
            )

        sla = await self.sla_repo.get_by_tier_and_severity(customer_tier, severity)
        response_due_at = sla_id = None

        if sla:
            # Response SLA clock starts at creation; resolution clock starts at start_working()
            response_due_at = compute_due_at(sla.response_time_mins)
            sla_id          = sla.id
            logger.info("create_ticket_sla_matched", sla_id=sla.id, sla_name=sla.name)
        else:
            logger.warning(
                "create_ticket_sla_not_found",
                customer_tier=customer_tier,
                severity=severity,
            )

        resolvers = await self.resolver_repo.get_by_issue(issue_id)
        team_id: int | None = resolvers[0].team_id if resolvers else None

        saved_attachments = await _process_attachments(attachments)

        ticket = await self.ticket_repo.create(
            ticket_number                   = _generate_ticket_number(),
            title                           = title,
            description                     = description,
            customer_id                     = customer_id,
            customer_email                  = current_user.get("email", ""),
            customer_tier                   = customer_tier,
            org_id                          = org_id,
            issue_id                        = issue_id,
            customer_priority               = customer_priority,
            priority                        = system_priority,
            priority_overridden             = priority_overridden,
            priority_override_justification = justification,
            severity                        = severity,
            status                          = TicketStatus.NEW,
            source                          = TicketSource.PORTAL,
            team_id                         = team_id,
            assigned_agent_id               = None,
            sla_id                          = sla_id,
            response_due_at                 = response_due_at,
            resolution_due_at               = None,   # set in start_working()
            attachments                     = saved_attachments or None,
        )

        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.CREATED,
            actor_id=customer_id, actor_role=current_user.get("role", "customer"),
            new_value={
                "ticket_number": ticket.ticket_number,
                "severity":      str(severity),
                "priority":      str(system_priority),
                "team_id":       team_id,
            },
        )

        sla_resolution_hrs = round(sla.resolution_time_mins / 60, 1) if sla else None
        send_ticket_acknowledgement_task.delay(
            ticket_id           = ticket.id,
            to_email            = ticket.customer_email,
            ticket_number       = ticket.ticket_number,
            ticket_title        = ticket.title,
            severity            = str(severity),
            priority            = str(system_priority),
            priority_overridden = priority_overridden,
            original_priority   = customer_priority.value,
            system_priority     = system_priority.value,
            sla_resolution_hrs  = sla_resolution_hrs,
        )

        await NotificationService(self.db).ticket_created(
            recipient_id  = customer_id,
            ticket_id     = ticket.id,
            ticket_number = ticket.ticket_number,
            ticket_title  = ticket.title,
        )

        if team_id:
            notify_team_lead_new_ticket_task.delay(
                ticket_id           = ticket.id,
                ticket_number       = ticket.ticket_number,
                ticket_title        = ticket.title,
                severity            = str(severity),
                priority            = str(system_priority),
                customer_email      = ticket.customer_email,
                customer_id         = customer_id,
                team_id             = team_id,
                priority_overridden = priority_overridden,
                original_priority   = customer_priority.value,
                system_priority     = system_priority.value,
            )

        # Auto-advance to ACKNOWLEDGED so team lead queue reflects correct status immediately
        ticket = await self.ticket_repo.update(ticket, status=TicketStatus.ACKNOWLEDGED)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.STATUS_CHANGED,
            actor_id=0, actor_role="system",
            old_value={"status": TicketStatus.NEW.value},
            new_value={"status": TicketStatus.ACKNOWLEDGED.value},
        )

        logger.info(
            "create_ticket_success",
            ticket_id=ticket.id,
            ticket_number=ticket.ticket_number,
            team_id=team_id,
        )
        return _inject_signed_urls(TicketResponse.model_validate(ticket))

    # ── Read ──────────────────

    async def get_ticket(
        self,
        ticket_id:         int,
        requester_id:      int,
        requester_role:    str,
        requester_team_id: int | None = None,
    ) -> TicketResponse:
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            logger.warning("get_ticket_not_found", ticket_id=ticket_id)
            raise NotFoundException("Ticket", ticket_id)

        # Role-based visibility: customers own-ticket, agents assigned-ticket, leads their team
        if requester_role == "customer":
            if ticket.customer_id != requester_id:
                logger.warning(
                    "get_ticket_forbidden_customer",
                    requester_id=requester_id,
                    ticket_id=ticket_id,
                )
                raise ForbiddenException("You can only view your own tickets.")

        elif requester_role == "support_agent":
            if ticket.assigned_agent_id != requester_id:
                logger.warning(
                    "get_ticket_forbidden_agent",
                    requester_id=requester_id,
                    ticket_id=ticket_id,
                )
                raise ForbiddenException("You can only view tickets currently assigned to you.")

        elif requester_role == "team_lead":
            if ticket.team_id != requester_team_id:
                logger.warning(
                    "get_ticket_forbidden_team_lead",
                    requester_id=requester_id,
                    ticket_id=ticket_id,
                    team_id=requester_team_id,
                )
                raise ForbiddenException("You can only view tickets for your team.")

        return _inject_signed_urls(TicketResponse.model_validate(ticket))

    async def get_my_tickets(self, customer_id: int) -> list[TicketResponse]:
        tickets = await self.ticket_repo.get_by_customer(customer_id)
        return [_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets]

    async def get_agent_tickets(self, agent_id: int) -> list[TicketResponse]:
        tickets = await self.ticket_repo.get_by_agent(agent_id)
        return [_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets]

    async def get_team_tickets(
        self, team_id: int, status: list[TicketStatus] | None = None
    ) -> list[TicketResponse]:
        tickets = await self.ticket_repo.get_by_team(team_id, status=status)
        return [_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets]

    async def get_sla_dashboard(self, team_id: int) -> list[TicketResponse]:
        tickets = await self.ticket_repo.get_sla_watch(team_id)
        return [TicketResponse.model_validate(t) for t in tickets]

    # ── Start Working ────────────────────────

    async def start_working(self, ticket_id: int, agent_id: int) -> TicketResponse:
        """
        Agent clicks Start Working. Sets resolution deadline and advances status to OPEN.
        Escalated tickets use a separate deadline field (escalated_resolution_due_at).
        """
        logger.info("start_working_started", ticket_id=ticket_id, agent_id=agent_id)

        escalation_repo = EscalationRepository(self.db)

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            logger.warning("start_working_ticket_not_found", ticket_id=ticket_id)
            raise NotFoundException("Ticket", ticket_id)

        if ticket.assigned_agent_id != agent_id:
            logger.warning(
                "start_working_forbidden", agent_id=agent_id, ticket_id=ticket_id
            )
            raise ForbiddenException("You can only start working on tickets assigned to you.")

        if ticket.status != TicketStatus.ASSIGNED:
            logger.warning(
                "start_working_invalid_status",
                ticket_id=ticket_id,
                current_status=ticket.status.value,
            )
            raise ForbiddenException(
                f"Ticket must be ASSIGNED to start working. "
                f"Current status: {ticket.status.value}"
            )

        now     = datetime.now(timezone.utc)
        updates = {"status": TicketStatus.OPEN, "work_started_at": now}

        if not ticket.first_response_at:
            updates["first_response_at"] = now

        sla = await self.sla_repo.get_by_id(ticket.sla_id) if ticket.sla_id else None

        if ticket.is_escalated:
            escalation = await escalation_repo.get_by_ticket(ticket_id)

            # Resolution window priority: escalation override → SLA additional → SLA normal
            if escalation and escalation.escalated_resolution_mins:
                resolution_mins = escalation.escalated_resolution_mins
                source = "escalation_record"
            elif sla and sla.additional_resolution_mins > 0:
                resolution_mins = sla.additional_resolution_mins
                source = "sla_additional"
            elif sla:
                resolution_mins = sla.resolution_time_mins
                source = "sla_fallback"
            else:
                resolution_mins = None
                source = "none"

            if resolution_mins:
                updates["escalated_resolution_due_at"] = compute_due_at(
                    resolution_mins, from_time=now
                )
                logger.info(
                    "start_working_escalated_deadline_set",
                    ticket_number=ticket.ticket_number,
                    resolution_mins=resolution_mins,
                    source=source,
                    escalated_resolution_due_at=updates["escalated_resolution_due_at"],
                )

            # Warn if the agent started late and response SLA is already breached
            effective_first_response = updates.get("first_response_at") or ticket.first_response_at
            if ticket.escalated_response_due_at and effective_first_response:
                if effective_first_response > ticket.escalated_response_due_at:
                    logger.warning(
                        "start_working_escalated_response_sla_breached",
                        ticket_number=ticket.ticket_number,
                        first_response_at=effective_first_response.isoformat(),
                        escalated_response_due_at=ticket.escalated_response_due_at.isoformat(),
                    )

        else:
            if sla:
                updates["resolution_due_at"] = compute_due_at(
                    sla.resolution_time_mins, from_time=now
                )
                logger.info(
                    "start_working_deadline_set",
                    ticket_number=ticket.ticket_number,
                    resolution_mins=sla.resolution_time_mins,
                    resolution_due_at=updates["resolution_due_at"],
                )

            # Warn if the agent started late and response SLA is already breached
            effective_first_response = updates.get("first_response_at") or ticket.first_response_at
            if ticket.response_due_at and effective_first_response:
                if effective_first_response > ticket.response_due_at:
                    logger.warning(
                        "start_working_response_sla_breached",
                        ticket_number=ticket.ticket_number,
                        first_response_at=effective_first_response.isoformat(),
                        response_due_at=ticket.response_due_at.isoformat(),
                    )

        ticket = await self.ticket_repo.update(ticket, **updates)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.WORK_STARTED,
            actor_id=agent_id, actor_role="support_agent",
            new_value={
                "is_escalated":                ticket.is_escalated,
                "work_started_at":             now.isoformat(),
                "first_response_at":           (
                    updates.get("first_response_at") or ticket.first_response_at
                ).isoformat() if (updates.get("first_response_at") or ticket.first_response_at) else None,
                "resolution_due_at":           ticket.resolution_due_at.isoformat()
                                               if ticket.resolution_due_at else None,
                "escalated_resolution_due_at": ticket.escalated_resolution_due_at.isoformat()
                                               if ticket.escalated_resolution_due_at else None,
            },
        )

        logger.info(
            "start_working_success",
            ticket_number=ticket.ticket_number,
            agent_id=agent_id,
            is_escalated=ticket.is_escalated,
        )
        return _inject_signed_urls(TicketResponse.model_validate(ticket))

    # ── Status update ─────────────────────────

    async def update_status(
        self,
        ticket_id:  int,
        new_status: TicketStatus,
        actor_id:   int,
        actor_role: str,
    ) -> TicketResponse:
        """
        Agent-driven status transitions (OPEN → IN_PROGRESS | ON_HOLD | RESOLVED etc.).
        ASSIGNED → OPEN is handled exclusively by start_working().
        """
        logger.info(
            "update_status_started",
            ticket_id=ticket_id,
            new_status=new_status,
            actor_id=actor_id,
        )

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            logger.warning("update_status_ticket_not_found", ticket_id=ticket_id)
            raise NotFoundException("Ticket", ticket_id)

        if ticket.assigned_agent_id != actor_id:
            logger.warning(
                "update_status_forbidden", actor_id=actor_id, ticket_id=ticket_id
            )
            raise ForbiddenException("You can only update tickets assigned to you.")

        if new_status not in AGENT_ALLOWED_TARGET_STATUSES:
            logger.warning(
                "update_status_disallowed_target",
                actor_id=actor_id,
                new_status=new_status,
            )
            raise ForbiddenException(
                f"Support agents can only set status to: "
                f"{', '.join(s.value for s in AGENT_ALLOWED_TARGET_STATUSES)}."
            )

        allowed = VALID_TRANSITIONS.get(ticket.status, [])
        if new_status not in allowed:
            logger.warning(
                "update_status_invalid_transition",
                ticket_id=ticket_id,
                from_status=ticket.status.value,
                to_status=new_status.value,
            )
            raise InvalidTransitionException(ticket.status.value, new_status.value)

        old_status = ticket.status
        updates: dict = {"status": new_status}
        now = datetime.now(timezone.utc)

        if new_status == TicketStatus.RESOLVED:
            updates["resolved_at"] = now
        if new_status == TicketStatus.CLOSED:
            updates["closed_at"] = now

        ticket = await self.ticket_repo.update(ticket, **updates)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.STATUS_CHANGED,
            actor_id=actor_id, actor_role=actor_role,
            old_value={"status": old_status.value},
            new_value={"status": new_status.value},
        )

        payload = build_status_update_email(
            to_email=ticket.customer_email, ticket_number=ticket.ticket_number,
            ticket_title=ticket.title, old_status=old_status.value,
            new_status=new_status.value, actor_role=actor_role,
        )
        send_notification_task.delay(
            **vars(payload), ticket_number=ticket.ticket_number,
            notification_type="status_update",
        )

        await NotificationService(self.db).status_changed(
            recipient_id  = ticket.customer_id,
            ticket_id     = ticket.id,
            ticket_number = ticket.ticket_number,
            ticket_title  = ticket.title,
            old_status    = old_status.value,
            new_status    = new_status.value,
        )

        logger.info(
            "update_status_success",
            ticket_id=ticket_id,
            from_status=old_status.value,
            to_status=new_status.value,
        )
        return _inject_signed_urls(TicketResponse.model_validate(ticket))

    # ── Filtered listing ────────────────

    async def get_agent_tickets_filtered(
        self, agent_id: int, filters: TicketFilterParams,
    ) -> PaginatedTicketResponse:
        tickets = await self.ticket_repo.get_agent_tickets_filtered(agent_id, filters)
        total   = await self.ticket_repo.count_agent_tickets_filtered(agent_id, filters)
        pages   = math.ceil(total / filters.per_page) if filters.per_page else 1
        return PaginatedTicketResponse(
            items=[_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets],
            total=total, page=filters.page, per_page=filters.per_page, pages=pages,
        )

    async def get_team_queue_filtered(
        self, team_id: int, filters: TicketFilterParams,
    ) -> PaginatedTicketResponse:
        tickets = await self.ticket_repo.get_team_queue_filtered(team_id, filters)
        total   = await self.ticket_repo.count_team_queue_filtered(team_id, filters)
        pages   = math.ceil(total / filters.per_page) if filters.per_page else 1
        return PaginatedTicketResponse(
            items=[_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets],
            total=total, page=filters.page, per_page=filters.per_page, pages=pages,
        )

    async def get_team_tickets_filtered(
        self, team_id: int, filters: TicketFilterParams,
    ) -> PaginatedTicketResponse:
        tickets = await self.ticket_repo.get_team_tickets_filtered(team_id, filters)
        total   = await self.ticket_repo.count_team_tickets_filtered(team_id, filters)
        pages   = math.ceil(total / filters.per_page) if filters.per_page else 1
        return PaginatedTicketResponse(
            items=[_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets],
            total=total, page=filters.page, per_page=filters.per_page, pages=pages,
        )

    async def get_org_tickets(
        self, org_id: int, filters: TicketFilterParams,
    ) -> PaginatedTicketResponse:
        """Paginated tickets for all customers under an org (org_admin only)."""
        tickets = await self.ticket_repo.get_by_org_filtered(org_id, filters)
        total   = await self.ticket_repo.count_by_org_filtered(org_id, filters)
        pages   = math.ceil(total / filters.per_page) if filters.per_page else 1
        return PaginatedTicketResponse(
            items=[_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets],
            total=total, page=filters.page, per_page=filters.per_page, pages=pages,
        )

    async def get_agent_workload(
        self,
        team_id:         int,
        agents:          list[dict],
        filters:         TicketFilterParams,
        include_tickets: bool = False,
    ) -> list[AgentWorkloadResponse]:
        raw_counts = await self.ticket_repo.get_agent_workload_summary(team_id)
        by_agent: dict[int, dict] = {}
        for row in raw_counts:
            aid = row["agent_id"]
            by_agent.setdefault(aid, {})[row["status"]] = row["count"]

        results: list[AgentWorkloadResponse] = []
        for agent in agents:
            aid        = agent["id"]
            status_map = by_agent.get(aid, {})
            total_     = sum(status_map.values())
            by_status  = [AgentStatusCount(status=s, count=c) for s, c in status_map.items()]
            tickets: list[TicketResponse] = []
            if include_tickets:
                raw     = await self.ticket_repo.get_agent_tickets_filtered(aid, filters)
                tickets = [_inject_signed_urls(TicketResponse.model_validate(t)) for t in raw]
            results.append(AgentWorkloadResponse(
                agent_id    = aid,
                agent_name  = agent.get("name", ""),
                agent_email = agent.get("email", ""),
                total       = total_,
                by_status   = by_status,
                tickets     = tickets,
            ))

        results.sort(key=lambda r: r.total, reverse=True)
        return results