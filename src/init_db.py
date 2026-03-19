import asyncio
from datetime import datetime, timezone
from sqlalchemy import select

from src.data.clients.postgres_client import AsyncSessionLocal
from src.data.models.postgres.sla_model import SLA
from src.data.models.postgres.issue_model import Issue
from src.constants.sla_constants import Severity
from src.constants.issue_constants import IssueCategory
from src.data.models.postgres.severity_keyword_model import SeverityKeyword



_SEED_KEYWORDS: list[tuple[str, Severity, float]] = [
    # CRITICAL
    ("outage", Severity.CRITICAL, 3.0),
    ("complete outage", Severity.CRITICAL, 3.5),
    ("total outage", Severity.CRITICAL, 3.5),
    ("system down", Severity.CRITICAL, 3.5),
    ("service down", Severity.CRITICAL, 3.5),
    ("site down", Severity.CRITICAL, 3.5),
    ("platform down", Severity.CRITICAL, 3.5),
    ("fully down", Severity.CRITICAL, 3.0),
    ("not accessible", Severity.CRITICAL, 2.5),
    ("inaccessible", Severity.CRITICAL, 2.5),
    ("unavailable", Severity.CRITICAL, 2.5),
    ("completely unavailable", Severity.CRITICAL, 3.0),
    ("production down", Severity.CRITICAL, 3.5),
    ("entire system", Severity.CRITICAL, 2.5),
    ("all users affected", Severity.CRITICAL, 3.0),
    ("100% failure", Severity.CRITICAL, 3.5),
    ("data loss", Severity.CRITICAL, 3.5),
    ("data lost", Severity.CRITICAL, 3.5),
    ("data corruption", Severity.CRITICAL, 3.5),
    ("corrupt data", Severity.CRITICAL, 3.0),
    ("corrupted", Severity.CRITICAL, 2.5),
    ("database down", Severity.CRITICAL, 3.0),
    ("database unavailable", Severity.CRITICAL, 3.0),
    ("records deleted", Severity.CRITICAL, 3.0),
    ("data deleted", Severity.CRITICAL, 3.0),
    ("data missing", Severity.CRITICAL, 2.5),
    ("backup failure", Severity.CRITICAL, 2.5),
    ("security breach", Severity.CRITICAL, 3.5),
    ("breach", Severity.CRITICAL, 2.5),
    ("hacked", Severity.CRITICAL, 3.0),
    ("hack", Severity.CRITICAL, 2.5),
    ("ransomware", Severity.CRITICAL, 3.5),
    ("malware", Severity.CRITICAL, 3.0),
    ("virus", Severity.CRITICAL, 2.5),
    ("unauthorized access", Severity.CRITICAL, 3.0),
    ("account takeover", Severity.CRITICAL, 3.0),
    ("credentials exposed", Severity.CRITICAL, 3.0),
    ("data exposed", Severity.CRITICAL, 3.0),
    ("pii exposed", Severity.CRITICAL, 3.5),
    ("personal data exposed", Severity.CRITICAL, 3.5),
    ("gdpr breach", Severity.CRITICAL, 3.5),
    ("compliance breach", Severity.CRITICAL, 3.0),
    ("ddos", Severity.CRITICAL, 3.0),
    ("intrusion", Severity.CRITICAL, 3.0),
    ("crash", Severity.CRITICAL, 2.5),
    ("crashed", Severity.CRITICAL, 2.5),
    ("server crash", Severity.CRITICAL, 3.0),
    ("kernel panic", Severity.CRITICAL, 3.0),
    ("fatal error", Severity.CRITICAL, 3.0),
    ("critical failure", Severity.CRITICAL, 3.5),
    ("system failure", Severity.CRITICAL, 3.0),
    ("infrastructure failure", Severity.CRITICAL, 3.0),
    ("disk full", Severity.CRITICAL, 2.5),
    ("out of disk", Severity.CRITICAL, 2.5),
    ("out of memory", Severity.CRITICAL, 2.5),
    ("oom", Severity.CRITICAL, 2.5),
    ("memory leak critical", Severity.CRITICAL, 3.0),
    ("cpu 100", Severity.CRITICAL, 2.5),
    ("cpu maxed", Severity.CRITICAL, 2.5),
    ("network down", Severity.CRITICAL, 3.0),
    ("api down", Severity.CRITICAL, 3.0),
    ("payment down", Severity.CRITICAL, 3.5),
    ("checkout down", Severity.CRITICAL, 3.5),
    ("login down", Severity.CRITICAL, 3.0),
    ("authentication down", Severity.CRITICAL, 3.0),
    ("sso down", Severity.CRITICAL, 3.0),
    ("emergency", Severity.CRITICAL, 3.0),
    ("critical", Severity.CRITICAL, 2.0),
    ("catastrophic", Severity.CRITICAL, 3.5),
    ("severe", Severity.CRITICAL, 2.0),
    ("immediately", Severity.CRITICAL, 1.5),
    ("urgent", Severity.CRITICAL, 1.5),
    ("asap", Severity.CRITICAL, 1.5),
    ("sla breach", Severity.CRITICAL, 3.0),
    ("revenue loss", Severity.CRITICAL, 3.0),
    ("business critical", Severity.CRITICAL, 3.0),
    # HIGH
    ("not working", Severity.HIGH, 2.0),
    ("broken", Severity.HIGH, 2.0),
    ("stopped working", Severity.HIGH, 2.5),
    ("no longer working", Severity.HIGH, 2.5),
    ("doesn't work", Severity.HIGH, 2.0),
    ("does not work", Severity.HIGH, 2.0),
    ("won't work", Severity.HIGH, 2.0),
    ("fails to", Severity.HIGH, 2.0),
    ("failing", Severity.HIGH, 2.0),
    ("failed", Severity.HIGH, 1.5),
    ("failure", Severity.HIGH, 1.5),
    ("malfunction", Severity.HIGH, 2.0),
    ("malfunctioning", Severity.HIGH, 2.0),
    ("not functioning", Severity.HIGH, 2.0),
    ("non functional", Severity.HIGH, 2.5),
    ("error", Severity.HIGH, 1.5),
    ("errors", Severity.HIGH, 1.5),
    ("500 error", Severity.HIGH, 2.5),
    ("500", Severity.HIGH, 1.5),
    ("503", Severity.HIGH, 2.0),
    ("502", Severity.HIGH, 2.0),
    ("timeout", Severity.HIGH, 1.5),
    ("timed out", Severity.HIGH, 1.5),
    ("exception", Severity.HIGH, 1.5),
    ("stack trace", Severity.HIGH, 2.0),
    ("unhandled exception", Severity.HIGH, 2.0),
    ("null pointer", Severity.HIGH, 2.0),
    ("segfault", Severity.HIGH, 2.5),
    ("connection refused", Severity.HIGH, 2.0),
    ("connection failed", Severity.HIGH, 2.0),
    ("unable to connect", Severity.HIGH, 2.0),
    ("cannot connect", Severity.HIGH, 2.0),
    ("cannot login", Severity.HIGH, 2.0),
    ("can't login", Severity.HIGH, 2.0),
    ("unable to login", Severity.HIGH, 2.0),
    ("login failed", Severity.HIGH, 2.0),
    ("login error", Severity.HIGH, 2.0),
    ("cannot sign in", Severity.HIGH, 2.0),
    ("unable to sign in", Severity.HIGH, 2.0),
    ("password reset broken", Severity.HIGH, 2.5),
    ("locked out", Severity.HIGH, 2.5),
    ("account locked", Severity.HIGH, 2.5),
    ("cannot access", Severity.HIGH, 2.0),
    ("unable to access", Severity.HIGH, 2.0),
    ("access denied", Severity.HIGH, 1.5),
    ("permission denied", Severity.HIGH, 1.5),
    ("unauthorised", Severity.HIGH, 1.5),
    ("unauthorized", Severity.HIGH, 1.5),
    ("blocking", Severity.HIGH, 2.0),
    ("blocked", Severity.HIGH, 2.0),
    ("cannot submit", Severity.HIGH, 2.0),
    ("unable to submit", Severity.HIGH, 2.0),
    ("cannot save", Severity.HIGH, 2.0),
    ("unable to save", Severity.HIGH, 2.0),
    ("cannot upload", Severity.HIGH, 2.0),
    ("upload fails", Severity.HIGH, 2.0),
    ("import fails", Severity.HIGH, 2.0),
    ("export fails", Severity.HIGH, 2.0),
    ("sync broken", Severity.HIGH, 2.0),
    ("sync failing", Severity.HIGH, 2.0),
    ("integration broken", Severity.HIGH, 2.0),
    ("api error", Severity.HIGH, 2.0),
    ("api failing", Severity.HIGH, 2.0),
    ("data not loading", Severity.HIGH, 2.0),
    ("page not loading", Severity.HIGH, 2.0),
    ("dashboard broken", Severity.HIGH, 2.0),
    ("report broken", Severity.HIGH, 2.0),
    ("significant impact", Severity.HIGH, 2.0),
    ("major issue", Severity.HIGH, 2.0),
    ("major bug", Severity.HIGH, 2.0),
    ("high priority", Severity.HIGH, 1.5),
    ("multiple users", Severity.HIGH, 1.5),
    ("all users", Severity.HIGH, 1.5),
    ("team affected", Severity.HIGH, 1.5),
    ("affecting team", Severity.HIGH, 1.5),
    ("affecting multiple", Severity.HIGH, 1.5),
    ("regression", Severity.HIGH, 2.0),
    ("broke after update", Severity.HIGH, 2.5),
    ("broke after deploy", Severity.HIGH, 2.5),
    ("broke after upgrade", Severity.HIGH, 2.5),
    # MEDIUM
    ("slow", Severity.MEDIUM, 1.5),
    ("slowly", Severity.MEDIUM, 1.5),
    ("sluggish", Severity.MEDIUM, 1.5),
    ("lag", Severity.MEDIUM, 1.5),
    ("laggy", Severity.MEDIUM, 1.5),
    ("latency", Severity.MEDIUM, 1.5),
    ("high latency", Severity.MEDIUM, 2.0),
    ("delay", Severity.MEDIUM, 1.5),
    ("delayed", Severity.MEDIUM, 1.5),
    ("taking too long", Severity.MEDIUM, 1.5),
    ("takes long", Severity.MEDIUM, 1.5),
    ("loading slow", Severity.MEDIUM, 1.5),
    ("response slow", Severity.MEDIUM, 1.5),
    ("performance issue", Severity.MEDIUM, 2.0),
    ("performance degraded", Severity.MEDIUM, 2.0),
    ("degraded", Severity.MEDIUM, 1.5),
    ("degradation", Severity.MEDIUM, 1.5),
    ("throughput", Severity.MEDIUM, 1.0),
    ("memory usage high", Severity.MEDIUM, 1.5),
    ("cpu usage high", Severity.MEDIUM, 1.5),
    ("intermittent", Severity.MEDIUM, 2.0),
    ("intermittently", Severity.MEDIUM, 2.0),
    ("sometimes", Severity.MEDIUM, 1.0),
    ("occasionally", Severity.MEDIUM, 1.0),
    ("random", Severity.MEDIUM, 1.0),
    ("randomly", Severity.MEDIUM, 1.0),
    ("flaky", Severity.MEDIUM, 1.5),
    ("inconsistent", Severity.MEDIUM, 1.5),
    ("sporadic", Severity.MEDIUM, 1.5),
    ("on and off", Severity.MEDIUM, 1.5),
    ("now and then", Severity.MEDIUM, 1.0),
    ("every so often", Severity.MEDIUM, 1.0),
    ("partial", Severity.MEDIUM, 1.5),
    ("partially", Severity.MEDIUM, 1.5),
    ("partial outage", Severity.MEDIUM, 2.0),
    ("some users", Severity.MEDIUM, 1.5),
    ("certain users", Severity.MEDIUM, 1.5),
    ("workaround", Severity.MEDIUM, 2.0),
    ("workaround available", Severity.MEDIUM, 2.0),
    ("workaround exists", Severity.MEDIUM, 2.0),
    ("can work around", Severity.MEDIUM, 1.5),
    ("limited functionality", Severity.MEDIUM, 1.5),
    ("reduced functionality", Severity.MEDIUM, 1.5),
    ("partially working", Severity.MEDIUM, 1.5),
    ("mostly working", Severity.MEDIUM, 1.0),
    ("incorrect data", Severity.MEDIUM, 1.5),
    ("wrong data", Severity.MEDIUM, 1.5),
    ("inaccurate", Severity.MEDIUM, 1.5),
    ("missing data", Severity.MEDIUM, 1.5),
    ("data not showing", Severity.MEDIUM, 1.5),
    ("not displaying", Severity.MEDIUM, 1.5),
    ("not rendering", Severity.MEDIUM, 1.5),
    ("blank screen", Severity.MEDIUM, 1.5),
    ("freezes", Severity.MEDIUM, 1.5),
    ("hangs", Severity.MEDIUM, 1.5),
    ("unresponsive", Severity.MEDIUM, 1.5),
    ("stale data", Severity.MEDIUM, 1.0),
    ("cache issue", Severity.MEDIUM, 1.0),
    ("sync delay", Severity.MEDIUM, 1.5),
    ("warning", Severity.MEDIUM, 1.0),
    ("404", Severity.MEDIUM, 1.0),
    ("not found", Severity.MEDIUM, 1.0),
    ("unexpected behaviour", Severity.MEDIUM, 1.5),
    ("unexpected behavior", Severity.MEDIUM, 1.5),
    ("side effect", Severity.MEDIUM, 1.0),
    ("minor error", Severity.MEDIUM, 1.0),
    # LOW
    ("feature request", Severity.LOW, 2.0),
    ("feature suggestion", Severity.LOW, 2.0),
    ("enhancement", Severity.LOW, 1.5),
    ("enhancement request", Severity.LOW, 2.0),
    ("suggestion", Severity.LOW, 1.5),
    ("improvement", Severity.LOW, 1.5),
    ("nice to have", Severity.LOW, 2.0),
    ("wish list", Severity.LOW, 1.5),
    ("would be great", Severity.LOW, 1.5),
    ("could you add", Severity.LOW, 1.5),
    ("please add", Severity.LOW, 1.5),
    ("can you add", Severity.LOW, 1.5),
    ("new feature", Severity.LOW, 1.5),
    ("question", Severity.LOW, 1.5),
    ("how do i", Severity.LOW, 1.5),
    ("how to", Severity.LOW, 1.0),
    ("how can i", Severity.LOW, 1.5),
    ("where is", Severity.LOW, 1.0),
    ("what is", Severity.LOW, 1.0),
    ("wondering", Severity.LOW, 1.0),
    ("curious", Severity.LOW, 1.0),
    ("help me understand", Severity.LOW, 1.5),
    ("documentation", Severity.LOW, 1.0),
    ("docs", Severity.LOW, 1.0),
    ("tutorial", Severity.LOW, 1.0),
    ("training", Severity.LOW, 1.0),
    ("onboarding", Severity.LOW, 1.0),
    ("best practice", Severity.LOW, 1.0),
    ("best practices", Severity.LOW, 1.0),
    ("information", Severity.LOW, 1.0),
    ("informational", Severity.LOW, 1.5),
    ("inquiry", Severity.LOW, 1.5),
    ("general question", Severity.LOW, 2.0),
    ("typo", Severity.LOW, 2.0),
    ("spelling", Severity.LOW, 1.5),
    ("cosmetic", Severity.LOW, 2.0),
    ("aesthetic", Severity.LOW, 1.5),
    ("ui tweak", Severity.LOW, 2.0),
    ("colour", Severity.LOW, 1.0),
    ("color", Severity.LOW, 1.0),
    ("font", Severity.LOW, 1.0),
    ("alignment", Severity.LOW, 1.0),
    ("spacing", Severity.LOW, 1.0),
    ("minor", Severity.LOW, 1.5),
    ("small issue", Severity.LOW, 1.5),
    ("trivial", Severity.LOW, 2.0),
    ("low priority", Severity.LOW, 2.0),
    ("whenever possible", Severity.LOW, 1.5),
    ("no rush", Severity.LOW, 2.0),
    ("not urgent", Severity.LOW, 2.0),
    ("when you get a chance", Severity.LOW, 2.0),
    ("at your convenience", Severity.LOW, 2.0),
    ("low impact", Severity.LOW, 2.0),
    ("minimal impact", Severity.LOW, 1.5),
    ("no impact", Severity.LOW, 2.0),
]


