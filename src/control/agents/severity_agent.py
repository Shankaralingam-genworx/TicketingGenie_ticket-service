"""
Deterministic severity detection agent using keyword heuristics and
weighted scoring rules — no LLM or external API calls required.

Scoring logic:
  - Each matched keyword contributes a weight to its severity bucket.
  - The bucket with the highest total score wins.
  - Tie-breaking favours higher severity (critical > high > medium > low).
  - Falls back to LOW if nothing matches.
"""

import logging
import re

from src.constants.sla_constants import Severity

logger = logging.getLogger("ticket.severity_agent")


# ---------------------------------------------------------------------------
# Keyword tables  (word / phrase  →  weight)
# Higher weight = stronger signal for that severity level.
# ---------------------------------------------------------------------------

_CRITICAL_KW: dict[str, float] = {
    # Outage / availability
    "outage": 3.0,
    "complete outage": 3.5,
    "total outage": 3.5,
    "system down": 3.5,
    "service down": 3.5,
    "site down": 3.5,
    "platform down": 3.5,
    "fully down": 3.0,
    "not accessible": 2.5,
    "inaccessible": 2.5,
    "unavailable": 2.5,
    "completely unavailable": 3.0,
    "production down": 3.5,
    "entire system": 2.5,
    "all users affected": 3.0,
    "100% failure": 3.5,
    # Data
    "data loss": 3.5,
    "data lost": 3.5,
    "data corruption": 3.5,
    "corrupt data": 3.0,
    "corrupted": 2.5,
    "database down": 3.0,
    "database unavailable": 3.0,
    "records deleted": 3.0,
    "data deleted": 3.0,
    "data missing": 2.5,
    "backup failure": 2.5,
    # Security
    "security breach": 3.5,
    "breach": 2.5,
    "hacked": 3.0,
    "hack": 2.5,
    "ransomware": 3.5,
    "malware": 3.0,
    "virus": 2.5,
    "unauthorized access": 3.0,
    "account takeover": 3.0,
    "credentials exposed": 3.0,
    "data exposed": 3.0,
    "pii exposed": 3.5,
    "personal data exposed": 3.5,
    "gdpr breach": 3.5,
    "compliance breach": 3.0,
    "ddos": 3.0,
    "intrusion": 3.0,
    # Infrastructure crash
    "crash": 2.5,
    "crashed": 2.5,
    "server crash": 3.0,
    "kernel panic": 3.0,
    "fatal error": 3.0,
    "critical failure": 3.5,
    "system failure": 3.0,
    "infrastructure failure": 3.0,
    "disk full": 2.5,
    "out of disk": 2.5,
    "out of memory": 2.5,
    "oom": 2.5,
    "memory leak critical": 3.0,
    "cpu 100": 2.5,
    "cpu maxed": 2.5,
    "network down": 3.0,
    "api down": 3.0,
    "payment down": 3.5,
    "checkout down": 3.5,
    "login down": 3.0,
    "authentication down": 3.0,
    "sso down": 3.0,
    # Urgency language
    "emergency": 3.0,
    "critical": 2.0,
    "catastrophic": 3.5,
    "severe": 2.0,
    "immediately": 1.5,
    "urgent": 1.5,
    "asap": 1.5,
    "sla breach": 3.0,
    "revenue loss": 3.0,
    "business critical": 3.0,
}

