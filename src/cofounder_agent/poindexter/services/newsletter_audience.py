"""Newsletter audience — the Resend segment is the signup inbox, pulled home.

A newsletter signup starts on the public site, which is served from Vercel,
and has to end in ``newsletter_subscribers`` on the local worker, because that
table is what :mod:`newsletter_service` mails on every publish. The worker has
no public ingress by design, so the site cannot write that table directly.
That is not hypothetical. The site's route used to POST the signup to the
worker through a Tailscale Funnel hostname. The hostname belonged to a node
that was retired, it stopped resolving, and every public signup failed that
leg while the owned table sat at one row (the operator's own 2026-06-03 test).
The route's "fail loud" capture went to a Sentry project that never received
a single public-site event, so nothing noticed.

So the capture and the owned copy are split, the same way the Resend delivery
poll and the Lemon Squeezy invoice poll split them. When a provider looks like
it needs an inbound hook, check whether its API already knows:

    browser ─▶ site route ─▶ Resend  POST /contacts  (segment = resend_audience_id)
                                         │
    SyncNewsletterAudienceJob ──GET /segments/{id}/contacts──┘  (outbound, every 15 min)
                                         │
                                         ▼
                            newsletter_subscribers  (owned copy: tokens, sends)

Resend facts this module relies on, verified against the live API on
2026-09-28 with transient ``@resend.dev`` test contacts (created and then
deleted, nothing sent):

- ``POST /contacts`` is an upsert. A duplicate returns 201 with the SAME id,
  and ``"unsubscribed": false`` in the body clears a prior opt-out. A
  returning subscriber therefore re-consents by signing up again.
- ``segments: [{"id": ...}]`` on create puts the contact in the segment;
  adding it again is also idempotent.
- ``GET /segments/{id}/contacts`` keeps unsubscribed contacts in the listing
  with ``"unsubscribed": true``, so an opt-out made on the Resend side is
  visible to the pull.
- The legacy ``/audiences/{id}/contacts`` endpoints still work but are
  deprecated, and an audience id IS the segment id (the account's one
  audience is listed under both). ``resend_audience_id`` keeps its name
  for that reason; Resend now calls it a segment.

Opt-out precedence is the invariant that matters here, because a mistake
mails someone who asked to leave. The owned table's opt-out always wins. The
sync never clears ``unsubscribed_at``: a contact that is active in Resend but
unsubscribed here stays unsubscribed and is counted as ``opted_out_kept``.
The reverse direction IS applied: a contact marked unsubscribed in Resend
unsubscribes the owned row. The cost is one-sided and deliberate. Someone who
unsubscribes through the relay and later signs up again on the site is not
re-activated by the sync, because a Resend contact carries no timestamp that
separates "signed up again" from "never left". Missing a re-subscribe is the
safe failure; mailing a person who opted out is not.

Owned opt-outs are NOT copied to Resend: an unsubscribe through the relay or
the API updates the owned row only, and the Resend contact stays active.
Nothing sends from the Resend side (the newsletter mails the owned list), so
this is safe as long as nobody sends a Resend Broadcast to the segment.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

RESEND_API_BASE = "https://api.resend.com"

#: api.resend.com sits behind Cloudflare, which 403s ("error code: 1010") on
#: some default library User-Agents; a browser-like UA gets through.
RESEND_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

#: Resend ids are UUID-shaped tokens. The segment id is spliced into a URL
#: path (CodeQL py/partial-ssrf #462), so anything else is a misconfiguration,
#: not a request to shape.
SEGMENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

#: Written to ``unsubscribe_reason`` when the sync applies an opt-out that was
#: made on the Resend side (dashboard edit, Broadcast preference page).
RESEND_UNSUBSCRIBE_REASON = "resend_contact_unsubscribed"

#: ``newsletter_subscribers`` column widths: email varchar(255), names
#: varchar(100). An over-long value would raise DataError mid-sync.
_EMAIL_MAX = 255
_NAME_MAX = 100
#: Deliberately loose: Resend has already validated the address. This only
#: keeps a malformed value out of a table the newsletter mails.
_EMAIL_SHAPE_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Resend's default API rate limit is a few requests per second per team.
#: Paging a large segment back to back trips it, so the pull spaces pages out
#: and honours ``Retry-After`` on a 429.
_PAGE_INTERVAL_S = 0.6
_RATE_LIMIT_RETRIES = 3
_RETRY_AFTER_CAP_S = 10.0

Pause = Callable[[float], Awaitable[Any]]


class AudienceConfigError(ValueError):
    """``resend_audience_id`` is set but cannot be used as a segment id."""


def mint_unsubscribe_token() -> str:
    """Per-subscriber unsubscribe credential.

    ``secrets.token_urlsafe(32)`` is 43 base64url chars, about 256 bits of
    entropy. The unsubscribe endpoint and the edge relay both look up by token,
    so an attacker has to guess a real token to unsubscribe anyone whose link
    they do not already hold. The UNIQUE index on the column and the rate
    limits make that operationally infeasible.
    """
    return secrets.token_urlsafe(32)


def resolve_segment_id(site_config: SiteConfig) -> str | None:
    """The Resend segment signups land in, or ``None`` when unconfigured.

    Raises :class:`AudienceConfigError` when ``resend_audience_id`` is set to
    something that is not a plain id token. That value would otherwise be
    spliced into a URL path.
    """
    raw = (site_config.get("resend_audience_id", "") or "").strip()
    if not raw:
        return None
    if not SEGMENT_ID_RE.fullmatch(raw):
        raise AudienceConfigError(
            f"resend_audience_id {raw[:40]!r} is not a plain Resend id token"
        )
    return raw


def canary_email(site_config: SiteConfig) -> str:
    """The signup canary's address (lower-cased), or ``""`` when unset.

    Read here as well as by the canary because the sync and the delivery poll
    must both ignore it: the canary signs up once a day and is not a reader.
    """
    return (site_config.get("newsletter_signup_canary_email", "") or "").strip().lower()


def resend_headers(api_key: str) -> dict[str, str]:
    """Auth + the Cloudflare-safe User-Agent for every Resend call."""
    return {"Authorization": f"Bearer {api_key}", "User-Agent": RESEND_USER_AGENT}


async def upsert_contact(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    segment_id: str,
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
) -> httpx.Response:
    """Create or refresh a contact and put it in ``segment_id``.

    ``POST /contacts`` is an upsert (see the module docstring), and
    ``"unsubscribed": false`` is sent on purpose: every caller is a fresh
    signup, which is fresh consent.
    """
    contact: dict[str, Any] = {
        "email": email,
        "unsubscribed": False,
        "segments": [{"id": segment_id}],
    }
    if first_name:
        contact["first_name"] = first_name
    if last_name:
        contact["last_name"] = last_name
    return await client.post(
        f"{RESEND_API_BASE}/contacts", headers=resend_headers(api_key), json=contact
    )


async def mirror_signup_to_segment(
    site_config: SiteConfig,
    *,
    email: str,
    first_name: str | None,
    last_name: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Best-effort copy of a direct API signup into the Resend segment.

    ``POST /api/newsletter/subscribe`` on the worker writes the owned row
    first; this keeps the segment complete so Resend-side tooling (Broadcasts,
    the dashboard) sees every subscriber. NEVER raises: the owned row is the
    system of record, and a Resend hiccup must not fail the signup.
    """
    try:
        segment_id = resolve_segment_id(site_config)
    except AudienceConfigError as exc:
        logger.warning("[newsletter] %s; skipping the segment mirror", exc)
        return
    if segment_id is None:
        return
    api_key = (await site_config.get_secret("resend_api_key", "")) or ""
    if not api_key:
        logger.warning(
            "[newsletter] resend_audience_id is set but resend_api_key is "
            "missing; skipping the segment mirror"
        )
        return
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            resp = await upsert_contact(
                client,
                api_key,
                segment_id=segment_id,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
        if resp.status_code >= 400:
            logger.warning(
                "[newsletter] Resend contact upsert failed: HTTP %s %s",
                resp.status_code, resp.text[:200],
            )
        else:
            logger.info("[newsletter] mirrored a signup into the Resend segment")
    except Exception as exc:  # noqa: BLE001 — best-effort, never fails a signup
        logger.warning(
            "[newsletter] Resend contact upsert error: %s", describe_exception(exc)
        )


async def _get_with_rate_limit(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
    pause: Pause,
) -> httpx.Response:
    """GET, waiting out up to ``_RATE_LIMIT_RETRIES`` 429s."""
    resp = await client.get(url, headers=headers, params=params)
    for _ in range(_RATE_LIMIT_RETRIES):
        if resp.status_code != 429:
            break
        try:
            wait = float(resp.headers.get("retry-after") or 1.0)
        except ValueError:
            wait = 1.0
        await pause(min(max(wait, 0.0), _RETRY_AFTER_CAP_S))
        resp = await client.get(url, headers=headers, params=params)
    return resp


async def list_segment_contacts(
    client: httpx.AsyncClient,
    api_key: str,
    segment_id: str,
    *,
    max_pages: int,
    page_size: int = 100,
    pause: Pause = asyncio.sleep,
) -> tuple[list[dict[str, Any]], bool]:
    """Every contact in the segment, following ``after`` cursors.

    Returns ``(contacts, truncated)``. ``truncated`` is True when the page cap
    ran out while Resend still reported ``has_more``. The cap bounds a
    ``has_more`` loop against a provider this code does not control, and a
    truncated read must be reported, never passed off as the whole segment.
    """
    url = f"{RESEND_API_BASE}/segments/{segment_id}/contacts"
    headers = resend_headers(api_key)
    contacts: list[dict[str, Any]] = []
    after: str | None = None
    for page in range(max(1, max_pages)):
        if page:
            await pause(_PAGE_INTERVAL_S)
        params: dict[str, Any] = {"limit": page_size}
        if after:
            params["after"] = after
        resp = await _get_with_rate_limit(
            client, url, headers=headers, params=params, pause=pause
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or []
        contacts.extend(data)
        if not body.get("has_more") or not data:
            return contacts, False
        after = data[-1].get("id")
        if not after:
            # has_more with no cursor to follow: stop, and say so.
            return contacts, True
    return contacts, True


@dataclass
class AudienceSyncOutcome:
    """What one pull saw and what it did (or, dry-run, would do)."""

    segment_id: str | None = None
    dry_run: bool = False
    contacts_seen: int = 0
    imported: int = 0
    already_subscribed: int = 0
    already_unsubscribed: int = 0
    opted_out_kept: int = 0
    unsubscribes_applied: int = 0
    skipped_unsubscribed: int = 0
    skipped_invalid: int = 0
    skipped_canary: int = 0
    truncated: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def changes(self) -> int:
        return self.imported + self.unsubscribes_applied

    def as_metrics(self) -> dict[str, Any]:
        return {
            "contacts_seen": self.contacts_seen,
            "imported": self.imported,
            "already_subscribed": self.already_subscribed,
            "already_unsubscribed": self.already_unsubscribed,
            "opted_out_kept": self.opted_out_kept,
            "unsubscribes_applied": self.unsubscribes_applied,
            "skipped_unsubscribed": self.skipped_unsubscribed,
            "skipped_invalid": self.skipped_invalid,
            "skipped_canary": self.skipped_canary,
            "truncated": self.truncated,
            "dry_run": self.dry_run,
            "errors": len(self.errors),
        }

    def summary(self) -> str:
        verb = "would import" if self.dry_run else "imported"
        return (
            f"{self.contacts_seen} contact(s) in the segment: {verb} "
            f"{self.imported}, {self.already_subscribed} already subscribed, "
            f"{self.unsubscribes_applied} Resend opt-out(s) applied, "
            f"{self.opted_out_kept} kept unsubscribed"
        )


def parse_resend_timestamp(value: Any) -> datetime | None:
    """Resend's ``created_at`` (``2026-09-28 15:58:09.965043+00``) as a tz-aware datetime.

    asyncpg binds a timestamptz from a ``datetime`` only, never a string. An
    unparseable value returns ``None`` and the column default (now) applies.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


async def _apply_contact(
    conn: Any,
    contact: dict[str, Any],
    outcome: AudienceSyncOutcome,
    *,
    canary: str,
    dry_run: bool,
) -> None:
    """Reconcile one Resend contact against ``newsletter_subscribers``."""
    email = str(contact.get("email") or "").strip()
    if not email or len(email) > _EMAIL_MAX or not _EMAIL_SHAPE_RE.match(email):
        outcome.skipped_invalid += 1
        return
    if canary and email.lower() == canary:
        outcome.skipped_canary += 1
        return

    # Case-insensitive: the UNIQUE constraint is on the raw column, so
    # Foo@x.com and foo@x.com would otherwise land as two subscribers.
    existing = await conn.fetchrow(
        """
        SELECT id, unsubscribed_at FROM newsletter_subscribers
         WHERE lower(email) = lower($1)
         ORDER BY id
         LIMIT 1
        """,
        email,
    )

    if contact.get("unsubscribed"):
        if existing is None:
            outcome.skipped_unsubscribed += 1
        elif existing["unsubscribed_at"] is not None:
            outcome.already_unsubscribed += 1
        elif dry_run:
            outcome.unsubscribes_applied += 1
        else:
            result = await conn.execute(
                """
                UPDATE newsletter_subscribers
                   SET unsubscribed_at = CURRENT_TIMESTAMP,
                       unsubscribe_reason = $2,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = $1
                   AND unsubscribed_at IS NULL
                """,
                existing["id"],
                RESEND_UNSUBSCRIBE_REASON,
            )
            if str(result).strip().endswith(" 1"):
                outcome.unsubscribes_applied += 1
            else:
                outcome.already_unsubscribed += 1
        return

    if existing is not None:
        # The owned opt-out wins. Never re-subscribe from here (module docstring).
        if existing["unsubscribed_at"] is not None:
            outcome.opted_out_kept += 1
        else:
            outcome.already_subscribed += 1
        return

    if dry_run:
        outcome.imported += 1
        return

    # Same row shape as a direct signup through the worker route: verified on
    # signup, no double opt-in, a fresh unsubscribe credential. ip_address and
    # user_agent stay NULL; the worker never saw the visitor's request.
    result = await conn.execute(
        """
        INSERT INTO newsletter_subscribers
            (email, first_name, last_name, subscribed_at, verified, unsubscribe_token)
        VALUES ($1, $2, $3, COALESCE($4, CURRENT_TIMESTAMP), TRUE, $5)
        ON CONFLICT (email) DO NOTHING
        """,
        email,
        _clip(contact.get("first_name"), _NAME_MAX),
        _clip(contact.get("last_name"), _NAME_MAX),
        parse_resend_timestamp(contact.get("created_at")),
        mint_unsubscribe_token(),
    )
    if str(result).strip().endswith(" 1"):
        outcome.imported += 1
    else:
        # Lost a race with a concurrent writer for the same address.
        outcome.already_subscribed += 1


async def sync_segment_to_subscribers(
    pool: Any,
    site_config: SiteConfig,
    *,
    dry_run: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    pause: Pause = asyncio.sleep,
) -> AudienceSyncOutcome:
    """Pull the Resend segment into ``newsletter_subscribers``.

    Raises :class:`AudienceConfigError` for an unusable ``resend_audience_id``
    and lets a failed Resend read propagate: both mean nothing was synced,
    which the caller reports. Per-contact failures are isolated and collected
    in ``outcome.errors`` so one bad row cannot strand the rest.
    """
    outcome = AudienceSyncOutcome(dry_run=dry_run)
    segment_id = resolve_segment_id(site_config)
    if segment_id is None:
        return outcome
    outcome.segment_id = segment_id

    api_key = (await site_config.get_secret("resend_api_key", "")) or ""
    if not api_key:
        outcome.errors.append("resend_api_key is not set")
        return outcome

    max_pages = site_config.get_int("newsletter_audience_sync_max_pages", 50)
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        contacts, truncated = await list_segment_contacts(
            client, api_key, segment_id, max_pages=max_pages, pause=pause
        )
    outcome.contacts_seen = len(contacts)
    outcome.truncated = truncated
    if truncated:
        outcome.errors.append(
            f"stopped after {max_pages} page(s) with contacts still unread; "
            "raise newsletter_audience_sync_max_pages"
        )

    canary = canary_email(site_config)
    async with pool.acquire() as conn:
        for contact in contacts:
            try:
                await _apply_contact(
                    conn, contact, outcome, canary=canary, dry_run=dry_run
                )
            except Exception as exc:  # per-contact isolation
                # The address is not in the error: these strings become a
                # finding body that ships to Discord. The Resend contact id is
                # enough to find the row.
                outcome.errors.append(
                    f"contact {contact.get('id')}: {describe_exception(exc)}"
                )
                logger.error(
                    "[newsletter_audience] failed to reconcile contact %s: %s",
                    contact.get("id"), describe_exception(exc), exc_info=True,
                )

    if outcome.changes:
        logger.info("[newsletter_audience] %s", outcome.summary())
    return outcome
