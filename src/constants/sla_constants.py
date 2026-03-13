"""SLA-related enums.
File: src/constants/sla_constants.py
"""

from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"


class CustomerTier(str, Enum):
    ENTERPRISE = "enterprise"
    SMB        = "smb"


class CommentSource(str, Enum):
    PORTAL = "portal"
    EMAIL  = "email"
    AGENT  = "agent"


class AuditAction(str, Enum):
    CREATED        = "created"
    ASSIGNED       = "assigned"
    REASSIGNED     = "reassigned"
    STATUS_CHANGED = "status_changed"
    COMMENTED      = "commented"
    ESCALATED      = "escalated"
    RESOLVED       = "resolved"
    CLOSED         = "closed"
    REOPENED       = "reopened"
    EMAIL_SENT     = "email_sent"
    SLA_BREACHED   = "sla_breached"
    WORK_STARTED   = "work_started"   # agent clicked "Start Working" — resolution SLA clock starts
    UPDATED        = "updated"        # generic field update (e.g. first_response_at)