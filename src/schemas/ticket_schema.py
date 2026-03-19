"""Ticket Pydantic schemas.
File: src/schemas/ticket_schema.py
"""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, computed_field

from src.constants.priority_constants import Priority
from src.constants.sla_constants import Severity
from src.constants.ticket_constants import TicketSource, TicketStatus
from src.schemas.issue_schema import IssueResponse
from src.schemas.sla_schema import SLAResponse
from src.schemas.attachment_schema import AttachmentMeta  # noqa: F401


# ── Request schemas ───────────────────────────────────────────────────────────

class TicketStatusUpdateRequest(BaseModel):
    status: TicketStatus


class TicketAssignRequest(BaseModel):
    agent_id: int = Field(..., description="Support agent user_id")


class EscalationReassignRequest(BaseModel):
    """
    Team lead reassigns an escalated ticket to a new agent.

    Override fields (both optional):
      escalated_response_mins   — override SLA.additional_response_mins
      escalated_resolution_mins — override SLA.additional_resolution_mins
    If not provided, values from the SLA policy are used automatically.
    """
    new_agent_id:              int        = Field(..., description="New agent's user_id")
    escalated_response_mins:   float | None = Field(
        None, gt=0,
        description="Override: response window for new agent in minutes. "
                    "Defaults to SLA.additional_response_mins."
    )
    escalated_resolution_mins: float | None = Field(
        None, gt=0,
        description="Override: resolution window for new agent in minutes. "
                    "Defaults to SLA.additional_resolution_mins."
    )


# ── Escalation response ───────────────────────────────────────────────────────

class EscalationResponse(BaseModel):
    id:                        int
    ticket_id:                 int
    old_agent_id:              int
    new_agent_id:              int | None
    reason:                    str
    notes:                     str | None
    escalated_by:              str
    escalated_at:              datetime
    reassigned_at:             datetime | None
    escalated_resolution_mins: float | None

    model_config = {"from_attributes": True}


# ── Core ticket response ──────────────────────────────────────────────────────

class TicketResponse(BaseModel):
    id:             int
    ticket_number:  str
    title:          str
    description:    str
    customer_id:    int
    # Plain tier name string (e.g. "smb", "enterprise") — not an enum.
    customer_tier:  str
    customer_email: str
    # org_id from auth service — NULL for customers not in an organisation.
    org_id:         int | None = None
    issue_id:       int | None
    issue:          IssueResponse | None = None

    customer_priority:               Priority
    priority:                        Priority
    priority_overridden:             bool
    priority_override_justification: str | None

    severity: Severity
    status:   TicketStatus
    source:   TicketSource

    team_id:           int | None
    assigned_agent_id: int | None

    sla_id: int | None
    sla:    SLAResponse | None = None

    # Normal SLA deadlines
    response_due_at:   datetime | None   # ticket creation + SLA.response_time_mins
    resolution_due_at: datetime | None   # start_working + SLA.resolution_time_mins

    # Escalation SLA deadlines (set after reassignment)
    escalated_response_due_at:   datetime | None  # reassignment + additional_response_mins
    escalated_resolution_due_at: datetime | None  # new agent's start_working + additional_resolution_mins

    # Timestamps
    work_started_at:   datetime | None
    first_response_at: datetime | None
    resolved_at:       datetime | None
    closed_at:         datetime | None

    # Escalation
    is_escalated: bool
    escalated_at: datetime | None
    escalation:   EscalationResponse | None = None

    attachments: Optional[List[AttachmentMeta]] = None

    created_at: datetime
    updated_at: datetime

    # ── Computed SLA breach flags ─────────────────────────────────────────────

    @computed_field  # type: ignore[misc]
    @property
    def response_sla_breached(self) -> bool:
        """Normal response SLA breached (non-escalated)."""
        if self.response_due_at is None:
            return False
        if self.first_response_at is not None:
            return self.first_response_at > self.response_due_at   # responded late
        return datetime.now(timezone.utc) > self.response_due_at   # not responded yet + overdue

    @computed_field  # type: ignore[misc]
    @property
    def resolution_sla_breached(self) -> bool:
        """Normal resolution SLA breached (non-escalated)."""
        if self.resolution_due_at is None:
            return False
        if self.resolved_at is not None:
            return self.resolved_at > self.resolution_due_at
        return datetime.now(timezone.utc) > self.resolution_due_at

    @computed_field  # type: ignore[misc]
    @property
    def escalated_response_sla_breached(self) -> bool:
        """Escalated response SLA breached (new agent after reassignment)."""
        if self.escalated_response_due_at is None:
            return False
        if self.first_response_at is not None:
            return self.first_response_at > self.escalated_response_due_at
        return datetime.now(timezone.utc) > self.escalated_response_due_at

    @computed_field  # type: ignore[misc]
    @property
    def escalated_resolution_sla_breached(self) -> bool:
        """Escalated resolution SLA breached (new agent after reassignment)."""
        if self.escalated_resolution_due_at is None:
            return False
        if self.resolved_at is not None:
            return self.resolved_at > self.escalated_resolution_due_at
        return datetime.now(timezone.utc) > self.escalated_resolution_due_at

    model_config = {"from_attributes": True}


# ── Paginated wrapper ─────────────────────────────────────────────────────────

class PaginatedTicketResponse(BaseModel):
    items:    List[TicketResponse]
    total:    int
    page:     int
    per_page: int
    pages:    int


# ── Agent workload ────────────────────────────────────────────────────────────

class AgentStatusCount(BaseModel):
    status: TicketStatus
    count:  int


class AgentWorkloadResponse(BaseModel):
    agent_id:    int
    agent_name:  str
    agent_email: str
    total:       int
    by_status:   List[AgentStatusCount]
    tickets:     List[TicketResponse] = []