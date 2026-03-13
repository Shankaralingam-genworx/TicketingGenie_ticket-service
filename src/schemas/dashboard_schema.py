"""
Dashboard schemas — admin analytics metrics.
File: src/schemas/dashboard_schema.py
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class TicketStatusBreakdown(BaseModel):
    new: int = 0
    acknowledged: int = 0
    open: int = 0
    in_progress: int = 0
    on_hold: int = 0
    resolved: int = 0
    closed: int = 0
    reopened: int = 0


class PriorityBreakdown(BaseModel):
    """Open + in-progress tickets grouped by priority/severity."""
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class SLABreachTrend(BaseModel):
    """Daily SLA breach counts for the last N days."""
    date: date
    breaches: int = Field(..., description="Tickets that breached SLA on this date")
    total: int = Field(..., description="Total tickets created/active on this date")


class ResponseTimeStat(BaseModel):
    """Daily first-response and resolution time averages."""
    date: date
    avg_first_response_mins: int = Field(
        ..., description="Average minutes from ticket creation to first agent response"
    )
    median_resolution_mins: int = Field(
        ..., description="Median minutes from ticket creation to resolution"
    )


class DashboardMetrics(BaseModel):
    # Core counts
    total_tickets: int
    by_status: TicketStatusBreakdown
    open_tickets: int
    escalated_tickets: int

    # Priority breakdown (open tickets only)
    by_priority: Optional[PriorityBreakdown] = None

    # Trend data (last 14 days)
    sla_breach_trend: Optional[list[SLABreachTrend]] = None
    response_time_trend: Optional[list[ResponseTimeStat]] = None

    # Aggregate KPIs
    resolved_today: Optional[int] = None
    avg_resolution_mins: Optional[int] = None
    sla_compliance_rate: Optional[float] = Field(
        default=None,
        description="Percentage of tickets resolved within SLA in last 30 days",
    )