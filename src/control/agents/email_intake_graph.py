"""LangGraph pipeline for inbound email → ticket / comment routing."""

from __future__ import annotations

import json
import random
import re
import string
import uuid
from datetime import datetime, timezone
from typing import Optional, TypedDict

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import settings
from src.constants.issue_constants import IssueCategory
from src.constants.priority_constants import SEVERITY_TO_PRIORITY
from src.constants.sla_constants import AuditAction, CommentSource
from src.constants.ticket_constants import TicketSource, TicketStatus
from src.control.agents.severity_agent import SeverityAgent
from src.core.celery.workers.email_tasks import send_ticket_acknowledgement_task
from src.core.services.notification_service import NotificationService
from src.data.models.postgres.email_thread_model import EmailThreadStatus
from src.data.models.postgres.notification_model import NotificationActor
from src.data.repositories.comment_repository import CommentRepository
from src.data.repositories.email_thread_repository import EmailThreadRepository
from src.data.repositories.issue_repository import IssueRepository
from src.data.repositories.issue_resolver_repository import IssueResolverRepository
from src.data.repositories.sla_repository import SLARepository
from src.data.repositories.ticket_audit_repository import TicketAuditRepository
from src.data.repositories.ticket_repository import TicketRepository
from src.utils.sla_utils import compute_due_at
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="email-intake")


# ── DB session factory ────────────────────────────────────────────────────────

def _make_session() -> AsyncSession:
    """Create a fresh AsyncSession for each graph node (nodes run independently)."""
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return factory()


# ── State ─────────────────────────────────────────────────────────────────────

class EmailIntakeState(TypedDict, total=False):
    # Raw inbound fields
    raw_from:    str
    raw_subject: str
    raw_body:    str
    message_id:  str
    in_reply_to: Optional[str]
    references:  Optional[str]

    # Resolved customer
    customer_email: str
    customer_id:    Optional[int]
    customer_tier:  Optional[str]
    org_id:         Optional[int]
    customer_valid: bool

    # Thread detection
    is_reply:           bool
    existing_thread_id: Optional[int]
    existing_ticket_id: Optional[int]

    # LLM extraction
    content_valid:       bool
    rejection_reason:    Optional[str]
    cleaned_title:       Optional[str]
    cleaned_description: Optional[str]
    issue_category_str:  Optional[str]

    # Issue classification
    issue_id:   Optional[int]
    issue_name: Optional[str]

    # Severity + created ticket
    severity:      Optional[str]
    ticket_id:     Optional[int]
    ticket_number: Optional[str]

    # Pipeline result
    outcome: str   # "ticket_created" | "comment_added" | "rejected"
    error:   Optional[str]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _extract_email(raw: str) -> str:
    """Extract bare address from 'Name <addr>' or plain 'addr'."""
    m = re.search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip().lower()


def _generate_ticket_number() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    month  = datetime.now(timezone.utc).strftime("%Y%m")
    return f"TKT-{month}-{suffix}"


def _llm() -> ChatGroq:
    return ChatGroq(api_key=settings.GROQ_API_KEY, model=settings.GROQ_MODEL, temperature=0)


def _reject(state: EmailIntakeState, reason: str) -> EmailIntakeState:
    """Mark state as rejected and record the reason."""
    state["customer_valid"]   = False
    state["content_valid"]    = False
    state["rejection_reason"] = reason
    return state


# ── Node 1: validate_customer ─────────────────────────────────────────────────

async def validate_customer(state: EmailIntakeState) -> EmailIntakeState:
    """Call Auth service to confirm sender is a registered CUSTOMER."""
    email = _extract_email(state.get("raw_from", ""))
    state["customer_email"] = email

    logger.info("intake_validate_customer_started", email=email)

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{settings.AUTH_SERVICE_URL}/api/v1/users/by-email",
                params={"email": email},
            )

        if resp.status_code == 200:
            data = resp.json()
            if data.get("role", "").lower() == "customer":
                state["customer_id"]    = int(data["id"])
                state["customer_tier"]  = (data.get("customer_tier") or "smb").lower()
                state["org_id"]         = data.get("org_id")
                state["customer_valid"] = True
                logger.info(
                    "intake_customer_validated",
                    email=email,
                    customer_id=state["customer_id"],
                    tier=state["customer_tier"],
                )
                return state

        logger.warning("intake_customer_not_found", email=email, status=resp.status_code)
        return _reject(state, f"No registered customer account found for {email}")

    except Exception as exc:
        logger.error("intake_auth_service_error", email=email, error=str(exc))
        return _reject(state, f"Auth service unavailable: {exc}")


