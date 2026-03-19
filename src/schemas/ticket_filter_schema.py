
from typing import List, Optional
from fastapi import Query
from pydantic import BaseModel

from src.constants.ticket_constants import TicketStatus
from src.constants.priority_constants import Priority
from src.constants.sla_constants import Severity


class TicketFilterParams(BaseModel):
    search:       Optional[str]                = None
    status:       Optional[List[TicketStatus]] = None
    priority:     Optional[List[Priority]]     = None
    severity:     Optional[List[Severity]]     = None
    category:     Optional[str]                = None
    is_escalated: Optional[bool]               = None   # True → only escalated tickets
    sort_by:      str                           = "created_at"
    sort_dir:     str                           = "desc"
    page:         int                           = 1
    per_page:     int                           = 50


def parse_ticket_filters(
    search:       Optional[str]                = Query(None, description="Search ticket_number / title / description"),
    status:       Optional[List[TicketStatus]] = Query(None, description="One or more statuses"),
    priority:     Optional[List[Priority]]     = Query(None, description="One or more priorities"),
    severity:     Optional[List[Severity]]     = Query(None, description="One or more severities"),
    category:     Optional[str]                = Query(None, description="Issue id (int) or name keyword"),
    is_escalated: Optional[bool]               = Query(None, description="True → only escalated tickets"),
    sort_by:      str                           = Query("created_at", description="remaining_time | priority | severity | created_at"),
    sort_dir:     str                           = Query("desc",        description="asc | desc"),
    page:         int                           = Query(1,   ge=1),
    per_page:     int                           = Query(50,  ge=1, le=200),
) -> TicketFilterParams:
    return TicketFilterParams(
        search=search, status=status, priority=priority,
        severity=severity, category=category,
        is_escalated=is_escalated,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, per_page=per_page,
    )