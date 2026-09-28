"""Newsletter signup canary — prove the public form still captures signups.

The signup path has failed silently twice. Before 2026-06-03 the site route
saved addresses nowhere (a send-only Resend key and an unset audience) while
still sending a welcome email. After that, its backend leg POSTed to a funnel
hostname that stopped resolving when the node behind it was retired. Both
times the route "failed loud" into telemetry nobody read: Vercel keeps
runtime logs for a day, and the Sentry capture had no project that ever
received a public-site event.

A low-traffic newsletter makes "no new subscribers" and "the form is broken"
look identical from the owned table, so watching the table cannot tell them
apart. This canary exercises the real path instead:

1. POST a Resend test address to the public signup endpoint, exactly as the
   form does (``newsletter_signup_canary_url``).
2. Ask Resend, with the WORKER's key, whether that contact is now in the
   segment the worker syncs (``resend_audience_id``). That checks the
   property the business needs end to end: what the site captures, the
   worker can see. A 200 from the route with the contact in a different
   Resend team or segment fails here, not silently.
3. Delete the contact. The canary is not a reader.

The address defaults to ``delivered+signup-canary@resend.dev``, one of
Resend's own test inboxes. Mail to it costs one send against the quota and
does not touch domain reputation. The route sends it a welcome email, which
:mod:`resend_delivery` skips so a daily canary cannot keep ``subscriber_events``
looking fresh while real sends are dead. :mod:`newsletter_audience` skips it
too, in case a failed cleanup leaves it in the segment.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from poindexter.services.newsletter_audience import (
    RESEND_API_BASE,
    Pause,
    canary_email,
    resend_headers,
    resolve_segment_id,
)
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

#: Tighter than the sync's shape check: this value is spliced into Resend URL
#: paths, so it must be a plain address.
_CANARY_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

#: Resend is read-your-writes in practice (the 2026-09-28 API check saw a new
#: contact's segment immediately), so a short bounded wait is plenty.
_VERIFY_ATTEMPTS = 3
_VERIFY_INTERVAL_S = 2.0


class CanaryConfigError(ValueError):
    """The canary is switched on but cannot run as configured."""


@dataclass
class SignupCanaryOutcome:
    """One canary run. ``problem`` is the operator-facing cause, or None."""

    url: str = ""
    segment_id: str = ""
    attempts: int = 0
    http_status: int | None = None
    route_ok: bool = False
    in_segment: bool = False
    cleaned_up: bool = False
    latency_ms: int = 0
    problem: str | None = None

    @property
    def healthy(self) -> bool:
        return self.route_ok and self.in_segment

    def as_metrics(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "route_ok": self.route_ok,
            "in_segment": self.in_segment,
            "cleaned_up": self.cleaned_up,
            "http_status": self.http_status,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
        }


def _canary_url(site_config: SiteConfig) -> str:
    return (site_config.get("newsletter_signup_canary_url", "") or "").strip()


def _route_detail(resp: httpx.Response) -> tuple[bool, str]:
    """(captured, detail) from the signup endpoint's JSON contract."""
    try:
        body = resp.json()
    except ValueError:
        return False, resp.text[:200]
    if not isinstance(body, dict):
        return False, str(body)[:200]
    captured = resp.status_code < 300 and body.get("success") is True
    detail = str(body.get("detail") or body.get("message") or "")[:200]
    return captured, detail