# ── Node 2: check_thread ──────────────────────────────────────────────────────

async def check_thread(state: EmailIntakeState) -> EmailIntakeState:
    """Match In-Reply-To / References headers to an existing active thread."""
    state["is_reply"] = False

    in_reply_to = state.get("in_reply_to")
    references  = state.get("references")

    if not in_reply_to and not references:
        logger.debug("intake_check_thread_no_reply_headers")
        return state

    async with _make_session() as session:
        thread = await EmailThreadRepository(session).find_by_references(
            in_reply_to, references
        )

    if thread:
        state["is_reply"]           = True
        state["existing_thread_id"] = thread.id
        state["existing_ticket_id"] = thread.ticket_id
        logger.info(
            "intake_thread_matched",
            thread_id=thread.id,
            ticket_id=thread.ticket_id,
        )
    else:
        # Headers present but no matching thread — treat as a new ticket
        logger.debug("intake_thread_not_matched")

    return state


# ── Node 3: validate_content (LLM) ───────────────────────────────────────────

_CONTENT_SYSTEM = """\
You are a support email triage assistant for a SaaS ticketing system.

Analyse the email Subject and Body provided by the user.
Respond ONLY with a single valid JSON object — no markdown, no explanation outside the JSON.

REJECT (is_valid: false) if the email is:
- An auto-reply, out-of-office, delivery failure, or bounce message
- A newsletter, promotional, or marketing email
- Completely empty or contains only a greeting / signature with no issue
- Spam or clearly not a support request

ACCEPT (is_valid: true) if the email contains a genuine support issue or question.

When accepting:
- cleaned_title       : concise title, max 120 chars, no greetings or signatures
- cleaned_description : full body cleaned — strip greetings, signatures, previous
                        quoted email chains (lines starting with ">"), legal footers
- issue_category      : exactly ONE value from:
    login_problem | access_issue | bug_or_error | performance_issue |
    feature_request | billing_issue | account_management | data_issue |
    integration_issue | security_concern | other

JSON schema (always include ALL keys):
{
  "is_valid": true | false,
  "rejection_reason": "string or null",
  "cleaned_title": "string or null",
  "cleaned_description": "string or null",
  "issue_category": "one of the values above or null"
}"""


async def validate_content(state: EmailIntakeState) -> EmailIntakeState:
    """Use Groq LLM to confirm genuine support request and extract structured fields."""
    subject = state.get("raw_subject", "")
    body    = state.get("raw_body", "")

    logger.info("intake_validate_content_started")

    try:
        response = await _llm().ainvoke([
            SystemMessage(content=_CONTENT_SYSTEM),
            HumanMessage(content=f"Subject: {subject}\n\nBody:\n{body[:4000]}"),
        ])
        raw_text = re.sub(r"```(?:json)?|```", "", response.content).strip()
        parsed   = json.loads(raw_text)

        state["content_valid"]       = bool(parsed.get("is_valid", False))
        state["rejection_reason"]    = parsed.get("rejection_reason")
        state["cleaned_title"]       = parsed.get("cleaned_title")
        state["cleaned_description"] = parsed.get("cleaned_description")
        state["issue_category_str"]  = parsed.get("issue_category")

        logger.info(
            "intake_content_validated",
            content_valid=state["content_valid"],
            category=state["issue_category_str"],
        )

    except Exception as exc:
        logger.error("intake_content_validation_failed", error=str(exc))
        state["content_valid"]    = False
        state["rejection_reason"] = f"Content validation error: {exc}"

    return state


# ── Node 4: classify_issue ────────────────────────────────────────────────────

_CATEGORY_MAP: dict[str, str] = {c.value: c.value for c in IssueCategory}


