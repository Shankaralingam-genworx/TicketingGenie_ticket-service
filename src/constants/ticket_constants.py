"""Ticket-related enums and constants.
File: src/constants/ticket_constants.py
"""

from enum import Enum


class TicketStatus(str, Enum):
    NEW          = "new"
    ACKNOWLEDGED = "acknowledged"
    ASSIGNED     = "assigned"
    OPEN         = "open"
    IN_PROGRESS  = "in_progress"
    ON_HOLD      = "on_hold"
    RESOLVED     = "resolved"
    CLOSED       = "closed"
    REOPENED     = "reopened"


class TicketSource(str, Enum):
    PORTAL = "portal"
    EMAIL  = "email"


# ── Valid status transitions (single source of truth) ────────────────────────
#
# Full lifecycle:
#   NEW          → ACKNOWLEDGED  (auto: ack email queued on ticket creation)
#   NEW          → ASSIGNED      (team lead bypass when email server is down)
#   ACKNOWLEDGED → ASSIGNED      (team lead assigns ticket to agent)
#   ASSIGNED     → OPEN          (agent clicks "Start Working" button)
#   OPEN         → IN_PROGRESS   (agent changes status via dropdown)
#   IN_PROGRESS  → ON_HOLD       (agent puts ticket on hold)
#   IN_PROGRESS  → RESOLVED      (agent marks resolved)
#   ON_HOLD      → IN_PROGRESS   (agent resumes work)
#   RESOLVED     → CLOSED        (system/admin closes)
#   CLOSED       → REOPENED      (customer or admin reopens)
#   REOPENED     → IN_PROGRESS   (agent picks back up)

VALID_TRANSITIONS: dict[TicketStatus, list[TicketStatus]] = {
    TicketStatus.NEW:          [TicketStatus.ACKNOWLEDGED, TicketStatus.ASSIGNED],
    TicketStatus.ACKNOWLEDGED: [TicketStatus.ASSIGNED],
    TicketStatus.ASSIGNED:     [TicketStatus.OPEN],
    TicketStatus.OPEN:         [TicketStatus.IN_PROGRESS,TicketStatus.ON_HOLD, TicketStatus.RESOLVED],
    TicketStatus.IN_PROGRESS:  [TicketStatus.ON_HOLD, TicketStatus.RESOLVED],
    TicketStatus.ON_HOLD:      [TicketStatus.IN_PROGRESS,TicketStatus.RESOLVED],
    TicketStatus.RESOLVED:     [TicketStatus.CLOSED],
    TicketStatus.CLOSED:       [TicketStatus.REOPENED],
    TicketStatus.REOPENED:     [TicketStatus.IN_PROGRESS,TicketStatus.ON_HOLD, TicketStatus.RESOLVED],
}

# Statuses a support_agent is allowed to set via the dropdown.
AGENT_ALLOWED_TARGET_STATUSES: set[TicketStatus] = {
    TicketStatus.IN_PROGRESS,
    TicketStatus.ON_HOLD,
    TicketStatus.RESOLVED,
    TicketStatus.REOPENED,
}

# Statuses a team_lead is allowed to set directly (assignment is handled
# by the dedicated /assign endpoint which also validates this).
TEAM_LEAD_ALLOWED_TARGET_STATUSES: set[TicketStatus] = {
    TicketStatus.ASSIGNED,
}