async def run_signup_canary(
    site_config: SiteConfig,
    *,
    url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    pause: Pause = asyncio.sleep,
) -> SignupCanaryOutcome | None:
    """Run the canary once. ``None`` means it is switched off (no URL).

    ``url`` overrides ``newsletter_signup_canary_url`` for a one-off run (the
    CLI's ``--url``), e.g. to check a preview deployment before switching the
    daily canary on.

    Raises :class:`CanaryConfigError` when switched on but misconfigured, so
    the caller can report a canary that cannot run as loudly as one that
    fails.
    """
    url = (url or "").strip() or _canary_url(site_config)
    if not url:
        return None
    if urlparse(url).scheme not in ("http", "https"):
        raise CanaryConfigError(f"canary URL {url[:80]!r} is not an http(s) URL")
    email = canary_email(site_config)
    if not _CANARY_EMAIL_RE.fullmatch(email):
        raise CanaryConfigError(
            "newsletter_signup_canary_email must be a plain address; use a "
            "Resend test inbox such as delivered+signup-canary@resend.dev"
        )
    try:
        segment_id = resolve_segment_id(site_config)
    except ValueError as exc:
        raise CanaryConfigError(str(exc)) from exc
    if segment_id is None:
        raise CanaryConfigError(
            "resend_audience_id is not set, so there is no segment to check "
            "the capture against"
        )
    api_key = (await site_config.get_secret("resend_api_key", "")) or ""
    if not api_key:
        raise CanaryConfigError("resend_api_key is not set")

    attempts = max(1, site_config.get_int("newsletter_signup_canary_attempts", 2))
    retry_s = max(0.0, site_config.get_float("newsletter_signup_canary_retry_seconds", 30.0))
    outcome = SignupCanaryOutcome(url=url, segment_id=segment_id)
    contact_path = f"{RESEND_API_BASE}/contacts/{quote(email, safe='@+')}"

    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        try:
            detail = ""
            for attempt in range(1, attempts + 1):
                outcome.attempts = attempt
                started = time.monotonic()
                try:
                    resp = await client.post(
                        url,
                        json={"email": email, "first_name": "Canary"},
                        headers={"User-Agent": "poindexter-signup-canary"},
                    )
                    outcome.http_status = resp.status_code
                    outcome.route_ok, detail = _route_detail(resp)
                except httpx.HTTPError as exc:
                    outcome.http_status = None
                    outcome.route_ok = False
                    detail = describe_exception(exc)
                outcome.latency_ms = int((time.monotonic() - started) * 1000)
                if outcome.route_ok or attempt == attempts:
                    break
                await pause(retry_s)

            if not outcome.route_ok:
                status = (
                    f"HTTP {outcome.http_status}"
                    if outcome.http_status is not None
                    else "no response"
                )
                outcome.problem = (
                    f"the signup endpoint did not capture the canary ({status}"
                    f"{': ' + detail if detail else ''}) after {outcome.attempts} "
                    "attempt(s). Every real signup is failing the same way."
                )
                return outcome

            for check in range(_VERIFY_ATTEMPTS):
                if check:
                    await pause(_VERIFY_INTERVAL_S)
                seg = await client.get(
                    f"{contact_path}/segments", headers=resend_headers(api_key)
                )
                if seg.status_code == 200:
                    ids = {s.get("id") for s in (seg.json().get("data") or [])}
                    if segment_id in ids:
                        outcome.in_segment = True
                        break
            if not outcome.in_segment:
                outcome.problem = (
                    "the signup endpoint answered success, but the canary is not "
                    f"in Resend segment {segment_id} (resend_audience_id), so the "
                    "worker will never sync it. The site's RESEND_API_KEY and "
                    "RESEND_AUDIENCE_ID must point at the same Resend team and "
                    "segment as the worker's resend_api_key and resend_audience_id."
                )
            return outcome
        finally:
            # Always attempt cleanup: a route that half-worked may still have
            # created the contact. 404 means there was nothing to remove.
            try:
                deleted = await client.delete(
                    contact_path, headers=resend_headers(api_key)
                )
                outcome.cleaned_up = deleted.status_code in (200, 404)
                if not outcome.cleaned_up:
                    logger.warning(
                        "[signup_canary] could not delete the canary contact: "
                        "HTTP %s %s", deleted.status_code, deleted.text[:200],
                    )
            except httpx.HTTPError as exc:
                logger.warning(
                    "[signup_canary] could not delete the canary contact: %s",
                    describe_exception(exc),
                )