async def classify_issue(state: EmailIntakeState) -> EmailIntakeState:
    """Map LLM category string to a live Issue row. Falls back to OTHER."""
    cat_str = (state.get("issue_category_str") or "other").lower().strip()
    cat_val = _CATEGORY_MAP.get(cat_str, IssueCategory.OTHER.value)

    async with _make_session() as session:
        issues = await IssueRepository(session).get_all(active_only=True)

    if not issues:
        logger.error("intake_classify_no_active_issues")
        state["content_valid"]    = False
        state["rejection_reason"] = "No active issue categories in database"
        return state

    matched = (
        [i for i in issues if i.category.value == cat_val]
        or [i for i in issues if i.category == IssueCategory.OTHER]
        or [issues[0]]
    )

    state["issue_id"]   = matched[0].id
    state["issue_name"] = matched[0].name
    logger.info(
        "intake_issue_classified",
        issue_id=matched[0].id,
        issue_name=matched[0].name,
        category=cat_val,
    )
    return state


# ── Node 5: detect_severity ───────────────────────────────────────────────────

async def detect_severity(state: EmailIntakeState) -> EmailIntakeState:
    """Run SeverityAgent then apply enterprise tier bump."""
    title       = state.get("cleaned_title")       or state.get("raw_subject", "")
    description = state.get("cleaned_description") or state.get("raw_body", "")
    issue_name  = state.get("issue_name")           or ""

    async with _make_session() as session:
        severity = await SeverityAgent(session).detect(
            issue_name=issue_name, title=title, description=description,
        )

    sev = severity.value

    # Enterprise tier gets a severity bump for faster SLA
    if (state.get("customer_tier") or "smb").lower() == "enterprise":
        if sev == "high":     sev = "critical"
        elif sev == "medium": sev = "high"
        elif sev == "low":    sev = "medium"

    state["severity"] = sev
    logger.info("intake_severity_detected", severity=sev)
    return state


# ── Node 6: create_ticket ─────────────────────────────────────────────────────

async def create_ticket(state: EmailIntakeState) -> EmailIntakeState:
    """
    Create ticket from email: SLA lookup → priority mapping → DB insert →
    audit log → Celery ack email → in-app notification → thread record.
    """
    from src.constants.sla_constants import Severity

    customer_id    = state["customer_id"]
    customer_email = state["customer_email"]
    customer_tier  = (state.get("customer_tier") or "smb").lower()
    org_id         = state.get("org_id")
    severity       = Severity(state["severity"])
    issue_id       = state["issue_id"]
    title          = state["cleaned_title"]
    description    = state["cleaned_description"]
    message_id     = state["message_id"]

    logger.info(
        "intake_create_ticket_started",
        customer_id=customer_id,
        severity=severity.value,
        issue_id=issue_id,
    )

    try:
        async with _make_session() as session:
            async with session.begin():
                ticket_repo   = TicketRepository(session)
                sla_repo      = SLARepository(session)
                resolver_repo = IssueResolverRepository(session)
                audit_repo    = TicketAuditRepository(session)
                thread_repo   = EmailThreadRepository(session)
                notif_svc     = NotificationService(session)

                sla = await sla_repo.get_by_tier_and_severity(customer_tier, severity)
                response_due_at = resolution_due_at = sla_id = None
                sla_resolution_hrs: float | None = None

                if sla:
                    response_due_at    = compute_due_at(sla.response_time_mins)
                    resolution_due_at  = compute_due_at(sla.resolution_time_mins)
                    sla_id             = sla.id
                    sla_resolution_hrs = round(sla.resolution_time_mins / 60, 1)
                    logger.info("intake_sla_matched", sla_id=sla.id, sla_name=sla.name)
                else:
                    logger.warning(
                        "intake_sla_not_found",
                        customer_tier=customer_tier,
                        severity=severity.value,
                    )

                # Email has no customer-chosen priority — use system-derived value
                system_priority = SEVERITY_TO_PRIORITY[severity]

                resolvers = await resolver_repo.get_by_issue(issue_id)
                team_id: int | None = resolvers[0].team_id if resolvers else None

                ticket_number = _generate_ticket_number()
                ticket = await ticket_repo.create(
                    ticket_number                   = ticket_number,
                    title                           = title,
                    description                     = description,
                    customer_id                     = customer_id,
                    customer_email                  = customer_email,
                    customer_tier                   = customer_tier,
                    org_id                          = org_id,
                    issue_id                        = issue_id,
                    customer_priority               = system_priority,
                    priority                        = system_priority,
                    priority_overridden             = False,
                    priority_override_justification = None,
                    severity                        = severity,
                    status                          = TicketStatus.NEW,
                    source                          = TicketSource.EMAIL,
                    team_id                         = team_id,
                    assigned_agent_id               = None,
                    sla_id                          = sla_id,
                    response_due_at                 = response_due_at,
                    resolution_due_at               = resolution_due_at,
                    attachments                     = None,
                )

                await audit_repo.log(
                    ticket_id  = ticket.id,
                    action     = AuditAction.CREATED,
                    actor_id   = customer_id,
                    actor_role = "customer",
                    new_value  = {
                        "ticket_number": ticket_number,
                        "severity":      severity.value,
                        "priority":      system_priority.value,
                        "team_id":       team_id,
                        "source":        "email",
                    },
                )

                await thread_repo.create(
                    customer_email = customer_email,
                    message_id     = message_id,
                    subject        = title,
                    ticket_id      = ticket.id,
                    status         = EmailThreadStatus.ACTIVE,
                )

                await notif_svc.ticket_created(
                    recipient_id  = customer_id,
                    ticket_id     = ticket.id,
                    ticket_number = ticket_number,
                    ticket_title  = title,
                )

        # Celery task runs outside the DB transaction to avoid blocking commit
        send_ticket_acknowledgement_task.delay(
            ticket_id           = ticket.id,
            to_email            = customer_email,
            ticket_number       = ticket_number,
            ticket_title        = title,
            severity            = severity.value,
            priority            = system_priority.value,
            priority_overridden = False,
            original_priority   = system_priority.value,
            system_priority     = system_priority.value,
            sla_resolution_hrs  = sla_resolution_hrs,
        )

        state["ticket_id"]     = ticket.id
        state["ticket_number"] = ticket_number
        state["outcome"]       = "ticket_created"
        logger.info(
            "intake_ticket_created",
            ticket_number=ticket_number,
            customer_email=customer_email,
            severity=severity.value,
        )

    except Exception as exc:
        logger.error("intake_create_ticket_failed", error=str(exc), exc_info=True)
        state["outcome"] = "rejected"
        state["error"]   = f"Ticket creation failed: {exc}"

    return state


