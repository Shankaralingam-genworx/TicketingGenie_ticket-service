"""
Celery email intake task — polls IMAP inbox and runs LangGraph pipeline.
File path: src/core/celery/workers/email_intake_task.py

Start (two terminals on Windows):
    Terminal 1: celery -A src.core.celery.celery_app worker --loglevel=info -Q email --pool=solo
    Terminal 2: celery -A src.core.celery.celery_app beat   --loglevel=info
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import re
import traceback
import uuid
from email.header import decode_header
from typing import Optional

from celery.utils.log import get_task_logger

from src.config.settings import settings
from src.core.celery.celery_app import celery_app

# Use Celery's own logger — output always visible in worker terminal
logger = get_task_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# IMAP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _decode_header_value(raw: Optional[str]) -> str:
    """Decode RFC-2047-encoded header to plain string."""
    if not raw:
        return ""
    parts = []
    for fragment, charset in decode_header(raw):
        if isinstance(fragment, bytes):
            parts.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(fragment))
    return "".join(parts)


def _extract_plain_body(msg: email.message.Message) -> str:
    """Return the first text/plain part; fall back to stripped text/html."""
    plain = html = None
    for part in msg.walk():
        ct  = part.get_content_type()
        cd  = part.get("Content-Disposition", "")
        if "attachment" in cd:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace")
        if ct == "text/plain" and plain is None:
            plain = decoded
        elif ct == "text/html" and html is None:
            html = decoded

    if plain:
        return plain
    if html:
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _parse_raw_message(raw_bytes: bytes) -> Optional[dict]:
    """Parse raw RFC-822 bytes into the EmailIntakeState initial dict."""
    try:
        msg = email.message_from_bytes(raw_bytes, policy=email.policy.compat32)

        from_header = _decode_header_value(msg.get("From", ""))
        subject     = _decode_header_value(msg.get("Subject", ""))
        message_id  = (msg.get("Message-ID", "") or "").strip()
        in_reply_to = (msg.get("In-Reply-To", "") or "").strip() or None
        references  = (msg.get("References",  "") or "").strip() or None
        body        = _extract_plain_body(msg)

        if not message_id:
            message_id = f"<generated-{uuid.uuid4()}@intake>"

        logger.info(f"[intake_task] Parsed email | from={from_header!r} | subject={subject!r}")

        return {
            "raw_from":    from_header,
            "raw_subject": subject,
            "raw_body":    body,
            "message_id":  message_id,
            "in_reply_to": in_reply_to,
            "references":  references,
        }

    except Exception:
        logger.error(f"[intake_task] Failed to parse message:\n{traceback.format_exc()}")
        return None


def _fetch_unseen_messages() -> list[tuple[bytes, bytes]]:
    """Connect via IMAP SSL, fetch all UNSEEN messages, mark them SEEN."""
    host   = settings.SUPPORT_EMAIL_HOST
    port   = int(getattr(settings, "SUPPORT_EMAIL_PORT", 993))
    user   = settings.SUPPORT_EMAIL_USER
    passwd = settings.SUPPORT_EMAIL_PASS
    folder = getattr(settings, "SUPPORT_EMAIL_FOLDER", "INBOX")

    messages: list[tuple[bytes, bytes]] = []

    logger.info(f"[intake_task] Connecting to IMAP {host}:{port} as {user}")

    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, passwd)
            logger.info("[intake_task] IMAP login successful")

            imap.select(folder)
            status, data = imap.search(None, "UNSEEN")

            if status != "OK" or not data or not data[0]:
                logger.info("[intake_task] No unseen messages found")
                return messages

            uids = data[0].split()
            logger.info(f"[intake_task] Found {len(uids)} unseen message(s)")

            for uid in uids:
                try:
                    status, msg_data = imap.fetch(uid, "(RFC822)")
                    if status != "OK" or not msg_data:
                        logger.warning(f"[intake_task] Failed to fetch uid={uid}")
                        continue
                    raw_bytes = msg_data[0][1]
                    messages.append((uid, raw_bytes))
                    imap.store(uid, "+FLAGS", "\\Seen")
                    logger.info(f"[intake_task] Fetched and marked SEEN uid={uid}")
                except Exception:
                    logger.error(
                        f"[intake_task] Error fetching uid={uid}:\n{traceback.format_exc()}"
                    )

    except imaplib.IMAP4.error:
        logger.error(f"[intake_task] IMAP auth/connection error:\n{traceback.format_exc()}")
    except Exception:
        logger.error(f"[intake_task] Unexpected IMAP error:\n{traceback.format_exc()}")

    return messages


# ─────────────────────────────────────────────────────────────────────────────
# Celery task
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name    = "src.core.celery.workers.email_intake_task.poll_support_inbox_task",
    bind    = True,
    max_retries = 0,
    queue   = "ticket_email",
    time_limit      = 300,
    soft_time_limit = 240,
)
def poll_support_inbox_task(self) -> dict:
    """
    Beat-driven task — polls support inbox every EMAIL_POLL_INTERVAL seconds
    and runs each unseen email through the LangGraph intake pipeline.
    """
    logger.info("=" * 60)
    logger.info("[intake_task] ▶ Poll started")

    try:
        raw_messages = _fetch_unseen_messages()
    except Exception:
        logger.error(f"[intake_task] _fetch_unseen_messages crashed:\n{traceback.format_exc()}")
        return {"processed": 0, "created": 0, "comments": 0, "rejected": 0}

    if not raw_messages:
        logger.info("[intake_task] ✓ No new messages — done")
        return {"processed": 0, "created": 0, "comments": 0, "rejected": 0}

    try:
        # Must use a fresh event loop per task — NOT asyncio.run().
        # Celery prefork workers inherit the parent process event loop.
        # asyncpg futures are bound to that loop; a second asyncio.run() call
        # creates a new loop causing: "Future attached to a different loop".
        # Fix: create, use, and close a brand-new loop every invocation.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(_process_all(raw_messages))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        # dummy assignment so the except block below still has `results` in scope
        results = results  # noqa
    except Exception:
        logger.error(f"[intake_task] _process_all crashed:\n{traceback.format_exc()}")
        return {"processed": len(raw_messages), "created": 0, "comments": 0, "rejected": len(raw_messages)}

    summary = {
        "processed": len(results),
        "created":   sum(1 for r in results if r.get("outcome") == "ticket_created"),
        "comments":  sum(1 for r in results if r.get("outcome") == "comment_added"),
        "rejected":  sum(1 for r in results if r.get("outcome") == "rejected"),
    }
    logger.info(
        f"[intake_task] ✓ Poll complete | "
        f"created={summary['created']} "
        f"comments={summary['comments']} "
        f"rejected={summary['rejected']}"
    )
    logger.info("=" * 60)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Async pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

async def _process_all(raw_messages: list[tuple[bytes, bytes]]) -> list[dict]:
    """Run each message through the LangGraph pipeline sequentially."""
    # Import here to avoid circular import at module load time
    from src.control.agents.email_intake_graph import email_intake_graph

    results = []

    for uid, raw_bytes in raw_messages:
        logger.info(f"[intake_task] Processing uid={uid}")

        state = _parse_raw_message(raw_bytes)
        if state is None:
            results.append({"outcome": "rejected", "error": "parse_failed"})
            continue

        try:
            final_state = await email_intake_graph.ainvoke(state)

            outcome    = final_state.get("outcome", "unknown")
            ticket_num = final_state.get("ticket_number")
            reason     = final_state.get("rejection_reason")
            error      = final_state.get("error")

            if outcome == "ticket_created":
                logger.info(
                    f"[intake_task] ✅ Ticket created: {ticket_num} | "
                    f"from={state['raw_from']}"
                )
            elif outcome == "comment_added":
                logger.info(
                    f"[intake_task] 💬 Comment added to {ticket_num} | "
                    f"from={state['raw_from']}"
                )
            else:
                logger.warning(
                    f"[intake_task] ❌ Rejected | "
                    f"reason={reason!r} | "
                    f"error={error!r} | "
                    f"from={state['raw_from']}"
                )

            results.append({
                "outcome":       outcome,
                "ticket_number": ticket_num,
                "error":         error,
            })

        except Exception:
            tb = traceback.format_exc()
            logger.error(
                f"[intake_task] Pipeline exception for uid={uid}:\n{tb}"
            )
            results.append({"outcome": "rejected", "error": tb})

    return results