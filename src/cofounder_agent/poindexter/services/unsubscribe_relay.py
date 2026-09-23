"""Apply unsubscribe requests queued at the edge relay.

A recipient clicking "unsubscribe" in their mail client has to reach
something live, and this worker is local-first with no public ingress — the
one part of the newsletter that a poll genuinely cannot substitute for. So a
Cloudflare Worker (``infrastructure/cloudflare/unsubscribe-relay``) takes the
click, confirms to the recipient immediately, and parks the token in KV;
this module drains that queue outbound-only, exactly like the Lemon Squeezy
custom_data relay.

The Worker cannot validate tokens — it has no database. Validation happens
HERE: an unknown token updates zero rows and is acked anyway, so junk that
someone posts at the public endpoint cannot accumulate. The token itself is
the credential (43 base64url chars, ~256 bits), and the same
``unsubscribed_at IS NULL`` guard the HTTP route uses keeps a re-click from
overwriting the original opt-out timestamp.

Ack only after the DB write succeeds. If the process dies mid-drain the
token stays in KV and the next tick re-applies it — re-applying is a no-op
against the already-set ``unsubscribed_at``, whereas acking first would drop
an opt-out on the floor. Losing one is the failure that matters.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

#: Mirrors ``routes/newsletter_routes._mint_unsubscribe_token`` —
#: ``secrets.token_urlsafe(32)`` is exactly 43 base64url characters.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


@dataclass
class DrainOutcome:
    """What one drain pass saw and applied."""

    pending_seen: int = 0
    unsubscribed: int = 0
    already_or_unknown: int = 0
    acked: int = 0
    errors: list[str] = field(default_factory=list)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "pending_seen": self.pending_seen,
            "unsubscribed": self.unsubscribed,
            "already_or_unknown": self.already_or_unknown,
            "acked": self.acked,
            "errors": len(self.errors),
        }


async def drain_unsubscribe_queue(
    pool: Any,
    site_config: SiteConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DrainOutcome:
    """Fetch queued tokens from the relay, apply them, ack what landed."""
    outcome = DrainOutcome()

    base = (site_config.get("newsletter_unsubscribe_relay_url", "") or "").strip()
    if not base:
        # Not an error: the relay is opt-in, and send_post_newsletter already
        # refuses to mail without it. Nothing queued means nothing to drain.
        return outcome
    secret = await site_config.get_secret("newsletter_unsubscribe_relay_secret", "")
    if not secret:
        outcome.errors.append("newsletter_unsubscribe_relay_secret not set")
        return outcome

    base = base.rstrip("/")
    headers = {"Authorization": f"Bearer {secret}"}

    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        resp = await client.get(f"{base}/pending", headers=headers)
        resp.raise_for_status()
        tokens = [t for t in (resp.json().get("tokens") or []) if _TOKEN_RE.match(str(t))]
        outcome.pending_seen = len(tokens)

        applied: list[str] = []
        async with pool.acquire() as conn:
            for token in tokens:
                try:
                    result = await conn.execute(
                        """
                        UPDATE newsletter_subscribers
                           SET unsubscribed_at = CURRENT_TIMESTAMP,
                               unsubscribe_reason = 'relay',
                               updated_at = CURRENT_TIMESTAMP
                         WHERE unsubscribe_token = $1
                           AND unsubscribed_at IS NULL
                        """,
                        token,
                    )
                    if str(result).strip().endswith(" 1"):
                        outcome.unsubscribed += 1
                    else:
                        # Unknown token, or already unsubscribed. Both are
                        # settled — ack so the queue drains.
                        outcome.already_or_unknown += 1
                    applied.append(token)
                except Exception as exc:  # per-token isolation
                    # The token is NOT logged, not even a prefix. It is the
                    # unsubscribe credential, and these strings become a
                    # finding body that ships to Discord — a wider audience
                    # than a log file. Identifying the row is unnecessary
                    # anyway: a failed token is not added to `applied`, so it
                    # is never acked and the next tick retries it. The cause
                    # is the actionable part.
                    outcome.errors.append(describe_exception(exc))
                    logger.error(
                        "[UNSUB_RELAY] failed to apply a queued unsubscribe "
                        "(will retry next tick): %s",
                        describe_exception(exc), exc_info=True,
                    )

        if applied:
            # Ack AFTER the writes. A crash before this point re-delivers the
            # token next tick; a crash after acking first would lose it.
            ack = await client.post(
                f"{base}/ack", headers=headers, json={"tokens": applied}
            )
            ack.raise_for_status()
            outcome.acked = int(ack.json().get("removed") or 0)

    if outcome.unsubscribed:
        logger.info(
            "[UNSUB_RELAY] applied %d unsubscribe(s) from the relay queue",
            outcome.unsubscribed,
        )
    return outcome