# ── Node 7: add_comment ───────────────────────────────────────────────────────

async def add_comment(state: EmailIntakeState) -> EmailIntakeState:
    """Add customer reply as a comment on the existing ticket and notify the agent."""
    ticket_id   = state["existing_ticket_id"]
    thread_id   = state["existing_thread_id"]
    customer_id = state["customer_id"]
    body        = state.get("raw_body", "").strip()

    # Strip quoted reply lines and blank lines
    cleaned_body = "\n".join(
        line for line in body.splitlines()
        if not line.startswith(">") and line.strip()
    ).strip()

    if not cleaned_body:
        logger.info("intake_add_comment_empty_body", ticket_id=ticket_id)
        state["outcome"]          = "rejected"
        state["rejection_reason"] = "Reply body was empty after stripping quoted text"
        return state

    logger.info("intake_add_comment_started", ticket_id=ticket_id, customer_id=customer_id)

    try:
        async with _make_session() as session:
            async with session.begin():
                comment_repo = CommentRepository(session)
                thread_repo  = EmailThreadRepository(session)
                notif_svc    = NotificationService(session)
                ticket_repo  = TicketRepository(session)

                ticket = await ticket_repo.get_by_id(ticket_id)
                if not ticket:
                    logger.warning("intake_add_comment_ticket_not_found", ticket_id=ticket_id)
                    state["outcome"]          = "rejected"
                    state["rejection_reason"] = f"Ticket {ticket_id} not found"
                    return state

                await comment_repo.create(
                    ticket_id   = ticket_id,
                    author_id   = customer_id,
                    author_role = "CUSTOMER",
                    content     = cleaned_body,
                    source      = CommentSource.EMAIL,
                )

                # Bump thread timestamp so idle-detection queries see activity
                from src.data.models.postgres.email_thread_model import EmailThread
                thread = await session.get(EmailThread, thread_id)
                if thread:
                    thread.last_email_at = datetime.now(timezone.utc)

                if ticket.assigned_agent_id:
                    await notif_svc.comment_received(
                        recipient_id   = ticket.assigned_agent_id,
                        recipient_role = NotificationActor.SUPPORT_AGENT,
                        ticket_id      = ticket_id,
                        ticket_number  = ticket.ticket_number,
                        ticket_title   = ticket.title,
                        author_role    = "CUSTOMER",
                        preview        = cleaned_body[:200],
                    )

        state["ticket_id"]     = ticket_id
        state["ticket_number"] = ticket.ticket_number
        state["outcome"]       = "comment_added"
        logger.info(
            "intake_comment_added",
            ticket_id=ticket_id,
            ticket_number=ticket.ticket_number,
            customer_email=state["customer_email"],
        )

    except Exception as exc:
        logger.error("intake_add_comment_failed", error=str(exc), exc_info=True)
        state["outcome"] = "rejected"
        state["error"]   = f"Comment creation failed: {exc}"

    return state