_HIGH_KW: dict[str, float] = {
    # Feature broken
    "not working": 2.0,
    "broken": 2.0,
    "stopped working": 2.5,
    "no longer working": 2.5,
    "doesn't work": 2.0,
    "does not work": 2.0,
    "won't work": 2.0,
    "fails to": 2.0,
    "failing": 2.0,
    "failed": 1.5,
    "failure": 1.5,
    "malfunction": 2.0,
    "malfunctioning": 2.0,
    "not functioning": 2.0,
    "non functional": 2.5,
    # Errors
    "error": 1.5,
    "errors": 1.5,
    "500 error": 2.5,
    "500": 1.5,
    "503": 2.0,
    "502": 2.0,
    "timeout": 1.5,
    "timed out": 1.5,
    "exception": 1.5,
    "stack trace": 2.0,
    "unhandled exception": 2.0,
    "null pointer": 2.0,
    "segfault": 2.5,
    "connection refused": 2.0,
    "connection failed": 2.0,
    "unable to connect": 2.0,
    "cannot connect": 2.0,
    # Access / auth
    "cannot login": 2.0,
    "can't login": 2.0,
    "unable to login": 2.0,
    "login failed": 2.0,
    "login error": 2.0,
    "cannot sign in": 2.0,
    "unable to sign in": 2.0,
    "password reset broken": 2.5,
    "locked out": 2.5,
    "account locked": 2.5,
    "cannot access": 2.0,
    "unable to access": 2.0,
    "access denied": 1.5,
    "permission denied": 1.5,
    "unauthorised": 1.5,
    "unauthorized": 1.5,
    # Workflow blockers
    "blocking": 2.0,
    "blocked": 2.0,
    "cannot submit": 2.0,
    "unable to submit": 2.0,
    "cannot save": 2.0,
    "unable to save": 2.0,
    "cannot upload": 2.0,
    "upload fails": 2.0,
    "import fails": 2.0,
    "export fails": 2.0,
    "sync broken": 2.0,
    "sync failing": 2.0,
    "integration broken": 2.0,
    "api error": 2.0,
    "api failing": 2.0,
    "data not loading": 2.0,
    "page not loading": 2.0,
    "dashboard broken": 2.0,
    "report broken": 2.0,
    # Impact language
    "significant impact": 2.0,
    "major issue": 2.0,
    "major bug": 2.0,
    "high priority": 1.5,
    "urgent": 1.0,
    "multiple users": 1.5,
    "all users": 1.5,
    "team affected": 1.5,
    "affecting team": 1.5,
    "affecting multiple": 1.5,
    "regression": 2.0,
    "broke after update": 2.5,
    "broke after deploy": 2.5,
    "broke after upgrade": 2.5,
}

_MEDIUM_KW: dict[str, float] = {
    # Performance
    "slow": 1.5,
    "slowly": 1.5,
    "sluggish": 1.5,
    "lag": 1.5,
    "laggy": 1.5,
    "latency": 1.5,
    "high latency": 2.0,
    "delay": 1.5,
    "delayed": 1.5,
    "taking too long": 1.5,
    "takes long": 1.5,
    "loading slow": 1.5,
    "response slow": 1.5,
    "performance issue": 2.0,
    "performance degraded": 2.0,
    "degraded": 1.5,
    "degradation": 1.5,
    "throughput": 1.0,
    "memory usage high": 1.5,
    "cpu usage high": 1.5,
    # Intermittent
    "intermittent": 2.0,
    "intermittently": 2.0,
    "sometimes": 1.0,
    "occasionally": 1.0,
    "random": 1.0,
    "randomly": 1.0,
    "flaky": 1.5,
    "inconsistent": 1.5,
    "sporadic": 1.5,
    "on and off": 1.5,
    "now and then": 1.0,
    "every so often": 1.0,
    # Partial issues
    "partial": 1.5,
    "partially": 1.5,
    "partial outage": 2.0,
    "some users": 1.5,
    "certain users": 1.5,
    "workaround": 2.0,
    "workaround available": 2.0,
    "workaround exists": 2.0,
    "can work around": 1.5,
    "limited functionality": 1.5,
    "reduced functionality": 1.5,
    "partially working": 1.5,
    "mostly working": 1.0,
    # UI / display issues with functional impact
    "incorrect data": 1.5,
    "wrong data": 1.5,
    "inaccurate": 1.5,
    "missing data": 1.5,
    "data not showing": 1.5,
    "not displaying": 1.5,
    "not rendering": 1.5,
    "blank screen": 1.5,
    "freezes": 1.5,
    "hangs": 1.5,
    "unresponsive": 1.5,
    "stale data": 1.0,
    "cache issue": 1.0,
    "sync delay": 1.5,
    # Warnings / non-critical errors
    "warning": 1.0,
    "404": 1.0,
    "not found": 1.0,
    "unexpected behaviour": 1.5,
    "unexpected behavior": 1.5,
    "side effect": 1.0,
    "minor error": 1.0,
}

