"""
Dashboard service — aggregated analytics metrics for the admin view.
File: src/core/services/dashboard_service.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from sqlalchemy import Float, select, func, cast, Date
from sqlalchemy import and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.ticket_model import Ticket
from src.schemas.dashboard_schema import (
    DashboardMetrics,
    PriorityBreakdown,
    ResponseTimeStat,
    SLABreachTrend,
    TicketStatusBreakdown,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_TREND_DAYS = 14
_SLA_WINDOW_DAYS = 30


def _today_utc() -> date:
    return datetime.now(_UTC).date()


def _days_ago(n: int) -> datetime:
    return datetime.now(_UTC) - timedelta(days=n)


# ---------------------------------------------------------------------------
# DashboardService
# ---------------------------------------------------------------------------

class DashboardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Public entry point ────────────────────────────────────────────────

    async def get_metrics(self) -> DashboardMetrics:
        by_status = await self._status_breakdown()
        by_priority = await self._priority_breakdown()
        escalated = await self._escalated_count()
        resolved_today = await self._resolved_today()
        avg_resolution = await self._avg_resolution_mins()
        sla_compliance = await self._sla_compliance_rate()
        breach_trend = await self._sla_breach_trend()
        response_trend = await self._response_time_trend()

        total = sum(
            [
                by_status.new,
                by_status.acknowledged,
                by_status.open,
                by_status.in_progress,
                by_status.on_hold,
                by_status.resolved,
                by_status.closed,
                by_status.reopened,
            ]
        )
        open_count = by_status.open + by_status.in_progress

        return DashboardMetrics(
            total_tickets=total,
            by_status=by_status,
            open_tickets=open_count,
            escalated_tickets=escalated,
            by_priority=by_priority,
            sla_breach_trend=breach_trend,
            response_time_trend=response_trend,
            resolved_today=resolved_today,
            avg_resolution_mins=avg_resolution,
            sla_compliance_rate=sla_compliance,
        )

    # ── Status breakdown ──────────────────────────────────────────────────

    async def _status_breakdown(self) -> TicketStatusBreakdown:
        result = await self.db.execute(
            select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)
        )
        raw: dict[str, int] = {row[0].value: row[1] for row in result.all()}
        return TicketStatusBreakdown(
            new=raw.get("new", 0),
            acknowledged=raw.get("acknowledged", 0),
            open=raw.get("open", 0),
            in_progress=raw.get("in_progress", 0),
            on_hold=raw.get("on_hold", 0),
            resolved=raw.get("resolved", 0),
            closed=raw.get("closed", 0),
            reopened=raw.get("reopened", 0),
        )

    # ── Priority breakdown (open tickets only) ────────────────────────────

    async def _priority_breakdown(self) -> PriorityBreakdown:
        """
        Count open + in_progress tickets grouped by severity/priority.
        Adjust the column name (Ticket.severity / Ticket.priority) to match
        your actual model.
        """
        # Guard: if your model uses a different column skip gracefully
        priority_col = getattr(Ticket, "severity", None) or getattr(
            Ticket, "priority", None
        )
        if priority_col is None:
            return PriorityBreakdown()

        result = await self.db.execute(
            select(priority_col, func.count(Ticket.id))
            .where(Ticket.status.in_(["open", "in_progress"]))
            .group_by(priority_col)
        )
        raw: dict[str, int] = {}
        for row in result.all():
            key = row[0].value if hasattr(row[0], "value") else str(row[0])
            raw[key] = row[1]

        return PriorityBreakdown(
            critical=raw.get("critical", 0),
            high=raw.get("high", 0),
            medium=raw.get("medium", 0),
            low=raw.get("low", 0),
        )

    # ── Escalated count ───────────────────────────────────────────────────

    async def _escalated_count(self) -> int:
        result = await self.db.execute(
            select(func.count(Ticket.id)).where(Ticket.is_escalated.is_(True))
        )
        return result.scalar() or 0

    # ── Resolved today ────────────────────────────────────────────────────

    async def _resolved_today(self) -> int:
        today = _today_utc()
        tomorrow = today + timedelta(days=1)

        resolved_at_col = getattr(Ticket, "resolved_at", None)

        if resolved_at_col is not None:
            # Use resolved_at timestamp when available
            result = await self.db.execute(
                select(func.count(Ticket.id)).where(
                    and_(
                        Ticket.status.in_(["resolved", "closed"]),
                        resolved_at_col >= datetime(today.year, today.month, today.day, tzinfo=_UTC),
                        resolved_at_col < datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=_UTC),
                    )
                )
            )
        else:
            # Fall back to updated_at
            result = await self.db.execute(
                select(func.count(Ticket.id)).where(
                    and_(
                        Ticket.status.in_(["resolved", "closed"]),
                        Ticket.updated_at >= datetime(today.year, today.month, today.day, tzinfo=_UTC),
                        Ticket.updated_at < datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=_UTC),
                    )
                )
            )
        return result.scalar() or 0

    # ── Average resolution minutes ────────────────────────────────────────

    async def _avg_resolution_mins(self) -> int | None:
        resolved_at_col = getattr(Ticket, "resolved_at", None)
        if resolved_at_col is None:
            return None

        # Average(resolved_at - created_at) in minutes
        diff_expr = func.extract(
            "epoch", resolved_at_col - Ticket.created_at
        ) / 60.0

        result = await self.db.execute(
            select(func.avg(diff_expr)).where(
                and_(
                    Ticket.status.in_(["resolved", "closed"]),
                    resolved_at_col.isnot(None),
                    Ticket.created_at >= _days_ago(_SLA_WINDOW_DAYS),
                )
            )
        )
        val = result.scalar()
        return int(val) if val is not None else None

    # ── SLA compliance rate (last 30 days) ────────────────────────────────

    async def _sla_compliance_rate(self) -> float | None:
        """
        Percentage of resolved tickets that were resolved within their SLA
        resolution_time_mins target.

        Requires: Ticket.resolved_at and Ticket.sla_resolution_target_mins
        (or similar).  Returns None if those columns don't exist.
        """
        resolved_at_col = getattr(Ticket, "resolved_at", None)
        sla_target_col = getattr(Ticket, "sla_resolution_target_mins", None)

        if resolved_at_col is None or sla_target_col is None:
            # Fallback: compute from is_sla_breached flag if it exists
            breached_col = getattr(Ticket, "is_sla_breached", None)
            if breached_col is None:
                return None

            result = await self.db.execute(
                select(
                    func.count(Ticket.id).label("total"),
                    func.sum(
                        case((breached_col.is_(False), 1), else_=0)
                    ).label("compliant"),
                ).where(
                    and_(
                        Ticket.status.in_(["resolved", "closed"]),
                        Ticket.created_at >= _days_ago(_SLA_WINDOW_DAYS),
                    )
                )
            )
            row = result.one()
            total, compliant = row.total or 0, row.compliant or 0
            return round(compliant / total * 100, 1) if total > 0 else None

        diff_mins = (
            func.extract("epoch", resolved_at_col - Ticket.created_at) / 60.0
        )
        result = await self.db.execute(
            select(
                func.count(Ticket.id).label("total"),
                func.sum(
                    case((diff_mins <= sla_target_col, 1), else_=0)
                ).label("compliant"),
            ).where(
                and_(
                    Ticket.status.in_(["resolved", "closed"]),
                    resolved_at_col.isnot(None),
                    Ticket.created_at >= _days_ago(_SLA_WINDOW_DAYS),
                )
            )
        )
        row = result.one()
        total, compliant = row.total or 0, row.compliant or 0
        return round(compliant / total * 100, 1) if total > 0 else None

    # ── SLA breach trend (last 14 days) ───────────────────────────────────

    async def _sla_breach_trend(self) -> list[SLABreachTrend]:
        """
        Per-day count of SLA breaches for the last TREND_DAYS days.
        Uses Ticket.is_sla_breached or Ticket.sla_breached_at column.
        """
        date_trunc = func.date_trunc("day", Ticket.created_at)
        since = _days_ago(_TREND_DAYS)

        breached_col = getattr(Ticket, "is_sla_breached", None)
        if breached_col is None:                                                  
            # Return empty — frontend will show sample data
            return []

        result = await self.db.execute(
            select(
                cast(date_trunc, type_=type(date_trunc)).label("day"),
                func.count(Ticket.id).label("total"),
                func.sum(case((breached_col.is_(True), 1), else_=0)).label("breaches"),
            )
            .where(Ticket.created_at >= since)
            .group_by("day")
            .order_by("day")
        )

        rows = result.all()
        return [
            SLABreachTrend(
                date=row.day.date() if hasattr(row.day, "date") else row.day,
                total=row.total or 0,
                breaches=row.breaches or 0,
            )
            for row in rows
        ]

    
    async def _response_time_trend(self) -> list[ResponseTimeStat]:

        first_responded_col = getattr(Ticket, "first_responded_at", None)
        resolved_at_col = getattr(Ticket, "resolved_at", None)

        if first_responded_col is None and resolved_at_col is None:
            return []

        since = _days_ago(_TREND_DAYS)
        day_col = func.date_trunc("day", Ticket.created_at).label("day")

        selects = [day_col]

        # --- First response ---
        if first_responded_col is not None:
            response_mins = (
                func.extract("epoch", first_responded_col - Ticket.created_at) / 60.0
            )
            selects.append(
                func.avg(response_mins)
                .cast(Float)
                .label("avg_first_response_mins")
            )
        else:
            selects.append(
                func.cast(0, Float).label("avg_first_response_mins")
            )

        # --- Resolution ---
        if resolved_at_col is not None:
            resolution_mins = (
                func.extract("epoch", resolved_at_col - Ticket.created_at) / 60.0
            )
            selects.append(
                func.percentile_cont(0.5)
                .within_group(resolution_mins)
                .cast(Float)
                .label("median_resolution_mins")
            )
        else:
            selects.append(
                func.cast(0, Float).label("median_resolution_mins")
            )

        result = await self.db.execute(
            select(*selects)
            .where(Ticket.created_at >= since)
            .group_by(day_col)
            .order_by(day_col)
        )

        rows = result.all()

        return [
            ResponseTimeStat(
                date=row.day.date() if hasattr(row.day, "date") else row.day,
                avg_first_response_mins=int(row.avg_first_response_mins or 0),
                median_resolution_mins=int(row.median_resolution_mins or 0),
            )
            for row in rows
        ]