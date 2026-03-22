"""Keyword-based severity scoring agent."""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.sla_constants import Severity
from src.data.repositories.severity_keyword_repository import SeverityKeywordRepository
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="severity-agent")

_PRIORITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]


class SeverityAgent:
    """Scores ticket text against DB keywords to determine severity."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect(self, issue_name: str, title: str, description: str) -> Severity:
        """
        Load active keywords from DB and score the ticket text.
        Keywords are fetched live so admin changes take effect without restart.
        Title is double-weighted — it carries the strongest signal.
        """
        buckets = await SeverityKeywordRepository(self.db).get_all_active_as_dict()

        # Double title to give it 2× weight; normalise punctuation variants
        combined = re.sub(
            r"[_\-/\\]", " ",
            f"{issue_name} {title} {title} {description}".lower(),
        )

        scores: dict[Severity, float] = {s: 0.0 for s in Severity}

        for severity, kw_dict in buckets.items():
            for phrase, weight in kw_dict.items():
                if phrase in combined:
                    scores[severity] += weight

        best = _resolve(scores)
        logger.info("severity_detected", severity=best.value, scores=dict(scores))
        return best


def _resolve(scores: dict[Severity, float]) -> Severity:
    """Highest score wins; ties break by CRITICAL > HIGH > MEDIUM > LOW. Defaults to LOW."""
    if all(v == 0.0 for v in scores.values()):
        return Severity.LOW

    max_score = max(scores.values())
    for severity in _PRIORITY_ORDER:
        if scores[severity] == max_score:
            return severity

    return Severity.LOW