# ── Node 8: reject_email ──────────────────────────────────────────────────────

async def reject_email(state: EmailIntakeState) -> EmailIntakeState:
    """Record rejection in email_threads for audit trail. No auto-reply sent."""
    reason     = state.get("rejection_reason", "Unknown reason")
    email      = state.get("customer_email") or state.get("raw_from", "unknown")
    message_id = state.get("message_id")     or f"unknown-{uuid.uuid4()}"
    subject    = state.get("raw_subject", "")

    try:
        async with _make_session() as session:
            async with session.begin():
                repo = EmailThreadRepository(session)
                # Guard: skip if this message_id was already recorded
                existing = await repo.get_by_message_id(message_id)
                if not existing:
                    await repo.create(
                        customer_email   = email,
                        message_id       = message_id,
                        subject          = subject,
                        ticket_id        = None,
                        status           = EmailThreadStatus.REJECTED,
                        rejection_reason = reason,
                    )
    except Exception as exc:
        logger.error("intake_reject_email_db_failed", error=str(exc))

    state["outcome"] = "rejected"
    logger.info("intake_email_rejected", reason=reason, email=email)
    return state


# ── Routing functions ─────────────────────────────────────────────────────────

def _route_customer(state: EmailIntakeState) -> str:
    return "check_thread" if state.get("customer_valid") else "reject_email"


def _route_thread(state: EmailIntakeState) -> str:
    if state.get("is_reply") and state.get("existing_ticket_id"):
        return "reply_validate_content"
    return "validate_content"


def _route_content(state: EmailIntakeState) -> str:
    return "classify_issue" if state.get("content_valid") else "reject_email"


def _route_reply_content(state: EmailIntakeState) -> str:
    return "add_comment" if state.get("content_valid") else "reject_email"


def _route_classify(state: EmailIntakeState) -> str:
    return "detect_severity" if state.get("content_valid") else "reject_email"


# ── Graph definition ──────────────────────────────────────────────────────────

def build_email_intake_graph():
    g = StateGraph(EmailIntakeState)

    g.add_node("validate_customer",      validate_customer)
    g.add_node("check_thread",           check_thread)
    g.add_node("validate_content",       validate_content)
    g.add_node("reply_validate_content", validate_content)   # same fn, reply branch
    g.add_node("classify_issue",         classify_issue)
    g.add_node("detect_severity",        detect_severity)
    g.add_node("create_ticket",          create_ticket)
    g.add_node("add_comment",            add_comment)
    g.add_node("reject_email",           reject_email)

    g.set_entry_point("validate_customer")

    g.add_conditional_edges(
        "validate_customer", _route_customer,
        {"check_thread": "check_thread", "reject_email": "reject_email"},
    )
    g.add_conditional_edges(
        "check_thread", _route_thread,
        {"validate_content": "validate_content", "reply_validate_content": "reply_validate_content"},
    )
    g.add_conditional_edges(
        "validate_content", _route_content,
        {"classify_issue": "classify_issue", "reject_email": "reject_email"},
    )
    g.add_conditional_edges(
        "reply_validate_content", _route_reply_content,
        {"add_comment": "add_comment", "reject_email": "reject_email"},
    )
    g.add_conditional_edges(
        "classify_issue", _route_classify,
        {"detect_severity": "detect_severity", "reject_email": "reject_email"},
    )

    g.add_edge("detect_severity", "create_ticket")
    g.add_edge("create_ticket",   END)
    g.add_edge("add_comment",     END)
    g.add_edge("reject_email",    END)

    return g.compile()


# Compiled singleton — import and call .ainvoke()
email_intake_graph = build_email_intake_graph()