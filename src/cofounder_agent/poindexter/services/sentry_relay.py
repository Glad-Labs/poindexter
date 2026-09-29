"""Drain the edge error relay into the local GlitchTip.

A browser on the public site, or one of the site's serverless functions,
cannot reach GlitchTip: the tracker is LAN-only and this worker has no public
ingress. So the Sentry SDK there tunnels each envelope to a Cloudflare Worker
(``infrastructure/cloudflare/sentry-relay``), which checks it and queues it in
D1. This module is the other half, an outbound poll that pulls the queue,
posts each envelope to GlitchTip's ingest over the LAN, and acks what landed.
It is the same split as the unsubscribe relay and the Lemon Squeezy
custom_data relay.

GlitchTip reads the DSN key only from ``?sentry_key=`` or ``X-Sentry-Auth``
(``apps/event_ingest/authentication.py::auth_from_request``). A tunnelled
envelope carries its DSN only in the envelope header, so a raw forward is
answered ``403 Denied``. The Worker stored the key beside the envelope, and
it goes back on as a query parameter here.

What happens to each envelope, decided by GlitchTip's answer:

* **2xx**: GlitchTip has it, so ack.
* **4xx other than 429**: a verdict about the envelope (wrong key, unknown
  project, malformed payload). Retrying cannot change it, so ack and count it
  as rejected. The job reports rejections, because a key that stopped
  matching turns every future error into one.
* **429, 5xx, or no answer**: GlitchTip is unavailable. Leave the envelope
  queued and end the pass; the next tick retries. The Worker's retention
  window bounds how long that can go on, and whatever it prunes comes back
  here as ``expired`` rather than disappearing.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

#: Where GlitchTip listens on the compose network. The brain's triage probe
#: reads the same key.
GLITCHTIP_BASE_URL_DEFAULT = "http://glitchtip-web:8000"

_DEFAULT_BATCH_SIZE = 25
_DEFAULT_MAX_BATCHES = 20

# Mirrors the Worker's own checks. The Worker already refused anything else,
# but these values become a URL path segment and a query parameter here, so
# they are checked again at the point of use.
_PROJECT_ID_RE = re.compile(r"[1-9][0-9]{0,9}")
_PUBLIC_KEY_RE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)

#: How many rejection / deferral reasons a finding body carries.
_SAMPLE_LIMIT = 5


@dataclass
class DrainOutcome:
    """What one drain pass saw and did."""

    configured: bool = False
    pulled: int = 0
    forwarded: int = 0
    rejected: int = 0
    deferred: int = 0
    acked: int = 0
    backlog: int = 0
    expired: int = 0
    rejections: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "configured": int(self.configured),
            "pulled": self.pulled,
            "forwarded": self.forwarded,
            "rejected": self.rejected,
            "deferred": self.deferred,
            "acked": self.acked,
            "backlog": self.backlog,
            "expired": self.expired,
            "errors": len(self.errors),
        }


def _positive_int(raw: Any, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


async def _forward(
    client: httpx.AsyncClient, glitchtip: str, row: dict[str, Any]
) -> tuple[str, str]:
    """Post one queued envelope to GlitchTip.

    Returns ``(verdict, reason)`` where verdict is ``forwarded``,
    ``rejected`` or ``deferred``. The reason never includes the envelope
    body: it holds a visitor's error report, and the reason can end up in a
    Discord message.
    """
    project_id = str(row.get("project_id") or "")
    public_key = str(row.get("public_key") or "")
    if not _PROJECT_ID_RE.fullmatch(project_id) or not _PUBLIC_KEY_RE.fullmatch(public_key):
        return "rejected", "malformed project id or public key"
    try:
        body = base64.b64decode(str(row.get("body") or ""), validate=True)
    except (binascii.Error, ValueError):
        return "rejected", "body is not valid base64"
    if not body:
        return "rejected", "empty body"

    try:
        resp = await client.post(
            f"{glitchtip}/api/{project_id}/envelope/",
            params={"sentry_key": public_key, "sentry_version": "7"},
            content=body,
            headers={"Content-Type": "application/x-sentry-envelope"},
        )
    except httpx.HTTPError as exc:
        return "deferred", f"GlitchTip unreachable: {describe_exception(exc)}"

    status = resp.status_code
    if 200 <= status < 300:
        return "forwarded", ""
    if status == 429 or status >= 500:
        return "deferred", f"GlitchTip answered {status}"
    return "rejected", f"GlitchTip answered {status} for project {project_id}: {resp.text[:200]}"


async def drain_sentry_relay(
    site_config: SiteConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DrainOutcome:
    """Pull queued envelopes from the relay, forward them, ack what is settled."""
    outcome = DrainOutcome()

    base = (site_config.get("sentry_relay_url", "") or "").strip().rstrip("/")
    if not base:
        # Not an error: the relay is opt-in. Nothing can be queued for an
        # install that never pointed a site at it.
        return outcome
    outcome.configured = True
    secret = await site_config.get_secret("sentry_relay_secret", "")
    if not secret:
        outcome.errors.append("sentry_relay_secret is not set")
        return outcome

    glitchtip = (
        (site_config.get("glitchtip_base_url", "") or "").strip().rstrip("/")
        or GLITCHTIP_BASE_URL_DEFAULT
    )
    batch_size = _positive_int(
        site_config.get("sentry_relay_drain_batch_size", str(_DEFAULT_BATCH_SIZE)),
        _DEFAULT_BATCH_SIZE,
    )
    max_batches = _positive_int(
        site_config.get("sentry_relay_drain_max_batches", str(_DEFAULT_MAX_BATCHES)),
        _DEFAULT_MAX_BATCHES,
    )
    relay_headers = {"Authorization": f"Bearer {secret}"}

    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        for _ in range(max_batches):
            resp = await client.get(
                f"{base}/pending", params={"limit": batch_size}, headers=relay_headers
            )
            resp.raise_for_status()
            page = resp.json()
            rows = [r for r in (page.get("envelopes") or []) if isinstance(r, dict)]
            outcome.expired += _positive_int(page.get("expired"), 0)
            backlog = _positive_int(page.get("backlog"), 0)
            outcome.backlog = backlog
            if not rows:
                break
            outcome.pulled += len(rows)

            settled: list[int] = []
            glitchtip_down = False
            for row in rows:
                row_id = row.get("id")
                verdict, reason = await _forward(client, glitchtip, row)
                if verdict == "deferred":
                    outcome.deferred += 1
                    outcome.errors.append(reason)
                    glitchtip_down = True
                    # Everything after this row would meet the same outage.
                    break
                if isinstance(row_id, int):
                    settled.append(row_id)
                if verdict == "forwarded":
                    outcome.forwarded += 1
                else:
                    outcome.rejected += 1
                    if len(outcome.rejections) < _SAMPLE_LIMIT:
                        outcome.rejections.append(reason)
                    logger.warning(
                        "[SENTRY_RELAY] dropped queued envelope %s: %s", row_id, reason
                    )

            if settled:
                # Ack AFTER GlitchTip answered. A crash before this line
                # re-delivers the envelope next tick, and GlitchTip dedupes a
                # repeated event_id; acking first would lose it.
                ack = await client.post(
                    f"{base}/ack", headers=relay_headers, json={"ids": settled}
                )
                ack.raise_for_status()
                acked = _positive_int(ack.json().get("removed"), 0)
                outcome.acked += acked
                outcome.backlog = max(0, backlog - acked)

            if glitchtip_down or len(rows) < batch_size:
                break

    if outcome.forwarded:
        logger.info(
            "[SENTRY_RELAY] forwarded %d envelope(s) to GlitchTip, %d still queued",
            outcome.forwarded, outcome.backlog,
        )
    return outcome