async def seed_severity_keywords(db):
    from sqlalchemy import select

    result = await db.execute(select(SeverityKeyword))
    if result.scalars().first():
        return  # already seeded

    entries = [
        SeverityKeyword(
            keyword=kw,
            severity=sev,
            weight=w,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        for kw, sev, w in _SEED_KEYWORDS
    ]

    db.add_all(entries)

async def seed_sla_policies(db):

    sla_data = [
        ("Enterprise Critical SLA", "enterprise", 2, Severity.CRITICAL, 60, 120, 30, 60),
        ("Enterprise High SLA", "enterprise", 2, Severity.HIGH, 90, 180, 30, 60),
        ("Enterprise Medium SLA", "enterprise", 2, Severity.MEDIUM, 120, 240, 45, 90),
        ("Enterprise Low SLA", "enterprise", 2, Severity.LOW, 150, 300, 45, 90),

        ("SMB Critical SLA", "smb", 1, Severity.CRITICAL, 90, 180, 30, 60),
        ("SMB High SLA", "smb", 1, Severity.HIGH, 120, 240, 45, 90),
        ("SMB Medium SLA", "smb", 1, Severity.MEDIUM, 150, 300, 45, 90),
        ("SMB Low SLA", "smb", 1, Severity.LOW, 180, 360, 45, 90),
    ]

    for name, tier, tier_id, severity, resp, res, add_resp, add_res in sla_data:

        result = await db.execute(
            select(SLA).where(
                SLA.customer_tier == tier,
                SLA.severity == severity
            )
        )

        if result.scalar_one_or_none():
            continue

        db.add(
            SLA(
                name=name,
                customer_tier=tier,
                customer_tier_id=tier_id,
                severity=severity,
                response_time_mins=resp,
                resolution_time_mins=res,
                additional_response_mins=add_resp,
                additional_resolution_mins=add_res,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )


async def seed_issue_categories(db):

    issues = [
        (
            "Login Problem",
            IssueCategory.LOGIN_PROBLEM,
            "User cannot log into their account due to incorrect credentials, authentication failure, or login page errors."
        ),
        (
            "Password Reset",
            IssueCategory.ACCESS_ISSUE,
            "User is unable to reset their password due to missing reset email, expired link, or reset system error."
        ),
        (
            "Access Denied",
            IssueCategory.ACCESS_ISSUE,
            "User receives an access denied message when trying to open a page, feature, or resource."
        ),
        (
            "Application Bug",
            IssueCategory.BUG_OR_ERROR,
            "Unexpected error, broken functionality, or system crash while using the application."
        ),
        (
            "Slow Performance",
            IssueCategory.PERFORMANCE_ISSUE,
            "Application pages take too long to load or respond slowly during normal usage."
        ),
        (
            "Feature Request",
            IssueCategory.FEATURE_REQUEST,
            "User requests a new feature or improvement to existing functionality."
        ),
        (
            "Billing Problem",
            IssueCategory.BILLING_ISSUE,
            "Issues related to payments, invoices, subscription charges, or billing errors."
        ),
        (
            "Account Update",
            IssueCategory.ACCOUNT_MANAGEMENT,
            "User needs to update account information such as email, name, or organization details."
        ),
        (
            "Data Missing",
            IssueCategory.DATA_ISSUE,
            "User reports missing or incorrect data inside the system."
        ),
        (
            "Integration Failure",
            IssueCategory.INTEGRATION_ISSUE,
            "External integrations like APIs or third-party apps are not syncing correctly."
        ),
        (
            "Security Concern",
            IssueCategory.SECURITY_CONCERN,
            "User reports suspicious activity, potential vulnerabilities, or security risks."
        ),
        (
            "Other Issue",
            IssueCategory.OTHER,
            "Any other issue that does not fall into predefined categories."
        ),
    ]

    for name, category, description in issues:

        result = await db.execute(
            select(Issue).where(Issue.name == name)
        )

        if result.scalar_one_or_none():
            continue

        db.add(
            Issue(
                name=name,
                category=category,
                description=description,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )


async def main():
    async with AsyncSessionLocal() as db:

        await seed_sla_policies(db)
        await seed_issue_categories(db)
        await seed_severity_keywords(db)  

        await db.commit()

    print("✅ All seed data inserted successfully")


if __name__ == "__main__":
    asyncio.run(main())