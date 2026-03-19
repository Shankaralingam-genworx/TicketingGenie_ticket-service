"""Repository for SeverityKeyword — DB-managed keyword table."""

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import Severity
from src.data.models.postgres.severity_keyword_model import SeverityKeyword


class SeverityKeywordRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self, active_only: bool = False) -> list[SeverityKeyword]:
        q = select(SeverityKeyword)
        if active_only:
            q = q.where(SeverityKeyword.is_active.is_(True))
        q = q.order_by(SeverityKeyword.severity, SeverityKeyword.keyword)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def get_by_id(self, kw_id: int) -> SeverityKeyword | None:
        result = await self.db.execute(
            select(SeverityKeyword).where(SeverityKeyword.id == kw_id)
        )
        return result.scalar_one_or_none()

    async def get_by_keyword(self, keyword: str) -> SeverityKeyword | None:
        result = await self.db.execute(
            select(SeverityKeyword).where(SeverityKeyword.keyword == keyword.lower().strip())
        )
        return result.scalar_one_or_none()

    async def get_active_by_severity(self, severity: Severity) -> list[SeverityKeyword]:
        result = await self.db.execute(
            select(SeverityKeyword).where(
                SeverityKeyword.severity == severity,
                SeverityKeyword.is_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def get_all_active_as_dict(self) -> dict[Severity, dict[str, float]]:
        """
        Returns all active keywords grouped by severity, ready for scoring.
        Shape: {Severity.CRITICAL: {"outage": 3.0, ...}, ...}
        """
        rows = await self.get_all(active_only=True)
        buckets: dict[Severity, dict[str, float]] = {s: {} for s in Severity}
        for row in rows:
            buckets[row.severity][row.keyword] = row.weight
        return buckets

    async def create(
        self, keyword: str, severity: Severity, weight: float = 1.0
    ) -> SeverityKeyword:
        kw = SeverityKeyword(
            keyword=keyword.lower().strip(),
            severity=severity,
            weight=weight,
        )
        self.db.add(kw)
        await self.db.flush()
        await self.db.refresh(kw)
        return kw

    async def update(
        self,
        kw: SeverityKeyword,
        keyword: str | None = None,
        severity: Severity | None = None,
        weight: float | None = None,
        is_active: bool | None = None,
    ) -> SeverityKeyword:
        if keyword is not None:
            kw.keyword = keyword.lower().strip()
        if severity is not None:
            kw.severity = severity
        if weight is not None:
            kw.weight = weight
        if is_active is not None:
            kw.is_active = is_active
        await self.db.flush()
        await self.db.refresh(kw)
        return kw

    async def delete(self, kw: SeverityKeyword) -> None:
        await self.db.delete(kw)
        await self.db.flush()

    async def bulk_create(
        self, entries: list[dict]
    ) -> int:
        """
        Seed helper — insert many keywords, skip duplicates.
        Each entry: {"keyword": str, "severity": Severity, "weight": float}
        Returns count of rows actually inserted.
        """
        inserted = 0
        for entry in entries:
            normalized = entry["keyword"].lower().strip()
            exists = await self.get_by_keyword(normalized)
            if not exists:
                self.db.add(SeverityKeyword(
                    keyword=normalized,
                    severity=entry["severity"],
                    weight=entry.get("weight", 1.0),
                ))
                inserted += 1
        if inserted:
            await self.db.flush()
        return inserted