_LOW_KW: dict[str, float] = {
    # Feature requests
    "feature request": 2.0,
    "feature suggestion": 2.0,
    "enhancement": 1.5,
    "enhancement request": 2.0,
    "suggestion": 1.5,
    "improvement": 1.5,
    "nice to have": 2.0,
    "wish list": 1.5,
    "would be great": 1.5,
    "could you add": 1.5,
    "please add": 1.5,
    "can you add": 1.5,
    "new feature": 1.5,
    # General questions / info
    "question": 1.5,
    "how do i": 1.5,
    "how to": 1.0,
    "how can i": 1.5,
    "where is": 1.0,
    "what is": 1.0,
    "wondering": 1.0,
    "curious": 1.0,
    "help me understand": 1.5,
    "documentation": 1.0,
    "docs": 1.0,
    "tutorial": 1.0,
    "training": 1.0,
    "onboarding": 1.0,
    "best practice": 1.0,
    "best practices": 1.0,
    "information": 1.0,
    "informational": 1.5,
    "inquiry": 1.5,
    "general question": 2.0,
    # Cosmetic / trivial
    "typo": 2.0,
    "spelling": 1.5,
    "cosmetic": 2.0,
    "aesthetic": 1.5,
    "ui tweak": 2.0,
    "colour": 1.0,
    "color": 1.0,
    "font": 1.0,
    "alignment": 1.0,
    "spacing": 1.0,
    "minor": 1.5,
    "small issue": 1.5,
    "trivial": 2.0,
    "low priority": 2.0,
    # Non-urgent language
    "whenever possible": 1.5,
    "no rush": 2.0,
    "not urgent": 2.0,
    "when you get a chance": 2.0,
    "at your convenience": 2.0,
    "low impact": 2.0,
    "minimal impact": 1.5,
    "no impact": 2.0,
}

# Map severity level → (keyword dict, Severity enum)
_SEVERITY_BUCKETS: list[tuple[dict[str, float], Severity]] = [
    (_CRITICAL_KW, Severity.CRITICAL),
    (_HIGH_KW,     Severity.HIGH),
    (_MEDIUM_KW,   Severity.MEDIUM),
    (_LOW_KW,      Severity.LOW),
]


class SeverityAgent:
    """
    Determines ticket severity using weighted keyword scoring.

    Scoring:
      - The combined title + description text is searched for each keyword.
      - Matched weights are summed per severity bucket.
      - The bucket with the highest score wins; ties break toward higher severity.
      - Returns LOW when nothing matches.

    Usage (sync or async — no I/O involved):
        agent = SeverityAgent()
        severity = await agent.detect(issue_name, title, description)
        # or synchronously:
        severity = agent.detect_sync(issue_name, title, description)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect(
        self, issue_name: str, title: str, description: str
    ) -> Severity:
        """Async wrapper around the synchronous scoring logic."""
        return self.detect_sync(issue_name, title, description)

    def detect_sync(
        self, issue_name: str, title: str, description: str
    ) -> Severity:
        """
        Classify severity from issue_name, title, and description.
        Title is double-weighted as it carries a stronger signal.
        """
        # Title gets 2× weight as it is the most concentrated signal.
        combined = f"{issue_name} {title} {title} {description}".lower()
        # Normalise punctuation so "not-working" == "not working"
        combined = re.sub(r"[_\-/\\]", " ", combined)

        scores: dict[Severity, float] = {s: 0.0 for _, s in _SEVERITY_BUCKETS}

        for kw_dict, severity in _SEVERITY_BUCKETS:
            for phrase, weight in kw_dict.items():
                if phrase in combined:
                    scores[severity] += weight

        best_severity = self._resolve(scores)
        logger.info(
            f"Keyword severity='{best_severity.value}' | "
            f"scores={dict(scores)} | title={title!r}"
        )
        return best_severity

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(scores: dict[Severity, float]) -> Severity:
        """
        Return the severity with the highest score.
        Ties are broken by priority order: CRITICAL > HIGH > MEDIUM > LOW.
        Falls back to LOW when all scores are zero.
        """
        priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]

        if all(v == 0.0 for v in scores.values()):
            return Severity.LOW

        max_score = max(scores.values())
        # Among all buckets that share the max score, pick the most severe.
        for severity in priority_order:
            if scores[severity] == max_score:
                return severity

        return Severity.LOW  # unreachable, but satisfies type checkers