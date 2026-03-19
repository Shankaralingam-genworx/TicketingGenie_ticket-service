from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from src.api.dependencies import get_current_user, require_role
from src.constants.priority_constants import Priority
from src.constants.ticket_constants import TicketStatus
from src.core.services.assignment_service import AssignmentService
from src.core.services.ticket_service import TicketService
from src.data.clients.postgres_client import get_db
from src.data.repositories.shared_user_repository import SharedUserRepository
from src.schemas.ticket_filter_schema import parse_ticket_filters, TicketFilterParams
from src.schemas.ticket_schema import (
    AgentWorkloadResponse,
    EscalationReassignRequest,
    PaginatedTicketResponse,
    TicketAssignRequest,
    TicketResponse,
    TicketStatusUpdateRequest,
)


router = APIRouter(prefix="/tickets", tags=["Tickets"])


# ── Customer ──────────────────────────────────────────────────────────────────

@router.post("/", response_model=TicketResponse, status_code=201)
async def create_ticket(
    issue_id:     int           = Form(...),
    title:        str           = Form(...),
    description:  str           = Form(...),
    priority:     Priority      = Form(...),
    attachments:  List[UploadFile] = File(default=[]),
    current_user: dict          = Depends(require_role("customer")),
    db:           AsyncSession  = Depends(get_db),
):
    """Customer raises a new ticket. Auto-acknowledged immediately."""
    return await TicketService(db).create_ticket(
        issue_id=issue_id, title=title, description=description,
        priority=priority, attachments=attachments, current_user=current_user,
    )


@router.get("/me", response_model=list[TicketResponse])
async def my_tickets(
    current_user: dict         = Depends(require_role("customer")),
    db:           AsyncSession = Depends(get_db),
):
    return await TicketService(db).get_my_tickets(current_user["user_id"])


# ── Support Agent listings ────────────────────────────────────────────────────

@router.get("/assigned", response_model=PaginatedTicketResponse)
async def my_assigned_tickets(
    current_user: dict               = Depends(require_role("support_agent")),
    filters:      TicketFilterParams = Depends(parse_ticket_filters),
    db:           AsyncSession       = Depends(get_db),
):
    """Support Agent — all assigned tickets with filter/sort/pagination."""
    return await TicketService(db).get_agent_tickets_filtered(
        agent_id=current_user["user_id"], filters=filters,
    )




# ── Team Lead — fixed-path routes (must come before /{ticket_id}) ────────────

@router.get("/team/queue", response_model=PaginatedTicketResponse)
async def team_queue(
    current_user: dict               = Depends(require_role("team_lead")),
    filters:      TicketFilterParams = Depends(parse_ticket_filters),
    db:           AsyncSession       = Depends(get_db),
):
    """Assignment Queue — ACKNOWLEDGED unassigned tickets for this team."""
    return await TicketService(db).get_team_queue_filtered(
        team_id=current_user["team_id"], filters=filters,
    )


@router.get("/team/all", response_model=PaginatedTicketResponse)
async def team_all_tickets(
    current_user: dict               = Depends(require_role("team_lead")),
    filters:      TicketFilterParams = Depends(parse_ticket_filters),
    db:           AsyncSession       = Depends(get_db),
):
    """All Tickets — every status, full filter/search/sort/pagination."""
    return await TicketService(db).get_team_tickets_filtered(
        team_id=current_user["team_id"], filters=filters,
    )


@router.get("/team/agents", response_model=list[AgentWorkloadResponse])
async def team_agent_workload(
    detail:       bool               = Query(False),
    current_user: dict               = Depends(require_role("team_lead")),
    filters:      TicketFilterParams = Depends(parse_ticket_filters),
    db:           AsyncSession       = Depends(get_db),
):
    """Agent Workload — per-agent ticket summary with counts by status."""
    agents = await SharedUserRepository(db).get_team_agents(current_user["team_id"])
    return await TicketService(db).get_agent_workload(
        team_id=current_user["team_id"],
        agents=agents,
        filters=filters,
        include_tickets=detail,
    )


@router.get("/team", response_model=list[TicketResponse])
async def team_tickets_legacy(
    status:       Optional[List[TicketStatus]] = Query(None),
    current_user: dict         = Depends(require_role("team_lead")),
    db:           AsyncSession = Depends(get_db),
):
    """Legacy unfiltered endpoint — kept for backward compatibility."""
    return await TicketService(db).get_team_tickets(
        team_id=current_user["team_id"], status=status,
    )


# ── REQ-4: Start Working (must be above /{ticket_id}) ────────────────────────

@router.post("/{ticket_id}/start-working", response_model=TicketResponse)
async def start_working(
    ticket_id:    int,
    current_user: dict         = Depends(require_role("support_agent")),
    db:           AsyncSession = Depends(get_db),
):
    """
    Agent clicks 'Start Working' on ticket detail page.
    Transitions: ASSIGNED → OPEN
    Sets: work_started_at, first_response_at, resolution_due_at
    """
    return await TicketService(db).start_working(
        ticket_id=ticket_id,
        agent_id=current_user["user_id"],
    )


@router.patch("/{ticket_id}/status", response_model=TicketResponse)
async def update_ticket_status(
    ticket_id:    int,
    data:         TicketStatusUpdateRequest,
    current_user: dict         = Depends(require_role("support_agent")),
    db:           AsyncSession = Depends(get_db),
):
    """
    Agent sets next status via dropdown.
    Validates strict forward-only transitions based on role.
    """
    return await TicketService(db).update_status(
        ticket_id, data.status,
        current_user["user_id"], current_user["role"],
    )


@router.patch("/{ticket_id}/assign", response_model=TicketResponse)
async def assign_ticket(
    ticket_id:    int,
    data:         TicketAssignRequest,
    current_user: dict         = Depends(require_role("team_lead")),
    db:           AsyncSession = Depends(get_db),
):
    """
    Team lead assigns ticket to a support agent.
    • ACKNOWLEDGED → ASSIGNED  (normal flow)
    • NEW → ASSIGNED            (email-server-down bypass, REQ-3)
    Raises 409 if ticket is already escalated — use /escalation/reassign.
    """
    return await AssignmentService(db).assign_ticket(
        ticket_id=ticket_id, agent_id=data.agent_id,
        actor_id=current_user["user_id"], actor_role=current_user["role"],
        actor_team_id=current_user.get("team_id"),
    )


# ── REQ-5: Escalation reassignment ───────────────────────────────────────────

@router.post("/{ticket_id}/escalation/reassign", response_model=TicketResponse)
async def reassign_escalated_ticket(
    ticket_id:    int,
    data:         EscalationReassignRequest,
    current_user: dict         = Depends(require_role("team_lead")),
    db:           AsyncSession = Depends(get_db),
):
    """
    Team lead reassigns an escalated ticket to a NEW agent.
    Optionally provide escalated_resolution_mins to set a custom deadline.

    After reassignment:
      • Old agent is blocked from commenting
      • New agent must click 'Start Working' to restart the timer
      • ticket.status returns to ASSIGNED
    """
    return await AssignmentService(db).reassign_escalated_ticket(
        ticket_id=ticket_id, data=data,
        actor_id=current_user["user_id"], actor_role=current_user["role"],
        actor_team_id=current_user.get("team_id"),
    )


# ── Shared detail view (wildcard — must be last) ──────────────────────────────

@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id:    int,
    current_user: dict         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    return await TicketService(db).get_ticket(
        ticket_id=ticket_id,
        requester_id=current_user["user_id"],
        requester_role=current_user["role"],
        requester_team_id=current_user.get("team_id"),
    )