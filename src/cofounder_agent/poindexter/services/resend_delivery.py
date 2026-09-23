"""Resend delivery-state poll — what happened to the mail we sent.

The newsletter fires on every publish seam (``publish_service`` ×4 plus
``scheduled_publisher``) and ``campaign_email_logs`` records that we handed
each message to Resend. That ledger answers "did we send?" — it cannot
answer "did it land?", which is the question bounces and complaints live in.

That second answer used to arrive by webhook at ``POST /api/webhooks/resend``.
That route is unreachable from the internet (401 locally, 404 publicly), so
receipts stopped 2026-07-19 while sending stayed healthy — the table looked
dead while the feature it reports on was fine. Nothing alerted, because
``webhook_freshness_subscriber_threshold_days`` had been raised 7 -> 180.

Resend's REST API answers it directly: ``GET /emails`` lists sent messages
with ``last_event`` (``delivered`` / ``bounced`` / ``complained`` / ...), so
delivery state needs no ingress at all. Same shape as the Lemon Squeezy
invoice poll (stack#3954) — when a provider looks like it needs a webhook,
check whether its API already knows.

Idempotency is structural: one row per ``(provider_message_id, event_type)``
against ``ux_subscriber_events_provider_event``, so a re-poll writes nothing
and a message that later transitions (delivered -> complained) adds a row
rather than mutating one. The ledger stays append-only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

RESEND_API_BASE = "https://api.resend.com"

#: ``last_event`` values worth recording. Resend also reports transient
#: pre-delivery states (``sent``, ``queued``, ``scheduled``); those are not
#: outcomes and would churn a row per poll until they settle, so only
#: terminal-ish states are persisted.
RECORDED_EVENTS = frozenset(
    {"delivered", "bounced", "complained", "delivery_delayed", "failed"}
)


@dataclass
class PollOutcome:
    """What one poll pass observed and wrote."""

    emails_seen: int = 0
    rows_written: int = 0
    skipped_non_terminal: int = 0
    unknown_recipients: int = 0
    errors: list[str] = field(default_factory=list)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "emails_seen": self.emails_seen,
            "rows_written": self.rows_written,
            "skipped_non_terminal": self.skipped_non_terminal,
            "unknown_recipients": self.unknown_recipients,
            "errors": len(self.errors),
        }


def _first_recipient(record: dict[str, Any]) -> str | None:
    """Resend's ``to`` is a list; the newsletter sends one recipient each."""
    to = record.get("to")
    if isinstance(to, list) and to:
        return str(to[0])
    if isinstance(to, str) and to:
        return to
    return None


async def _fetch_emails(
    client: httpx.AsyncClient, api_key: str, limit_pages: int = 20
) -> list[dict[str, Any]]:
    """List sent emails across pages.

    Bounded by ``limit_pages`` so a pathological ``has_more`` loop cannot
    spin forever against a provider we do not control.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    out: list[dict[str, Any]] = []
    url: str | None = f"{RESEND_API_BASE}/emails"
    pages = 0
    while url and pages < limit_pages:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        out.extend(body.get("data") or [])
        pages += 1
        # Resend paginates with `has_more` + an `after` cursor on the last id.
        if not body.get("has_more"):
            break
        last = out[-1].get("id") if out else None
        url = f"{RESEND_API_BASE}/emails?after={last}" if last else None
    return out


async def _record_event(conn: Any, record: dict[str, Any]) -> tuple[int, bool]:
    """Write one delivery-state row.

    Returns ``(rows_written, recipient_is_known_subscriber)``.
    """
    message_id = record.get("id")
    last_event = str(record.get("last_event") or "").lower()
    if not message_id or last_event not in RECORDED_EVENTS:
        return (0, False)

    email = _first_recipient(record)
    # EMAIL is the identity for this table — every writer sets it, and the
    # uuid ``subscriber_id`` column that used to sit beside it was dropped
    # (stack migration 20260923_225300) because ``newsletter_subscribers.id``
    # is a serial int and the two could never be joined. Binding the int
    # anyway was a DataError that failed all 19 messages on this poll's first
    # production tick.
    #
    # The subscriber lookup survives as an existence check only: a delivery
    # to an address that is not on the list is worth counting even though it
    # cannot be foreign-keyed.
    subscriber_known = False
    if email:
        subscriber_known = bool(
            await conn.fetchval(
                "SELECT 1 FROM newsletter_subscribers WHERE lower(email) = lower($1)",
                email,
            )
        )

    result = await conn.execute(
        """
        INSERT INTO subscriber_events (
            email, event_type, event_data, provider_message_id
        )
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (provider_message_id, event_type)
        WHERE provider_message_id IS NOT NULL
        DO NOTHING
        """,
        email,
        f"email.{last_event}",
        json.dumps(
            {
                "via": "resend_delivery_poll",
                "subject": record.get("subject"),
                "created_at": record.get("created_at"),
                "message_id": record.get("message_id"),
            }
        ),
        str(message_id),
    )
    written = 1 if str(result).strip().endswith(" 1") else 0
    return (written, subscriber_known)


async def poll_delivery_state(
    pool: Any,
    site_config: SiteConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PollOutcome:
    """One pass: list Resend emails, record their delivery state."""
    outcome = PollOutcome()
    api_key = await site_config.get_secret("resend_api_key", "")
    if not api_key:
        outcome.errors.append("resend_api_key not set")
        return outcome

    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        records = await _fetch_emails(client, api_key)

    outcome.emails_seen = len(records)
    async with pool.acquire() as conn:
        for record in records:
            try:
                last_event = str(record.get("last_event") or "").lower()
                if last_event not in RECORDED_EVENTS:
                    outcome.skipped_non_terminal += 1
                    continue
                written, known = await _record_event(conn, record)
                outcome.rows_written += written
                # Mail to an address that is not (or is no longer) a
                # subscriber is worth counting: it is how a leaked send
                # or a stale audience shows up.
                if written and not known:
                    outcome.unknown_recipients += 1
            except Exception as exc:  # per-record isolation
                outcome.errors.append(f"{record.get('id')}: {describe_exception(exc)}")
                logger.error(
                    "[RESEND_POLL] failed to record %s: %s",
                    record.get("id"), describe_exception(exc), exc_info=True,
                )
    return outcome
