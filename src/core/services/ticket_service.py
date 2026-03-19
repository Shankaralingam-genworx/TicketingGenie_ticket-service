"""
Ticket service — orchestrates the full ticket lifecycle.
File: src/core/services/ticket_service.py
"""

import math
import uuid
import logging
import random
import string
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from src.data.repositories.escalation_repository import EscalationRepository
from src.core.celery.workers.email_tasks import (
    build_status_update_email,
    send_notification_task,
    send_ticket_acknowledgement_task,
    notify_team_lead_new_ticket_task,
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
from src.core.exceptions.base_exception import (
    ForbiddenException,
    InvalidTransitionException,
    NotFoundException,
)
from src.data.repositories.issue_repository import IssueRepository
from src.data.repositories.issue_resolver_repository import IssueResolverRepository
from src.data.repositories.sla_repository import SLARepository
from src.data.repositories.ticket_audit_repository import TicketAuditRepository
from src.data.repositories.ticket_repository import TicketRepository
from src.utils.gcs_utils import _inject_signed_urls, upload_images, TICKET_PREFIX
from src.schemas.ticket_filter_schema import TicketFilterParams
from src.schemas.ticket_schema import (
    AgentStatusCount,
    AgentWorkloadResponse,
    PaginatedTicketResponse,
    TicketResponse,
)
from src.utils.sla_utils import compute_due_at

logger = logging.getLogger("ticket.service")


# ── Attachment helpers ────────────────────────────────────────────────────────

async def _process_attachments(attachments: Optional[List[UploadFile]]) -> list[dict]:
    """Validate and upload ticket attachments to GCS. Images only."""
    return await upload_images(attachments, prefix=TICKET_PREFIX)


def _generate_ticket_number() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    month  = datetime.now(timezone.utc).strftime("%Y%m")
    return f"TKT-{month}-{suffix}"


# ── Main service ──────────────────────────────────────────────────────────────

class TicketService:
    def __init__(self, db: AsyncSession):
        self.db             = db
        self.ticket_repo    = TicketRepository(db)
        self.issue_repo     = IssueRepository(db)
        self.sla_repo       = SLARepository(db)
        self.resolver_repo  = IssueResolverRepository(db)
        self.audit_repo     = TicketAuditRepository(db)
        self.severity_agent = SeverityAgent(db)

    # ── REQ-1: Create — auto-acknowledge after ack email queued ───────────────

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

        issue = await self.issue_repo.get_by_id(issue_id)
        if not issue:
            raise NotFoundException("Issue", issue_id)

        severity = await self.severity_agent.detect(
            issue_name=issue.name, title=title, description=description,
        )
        logger.info(f"Severity detected: {severity} | customer={customer_id}")

        # Enterprise tier gets a severity bump
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
            logger.info(f"Priority override: {customer_priority} → {system_priority}")

        sla = await self.sla_repo.get_by_tier_and_severity(customer_tier, severity)
        response_due_at = sla_id = None
        if sla:
            # Response SLA starts from creation time
            response_due_at = compute_due_at(sla.response_time_mins)
            sla_id          = sla.id
            logger.info(f"SLA matched: {sla.name} (id={sla.id})")
        else:
            logger.warning(f"No SLA for tier={customer_tier} severity={severity}.")

        # resolution_due_at is intentionally NULL at creation.
        # It will be set in start_working() when the agent clicks "Start Working".

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
            resolution_due_at               = None,
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

        # Queue ack email via Celery
        sla_resolution_hrs: float | None = round(sla.resolution_time_mins / 60, 1) if sla else None
        send_ticket_acknowledgement_task.delay(
            ticket_id         = ticket.id,
            to_email          = ticket.customer_email,
            ticket_number     = ticket.ticket_number,
            ticket_title      = ticket.title,
            severity          = str(severity),
            priority          = str(system_priority),
            priority_overridden = priority_overridden,
            original_priority = customer_priority.value,
            system_priority   = system_priority.value,
            sla_resolution_hrs= sla_resolution_hrs,
        )

        # ── In-app notification: customer — ticket received ───────────────────
        await NotificationService(self.db).ticket_created(
            recipient_id  = customer_id,
            ticket_id     = ticket.id,
            ticket_number = ticket.ticket_number,
            ticket_title  = ticket.title,
        )

        # ── Email + in-app: team lead — new ticket in their queue ─────────────
        # Only fired when the ticket was routed to a team.
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

        # REQ-1: Immediately move to ACKNOWLEDGED so team lead queue shows correct status.
        # The email is already enqueued above; this just keeps the DB in sync.
        ticket = await self.ticket_repo.update(ticket, status=TicketStatus.ACKNOWLEDGED)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.STATUS_CHANGED,
            actor_id=0, actor_role="system",
            old_value={"status": TicketStatus.NEW.value},
            new_value={"status": TicketStatus.ACKNOWLEDGED.value},
        )

        return _inject_signed_urls(TicketResponse.model_validate(ticket))

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_ticket(
        self,
        ticket_id:         int,
        requester_id:      int,
        requester_role:    str,
        requester_team_id: int | None = None,
    ) -> TicketResponse:
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise NotFoundException("Ticket", ticket_id)

        if requester_role == "customer":
            if ticket.customer_id != requester_id:
                raise ForbiddenException("You can only view your own tickets.")

        elif requester_role == "support_agent":
            # Current assignee can view. Old escalated agent cannot view via
            # this route (they lost access when reassigned).
            if ticket.assigned_agent_id != requester_id:
                raise ForbiddenException("You can only view tickets currently assigned to you.")

        elif requester_role == "team_lead":
            if ticket.team_id != requester_team_id:
                raise ForbiddenException("You can only view tickets for your team.")

        return _inject_signed_urls(TicketResponse.model_validate(ticket))

    async def get_my_tickets(self, customer_id: int) -> list[TicketResponse]:
        return [
        _inject_signed_urls(TicketResponse.model_validate(t))
        for t in await self.ticket_repo.get_by_customer(customer_id)
    ]

    async def get_agent_tickets(self, agent_id: int) -> list[TicketResponse]:
        return [
        _inject_signed_urls(TicketResponse.model_validate(t))
        for t in await self.ticket_repo.get_by_agent(agent_id)
    ]

    async def get_team_tickets(
        self, team_id: int, status: list[TicketStatus] | None = None
    ) -> list[TicketResponse]:
        tickets = await self.ticket_repo.get_by_team(team_id, status=status)
        return [_inject_signed_urls(TicketResponse.model_validate(t)) for t in tickets]


    async def get_sla_dashboard(self, team_id: int) -> list[TicketResponse]:
        tickets = await self.ticket_repo.get_sla_watch(team_id)
        return [TicketResponse.model_validate(t) for t in tickets]

    # ── REQ-4: Start Working ──────────────────────────────────────────────────

    async def start_working(self, ticket_id: int, agent_id: int) -> TicketResponse:
        """
        Agent clicks "Start Working".

        Non-escalated ticket:
          ASSIGNED → OPEN
          work_started_at         = NOW
          first_response_at       = NOW  (if not already set)
          resolution_due_at       = NOW + sla.resolution_time_mins
          response_due_at         unchanged (used for SLA audit)
        """
        
        escalation_repo = EscalationRepository(self.db)

        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise NotFoundException("Ticket", ticket_id)

        if ticket.assigned_agent_id != agent_id:
            raise ForbiddenException("You can only start working on tickets assigned to you.")

        if ticket.status != TicketStatus.ASSIGNED:
            raise ForbiddenException(
                f"Ticket must be ASSIGNED to start working. "
                f"Current status: {ticket.status.value}"
            )

        now     = datetime.now(timezone.utc)
        updates = {
            "status":          TicketStatus.OPEN,
            "work_started_at": now,
        }

        if not ticket.first_response_at:
            updates["first_response_at"] = now

        # Load SLA policy for computing deadlines
        sla = await self.sla_repo.get_by_id(ticket.sla_id) if ticket.sla_id else None

        if ticket.is_escalated:
            # ── Escalated path: set escalated_resolution_due_at ───────────────
            escalation = await escalation_repo.get_by_ticket(ticket_id)

            # Resolve which resolution window to use
            if escalation and escalation.escalated_resolution_mins:
                resolution_mins = escalation.escalated_resolution_mins
                source = "escalation record (from reassignment)"
            elif sla and sla.additional_resolution_mins > 0:
                resolution_mins = sla.additional_resolution_mins
                source = f"SLA additional_resolution_mins ({sla.additional_resolution_mins})"
            elif sla:
                resolution_mins = sla.resolution_time_mins
                source = f"SLA resolution_time_mins fallback ({sla.resolution_time_mins})"
            else:
                resolution_mins = None
                source = "none — no SLA found"

            if resolution_mins:
                updates["escalated_resolution_due_at"] = compute_due_at(
                    resolution_mins, from_time=now
                )
                logger.info(
                    f"[start_working][escalated] {ticket.ticket_number} | "
                    f"escalated_resolution_due_at={updates['escalated_resolution_due_at']} "
                    f"({resolution_mins} mins, source: {source})"
                )

            # Log if response SLA was also breached (late start)
            effective_first_response = updates.get("first_response_at") or ticket.first_response_at
            if ticket.escalated_response_due_at and effective_first_response:
                if effective_first_response > ticket.escalated_response_due_at:
                    logger.warning(
                        f"[start_working] ESCALATED RESPONSE SLA BREACHED for "
                        f"{ticket.ticket_number} | "
                        f"first_response_at={effective_first_response.isoformat()} | "
                        f"escalated_response_due_at={ticket.escalated_response_due_at.isoformat()}"
                    )

        else:
            # ── Normal path: set resolution_due_at ───────────────────────────
            if sla:
                updates["resolution_due_at"] = compute_due_at(
                    sla.resolution_time_mins, from_time=now
                )
                logger.info(
                    f"[start_working] {ticket.ticket_number} | "
                    f"resolution_due_at={updates['resolution_due_at']} "
                    f"({sla.resolution_time_mins} mins from now)"
                )

            # Log if normal response SLA was breached (late start)
            effective_first_response = updates.get("first_response_at") or ticket.first_response_at
            if ticket.response_due_at and effective_first_response:
                if effective_first_response > ticket.response_due_at:
                    logger.warning(
                        f"[start_working] RESPONSE SLA BREACHED for {ticket.ticket_number} | "
                        f"first_response_at={effective_first_response.isoformat()} | "
                        f"response_due_at={ticket.response_due_at.isoformat()}"
                    )

        ticket = await self.ticket_repo.update(ticket, **updates)
        await self.audit_repo.log(
            ticket_id=ticket.id, action=AuditAction.WORK_STARTED,
            actor_id=agent_id, actor_role="support_agent",
            new_value={
                "is_escalated":              ticket.is_escalated,
                "work_started_at":           now.isoformat(),
                "first_response_at":         (updates.get("first_response_at") or ticket.first_response_at).isoformat()
                                             if (updates.get("first_response_at") or ticket.first_response_at) else None,
                "resolution_due_at":         ticket.resolution_due_at.isoformat()
                                             if ticket.resolution_due_at else None,
                "escalated_resolution_due_at": ticket.escalated_resolution_due_at.isoformat()
                                               if ticket.escalated_resolution_due_at else None,
            },
        )
        logger.info(f"[start_working] {ticket.ticket_number} → OPEN | agent={agent_id}")
        return _inject_signed_urls(TicketResponse.model_validate(ticket))

   

    async def update_status(
        self,
        ticket_id:  int,
        new_status: TicketStatus,
        actor_id:   int,
        actor_role: str,
    ) -> TicketResponse:
        """
        Called when agent selects a status from the dropdown on the ticket
        detail page.  Supports strictly forward transitions only.

        Allowed flows for support_agent:
          OPEN         → IN_PROGRESS | ON_HOLD | RESOLVED
          IN_PROGRESS  → ON_HOLD | RESOLVED
          ON_HOLD      → IN_PROGRESS | RESOLVED

        The ASSIGNED → OPEN transition is handled exclusively by start_working().
        """
        ticket = await self.ticket_repo.get_by_id(ticket_id)
        if not ticket:
            raise NotFoundException("Ticket", ticket_id)

        if ticket.assigned_agent_id != actor_id:
                raise ForbiddenException("You can only update tickets assigned to you.")
        # Agent may only set statuses in the allowed set
        if new_status not in AGENT_ALLOWED_TARGET_STATUSES:
            raise ForbiddenException(
                    f"Support agents can only set status to: "
                    f"{', '.join(s.value for s in AGENT_ALLOWED_TARGET_STATUSES)}."
            )

        # Validate the transition is in the global transition map
        allowed = VALID_TRANSITIONS.get(ticket.status, [])
        if new_status not in allowed:
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

        # ── In-app notification: customer — status changed ────────────────────
        await NotificationService(self.db).status_changed(
            recipient_id  = ticket.customer_id,
            ticket_id     = ticket.id,
            ticket_number = ticket.ticket_number,
            ticket_title  = ticket.title,
            old_status    = old_status.value,
            new_status    = new_status.value,
        )

        return _inject_signed_urls(TicketResponse.model_validate(ticket))

    # ── Filtered listing (unchanged logic, deduped method) ───────────────────

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
        self,
        org_id: int,
        filters: TicketFilterParams,
    ) -> PaginatedTicketResponse:
        """
        Return a paginated list of all tickets raised by customers
        that belong to the given organisation.

        Called by org_admin via GET /organisations/me/tickets.
        org_id is taken from the JWT claim — never from user input.
        """
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
                agent_id=aid, agent_name=agent.get("name", ""),
                agent_email=agent.get("email", ""), total=total_,
                by_status=by_status, tickets=tickets,
            ))
        results.sort(key=lambda r: r.total, reverse=True)
        return results
    
    