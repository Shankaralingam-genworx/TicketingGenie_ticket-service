"""
Generic query-parameter schema for filtered ticket listing.
File: src/schemas/ticket_filter_schema.py

All search / filter / sort is resolved by the backend.
The frontend passes these as plain query-string params.

Supported params
────────────────
  search    – ilike match on ticket_number, title, description
  status    – one or many TicketStatus  (?status=new&status=open)
  priority  – one or many Priority
  severity  – one or many Severity
  category  – issue_id (int string) OR keyword matched on issue.name
  sort_by   – remaining_time | priority | severity | created_at  (default: created_at)
  sort_dir  – asc | desc                                          (default: desc)
  page      – int ≥ 1                                             (default: 1)
  per_page  – 1–200                                               (default: 50)
"""

from typing import List, Optional
from fastapi import Query
from pydantic import BaseModel

from src.constants.ticket_constants import TicketStatus
from src.constants.priority_constants import Priority
from src.constants.sla_constants import Severity


class TicketFilterParams(BaseModel):
    search:   Optional[str]                = None
    status:   Optional[List[TicketStatus]] = None
    priority: Optional[List[Priority]]     = None
    severity: Optional[List[Severity]]     = None
    category: Optional[str]                = None
    sort_by:  str                           = "created_at"
    sort_dir: str                           = "desc"
    page:     int                           = 1
    per_page: int                           = 50


def parse_ticket_filters(
    search:   Optional[str]                = Query(None, description="Search ticket_number / title / description"),
    status:   Optional[List[TicketStatus]] = Query(None, description="One or more statuses"),
    priority: Optional[List[Priority]]     = Query(None, description="One or more priorities"),
    severity: Optional[List[Severity]]     = Query(None, description="One or more severities"),
    category: Optional[str]                = Query(None, description="Issue id (int) or name keyword"),
    sort_by:  str                           = Query("created_at", description="remaining_time | priority | severity | created_at"),
    sort_dir: str                           = Query("desc",        description="asc | desc"),
    page:     int                           = Query(1,   ge=1),
    per_page: int                           = Query(50,  ge=1, le=200),
) -> TicketFilterParams:
    return TicketFilterParams(
        search=search, status=status, priority=priority,
        severity=severity, category=category,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, per_page=per_page,
    )