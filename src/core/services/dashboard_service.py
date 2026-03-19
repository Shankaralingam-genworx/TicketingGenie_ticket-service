"""
Dashboard service — aggregated analytics metrics for the admin view.
File: src/core/services/dashboard_service.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from sqlalchemy import Float, func, cast, and_, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.ticket_model import Ticket
from src.schemas.dashboard_schema import (
    DashboardMetrics,
    PriorityBreakdown,
    ResponseTimeStat,
    SLABreachTrend,
)

_UTC            = timezone.utc
_TREND_DAYS     = 7   # SLA breach trend and response trend window
_SLA_WINDOW_DAYS = 30


def _today_utc() -> date:
    return datetime.now(_UTC).date()


def _days_ago(n: int) -> datetime:
    return datetime.now(_UTC) - timedelta(days=n)


class DashboardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Public entry point ────────────────────────────────────────────────

    async def get_metrics(self) -> DashboardMetrics:
        total          = await self._total_tickets()
        open_count     = await self._open_tickets()
        escalated      = await self._escalated_count()
        by_priority    = await self._priority_breakdown()
        resolved_today = await self._resolved_today()
        avg_resolution = await self._avg_resolution_mins()
        sla_compliance = await self._sla_compliance_rate()
        breach_trend   = await self._sla_breach_trend()
        response_trend = await self._response_time_trend()

        return DashboardMetrics(
            total_tickets=total,
            open_tickets=open_count,
            escalated_tickets=escalated,
            by_priority=by_priority,
            sla_breach_trend=breach_trend,
            response_time_trend=response_trend,
            resolved_today=resolved_today,
            avg_resolution_mins=avg_resolution,
            sla_compliance_rate=sla_compliance,
        )

    # ── Total tickets ─────────────────────────────────────────────────────

    async def _total_tickets(self) -> int:
        result = await self.db.execute(select(func.count(Ticket.id)))
        return result.scalar() or 0

    # ── Open tickets (open + in_progress) ────────────────────────────────

    async def _open_tickets(self) -> int:
        result = await self.db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.status.in_(["open", "in_progress"])
            )
        )
        return result.scalar() or 0

    # ── Priority breakdown (open + in_progress tickets only) ─────────────

    async def _priority_breakdown(self) -> PriorityBreakdown:
        priority_col = getattr(Ticket, "severity", None) or getattr(Ticket, "priority", None)
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
        today    = _today_utc()
        tomorrow = today + timedelta(days=1)
        result = await self.db.execute(
            select(func.count(Ticket.id)).where(
                and_(
                    Ticket.status.in_(["resolved", "closed"]),
                    Ticket.resolved_at >= datetime(today.year, today.month, today.day, tzinfo=_UTC),
                    Ticket.resolved_at <  datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=_UTC),
                )
            )
        )
        return result.scalar() or 0

    # ── Average resolution minutes (last 30 days) ─────────────────────────

    async def _avg_resolution_mins(self) -> int | None:
        diff_expr = func.extract("epoch", Ticket.resolved_at - Ticket.created_at) / 60.0
        result = await self.db.execute(
            select(func.avg(diff_expr)).where(
                and_(
                    Ticket.status.in_(["resolved", "closed"]),
                    Ticket.resolved_at.isnot(None),
                    Ticket.created_at >= _days_ago(_SLA_WINDOW_DAYS),
                )
            )
        )
        val = result.scalar()
        return int(val) if val is not None else None

    # ── SLA compliance rate (last 30 days) ────────────────────────────────

    async def _sla_compliance_rate(self) -> float | None:
        """
        % of resolved/closed tickets resolved before their resolution_due_at.
        Uses real Ticket.resolution_due_at and Ticket.resolved_at columns.
        """
        result = await self.db.execute(
            select(
                func.count(Ticket.id).label("total"),
                func.sum(
                    case(
                        (
                            and_(
                                Ticket.resolved_at.isnot(None),
                                Ticket.resolution_due_at.isnot(None),
                                Ticket.resolved_at <= Ticket.resolution_due_at,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("compliant"),
            ).where(
                and_(
                    Ticket.status.in_(["resolved", "closed"]),
                    Ticket.resolved_at.isnot(None),
                    Ticket.created_at >= _days_ago(_SLA_WINDOW_DAYS),
                )
            )
        )
        row = result.one()
        total, compliant = row.total or 0, row.compliant or 0
        return round(compliant / total * 100, 1) if total > 0 else None

    # ── SLA breach trend (last 7 days — real data) ────────────────────────

    async def _sla_breach_trend(self) -> list[SLABreachTrend]:
        """
        Per-day SLA breach count for the last _TREND_DAYS days.

        A ticket is counted as breached on its creation date when:
          - It has been resolved and resolved_at > resolution_due_at, OR
          - It is still open/in-progress and resolution_due_at < NOW()

        Both normal and escalated deadlines are checked:
          - Non-escalated: resolution_due_at
          - Escalated:     escalated_resolution_due_at (falls back to resolution_due_at)
        """
        now   = datetime.now(_UTC)
        since = _days_ago(_TREND_DAYS)

        # Effective resolution deadline per ticket
        effective_deadline = case(
            (
                and_(
                    Ticket.is_escalated.is_(True),
                    Ticket.escalated_resolution_due_at.isnot(None),
                ),
                Ticket.escalated_resolution_due_at,
            ),
            else_=Ticket.resolution_due_at,
        )

        # A breach is when the deadline was missed
        is_breached = case(
            # Resolved late
            (
                and_(
                    Ticket.resolved_at.isnot(None),
                    effective_deadline.isnot(None),
                    Ticket.resolved_at > effective_deadline,
                ),
                1,
            ),
            # Still open but deadline already passed
            (
                and_(
                    Ticket.resolved_at.is_(None),
                    effective_deadline.isnot(None),
                    effective_deadline < now,
                ),
                1,
            ),
            else_=0,
        )

        day_col = func.date_trunc("day", Ticket.created_at).label("day")

        result = await self.db.execute(
            select(
                day_col,
                func.count(Ticket.id).label("total"),
                func.sum(is_breached).label("breaches"),
            )
            .where(Ticket.created_at >= since)
            .group_by(day_col)
            .order_by(day_col)
        )

        rows = result.all()

        # Build a full 7-day series — fill in zeros for days with no tickets
        date_map: dict[date, tuple[int, int]] = {}
        for row in rows:
            d = row.day.date() if hasattr(row.day, "date") else row.day
            date_map[d] = (row.total or 0, row.breaches or 0)

        series: list[SLABreachTrend] = []
        for i in range(_TREND_DAYS - 1, -1, -1):
            d = (_today_utc() - timedelta(days=i))
            total, breaches = date_map.get(d, (0, 0))
            series.append(SLABreachTrend(date=d, total=total, breaches=breaches))

        return series

    # ── Response & resolution time trend (last 7 days) ────────────────────

    async def _response_time_trend(self) -> list[ResponseTimeStat]:
        since   = _days_ago(_TREND_DAYS)
        day_col = func.date_trunc("day", Ticket.created_at).label("day")

        response_mins   = func.extract("epoch", Ticket.first_response_at - Ticket.created_at) / 60.0
        resolution_mins = func.extract("epoch", Ticket.resolved_at - Ticket.created_at) / 60.0

        result = await self.db.execute(
            select(
                day_col,
                func.avg(response_mins).cast(Float).label("avg_first_response_mins"),
                func.percentile_cont(0.5)
                    .within_group(resolution_mins)
                    .cast(Float)
                    .label("median_resolution_mins"),
            )
            .where(
                and_(
                    Ticket.created_at >= since,
                    Ticket.first_response_at.isnot(None),
                )
            )
            .group_by(day_col)
            .order_by(day_col)
        )

        rows = result.all()

        date_map: dict[date, tuple[int, int]] = {}
        for row in rows:
            d = row.day.date() if hasattr(row.day, "date") else row.day
            date_map[d] = (
                int(row.avg_first_response_mins or 0),
                int(row.median_resolution_mins or 0),
            )

        series: list[ResponseTimeStat] = []
        for i in range(_TREND_DAYS - 1, -1, -1):
            d = (_today_utc() - timedelta(days=i))
            avg_resp, med_res = date_map.get(d, (0, 0))
            series.append(ResponseTimeStat(
                date=d,
                avg_first_response_mins=avg_resp,
                median_resolution_mins=med_res,
            ))

        return series