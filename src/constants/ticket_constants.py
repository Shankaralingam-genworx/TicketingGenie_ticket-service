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


AGENT_ALLOWED_TARGET_STATUSES: set[TicketStatus] = {
    TicketStatus.IN_PROGRESS,
    TicketStatus.ON_HOLD,
    TicketStatus.RESOLVED,
    TicketStatus.REOPENED,
}

# Statuses a team_lead is allowed to set directly 
TEAM_LEAD_ALLOWED_TARGET_STATUSES: set[TicketStatus] = {
    TicketStatus.ASSIGNED,
}