"""Pro subscription delivery — Lemon Squeezy poll → GitHub collaborator sync.

Closes the pay→deliver gap (glad-labs-stack#3216): a Poindexter Pro purchase
must end in repo access with zero operator action, and a lapsed subscription
must lose that access again — automatically in both directions.

Why POLL instead of receiving webhooks: the worker has no public ingress by
design (local-first; Vercel functions can't reach the LAN, the Tailscale
Funnel is retired), so the existing ``POST /api/webhooks/lemon-squeezy``
route — kept, but no longer load-bearing — can never hear a live event. A
poll from inside the network needs no ingress and reconciles after downtime,
where a missed webhook is simply lost. If a public webhook relay ever gets
built, the order-id-keyed ``revenue_events`` dedup here must be reconciled
with the webhook handler's ``webhook_id``-keyed rows first.

Access policy (also documented in docs/operations/pro-delivery.md):

- ``on_trial`` / ``active`` / ``past_due`` / ``cancelled`` → access.
  ``cancelled`` keeps access because Lemon Squeezy holds a cancelled
  subscription in that status until ``ends_at`` passes (the buyer paid
  through the period), then flips it to ``expired`` — which is when we
  revoke. ``past_due`` rides the dunning window rather than punishing a
  card hiccup.
- ``expired`` / ``unpaid`` / ``paused`` → no access. "Keep what you
  downloaded" still applies; access to future updates does not.

Safety invariant: the sync is ROW-driven, never set-driven. It only ever
invites or removes the GitHub usernames recorded in ``pro_subscriptions``,
so it cannot touch the operator's own account or collaborators added by
hand for unrelated reasons.

GitHub username resolution: the storefront passes the buyer's username via
``checkout[custom][github_username]`` on the buy URL. The sync looks for
that custom data on the subscription and its order; when the LS API doesn't
expose it, delivery degrades to a one-command manual step — a
``pro_delivery_action_needed`` finding pings the operator with
``poindexter pro link <subscription> <github-username>``. Operator-set
usernames always win; the sync never overwrites a non-NULL value.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from utils.findings import emit_finding

logger = logging.getLogger(__name__)

LS_API_BASE = "https://api.lemonsqueezy.com/v1"
GITHUB_API_BASE = "https://api.github.com"

# LS lifecycle statuses that grant access vs. revoke it. Anything unknown
# (a status LS adds later) deliberately falls in NEITHER set: the row is
# recorded and surfaced, but no GitHub mutation fires until the policy here
# names it — safer than guessing on an unmodeled state.
ACCESS_STATUSES = frozenset({"on_trial", "active", "past_due", "cancelled"})
REVOKE_STATUSES = frozenset({"expired", "unpaid", "paused"})

# GitHub username: 1-39 alphanumerics/hyphens, no leading/trailing/double
# hyphen (GitHub's own rule).
_GITHUB_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}$")

# Keys probed inside LS checkout custom_data payloads.
_CUSTOM_DATA_KEYS = ("github_username", "github", "gh_username")


class ProDeliveryConfigError(RuntimeError):
    """Raised when pro delivery is invoked without required config.

    The message names every missing piece at once (fail loud + actionable)
    rather than failing one key at a time.
    """


@dataclass
class SyncOutcome:
    """What one sync pass observed and did."""

    subscriptions_seen: int = 0
    invited: list[str] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    missing_username: list[str] = field(default_factory=list)
    revenue_rows: int = 0
    errors: list[str] = field(default_factory=list)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "subscriptions_seen": self.subscriptions_seen,
            "invited": len(self.invited),
            "revoked": len(self.revoked),
            "missing_username": len(self.missing_username),
            "revenue_rows": self.revenue_rows,
            "errors": len(self.errors),
        }


@dataclass
class _Config:
    ls_api_key: str
    github_token: str
    repo: str  # "owner/name"
    permission: str
    store_id: str
    product_id: str


def normalize_github_username(raw: str | None) -> str | None:
    """Normalize operator/buyer input to a bare GitHub username, or None.

    Accepts ``@name``, ``github.com/name`` / full profile URLs, and stray
    whitespace; rejects anything that isn't a valid username after
    normalization (returning None so callers treat it as missing rather
    than inviting a typo).
    """
    if not raw:
        return None
    name = str(raw).strip().strip("/")
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    name = name.lstrip("@").strip()
    if not name or not _GITHUB_USERNAME_RE.match(name):
        return None
    return name


def _parse_ts(raw: Any) -> datetime | None:
    """Parse an LS ISO-8601 timestamp to an aware datetime.

    asyncpg refuses str binds on timestamptz columns, so timestamps MUST
    cross the driver boundary as datetimes (memory:
    reference_asyncpg_timestamptz_str_bind).
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_github_username(*sources: Any) -> str | None:
    """Hunt checkout custom_data for a GitHub username across payloads.

    LS documents custom data on webhook payloads; whether the REST API
    exposes it on subscription/order attributes has to be observed live,
    so this probes every plausible carrier and the caller degrades to the
    manual ``pro link`` path when nothing is found.
    """
    for src in sources:
        if not isinstance(src, dict):
            continue
        for carrier in (src.get("custom_data"), src.get("checkout_data")):
            if not isinstance(carrier, dict):
                continue
            for key in _CUSTOM_DATA_KEYS:
                name = normalize_github_username(carrier.get(key))
                if name:
                    return name
    return None


class ProDeliveryService:
    """LS subscription poll + row-driven GitHub collaborator reconciler."""

    def __init__(
        self,
        *,
        pool: Any,
        site_config: Any,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._pool = pool
        self._site_config = site_config
        # Injectable transport so tests run against httpx.MockTransport
        # instead of monkeypatching the client (mirrors the finance-module
        # Mercury client's test seam).
        self._transport = transport

    # -- config ------------------------------------------------------------

    async def _resolve_config(self) -> _Config:
        sc = self._site_config
        ls_api_key = await sc.get_secret("lemon_squeezy_api_key", "")
        github_token = await sc.get_secret("pro_delivery_github_token", "")
        repo = (sc.get("pro_delivery_github_repo", "") or "").strip()

        missing: list[str] = []
        if not ls_api_key:
            missing.append(
                "secret lemon_squeezy_api_key (Lemon Squeezy → Settings → API)"
            )
        if not github_token:
            missing.append(
                "secret pro_delivery_github_token (fine-grained PAT, single repo, "
                "Administration: read+write)"
            )
        if not repo or "/" not in repo:
            missing.append(
                "pro_delivery_github_repo (owner/name, e.g. Glad-Labs/poindexter-pro)"
            )
        if missing:
            raise ProDeliveryConfigError(
                "pro delivery is not configured — set: " + "; ".join(missing)
            )

        return _Config(
            ls_api_key=ls_api_key,
            github_token=github_token,
            repo=repo,
            permission=(sc.get("pro_delivery_github_permission", "pull") or "pull"),
            store_id=(sc.get("pro_delivery_ls_store_id", "") or "").strip(),
            product_id=(sc.get("pro_delivery_ls_product_id", "") or "").strip(),
        )

    # -- HTTP helpers ------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=30.0)

    @staticmethod
    def _ls_headers(cfg: _Config) -> dict[str, str]:
        return {
            "Accept": "application/vnd.api+json",
            "Authorization": f"Bearer {cfg.ls_api_key}",
        }

    @staticmethod
    def _gh_headers(cfg: _Config) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {cfg.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "poindexter-pro-delivery",
        }

    async def _fetch_subscriptions(
        self, client: httpx.AsyncClient, cfg: _Config
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Fetch all LS subscriptions (+ included orders) across pages.

        Returns ``(subscriptions, orders_by_id)``. ``include=order`` rides
        along so custom_data/total lookups don't need one GET per sub.
        """
        subs: list[dict[str, Any]] = []
        orders: dict[str, dict[str, Any]] = {}
        url: str | None = f"{LS_API_BASE}/subscriptions"
        params: dict[str, str] | None = {"page[size]": "100", "include": "order"}
        if cfg.store_id:
            params["filter[store_id]"] = cfg.store_id
        if cfg.product_id:
            params["filter[product_id]"] = cfg.product_id
        while url:
            resp = await client.get(url, params=params, headers=self._ls_headers(cfg))
            resp.raise_for_status()
            body = resp.json()
            subs.extend(body.get("data") or [])
            for inc in body.get("included") or []:
                if inc.get("type") == "orders" and inc.get("id") is not None:
                    orders[str(inc["id"])] = inc.get("attributes") or {}
            # JSON:API pagination — links.next is absolute and already
            # carries page+filter params.
            url = (body.get("links") or {}).get("next")
            params = None
        return subs, orders

    async def _github_invite(
        self, client: httpx.AsyncClient, cfg: _Config, username: str
    ) -> None:
        """Idempotently ensure ``username`` is invited (201) or already a
        collaborator (204)."""
        resp = await client.put(
            f"{GITHUB_API_BASE}/repos/{cfg.repo}/collaborators/{username}",
            json={"permission": cfg.permission},
            headers=self._gh_headers(cfg),
        )
        if resp.status_code not in (201, 204):
            raise RuntimeError(
                f"GitHub invite for {username} failed: "
                f"{resp.status_code} {resp.text[:200]}"
            )

    async def _github_revoke(
        self,
        client: httpx.AsyncClient,
        cfg: _Config,
        username: str,
        invitations: list[dict[str, Any]],
    ) -> None:
        """Remove ``username``'s access: cancel a pending invitation if one
        exists, then drop the collaborator entry (404 = already gone)."""
        for inv in invitations:
            invitee = (inv.get("invitee") or {}).get("login") or ""
            if invitee.lower() == username.lower():
                resp = await client.delete(
                    f"{GITHUB_API_BASE}/repos/{cfg.repo}/invitations/{inv.get('id')}",
                    headers=self._gh_headers(cfg),
                )
                if resp.status_code not in (204, 404):
                    raise RuntimeError(
                        f"GitHub invitation cancel for {username} failed: "
                        f"{resp.status_code} {resp.text[:200]}"
                    )
        resp = await client.delete(
            f"{GITHUB_API_BASE}/repos/{cfg.repo}/collaborators/{username}",
            headers=self._gh_headers(cfg),
        )
        if resp.status_code not in (204, 404):
            raise RuntimeError(
                f"GitHub collaborator removal for {username} failed: "
                f"{resp.status_code} {resp.text[:200]}"
            )

    async def _github_pending_invitations(
        self, client: httpx.AsyncClient, cfg: _Config
    ) -> list[dict[str, Any]]:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{cfg.repo}/invitations",
            params={"per_page": "100"},
            headers=self._gh_headers(cfg),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    # -- DB helpers --------------------------------------------------------

    @staticmethod
    async def _upsert_row(
        conn: Any, sub: dict[str, Any], username_from_ls: str | None
    ) -> bool:
        """Upsert one LS subscription. Returns True when the row is new.

        Operator-set ``github_username`` wins: the LS-derived value only
        lands when the stored column is NULL.
        """
        attrs = sub.get("attributes") or {}
        sub_id = str(sub.get("id"))
        is_new = (
            await conn.fetchval(
                "SELECT 1 FROM pro_subscriptions WHERE subscription_id = $1", sub_id
            )
        ) is None
        await conn.execute(
            """
            INSERT INTO pro_subscriptions (
                subscription_id, order_id, status, product_id, variant_id,
                customer_email, customer_name, github_username,
                ends_at, renews_at, raw
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (subscription_id) DO UPDATE SET
                order_id = EXCLUDED.order_id,
                status = EXCLUDED.status,
                product_id = EXCLUDED.product_id,
                variant_id = EXCLUDED.variant_id,
                customer_email = EXCLUDED.customer_email,
                customer_name = EXCLUDED.customer_name,
                github_username = COALESCE(
                    pro_subscriptions.github_username, EXCLUDED.github_username
                ),
                ends_at = EXCLUDED.ends_at,
                renews_at = EXCLUDED.renews_at,
                raw = EXCLUDED.raw,
                last_seen_at = NOW(),
                updated_at = NOW()
            """,
            sub_id,
            str(attrs.get("order_id")) if attrs.get("order_id") is not None else None,
            str(attrs.get("status") or "unknown").lower(),
            str(attrs.get("product_id")) if attrs.get("product_id") is not None else None,
            str(attrs.get("variant_id")) if attrs.get("variant_id") is not None else None,
            attrs.get("user_email"),
            attrs.get("user_name"),
            username_from_ls,
            _parse_ts(attrs.get("ends_at")),
            _parse_ts(attrs.get("renews_at")),
            json.dumps(attrs),
        )
        return is_new

    @staticmethod
    async def _record_initial_revenue(
        conn: Any,
        sub: dict[str, Any],
        order_attrs: dict[str, Any] | None,
    ) -> int:
        """Record the initial order as a revenue_events row, idempotently.

        Keyed ``ls_order_<id>`` so re-syncs never double-count. Renewal
        invoices are a follow-up (#3216) — this covers the initial charge
        that the unreachable webhook was supposed to capture.
        """
        attrs = sub.get("attributes") or {}
        order_id = attrs.get("order_id")
        if order_id is None or not order_attrs:
            return 0
        cents = order_attrs.get("total") or 0
        try:
            amount_usd = float(cents) / 100.0
        except (TypeError, ValueError):
            amount_usd = 0.0
        result = await conn.execute(
            """
            INSERT INTO revenue_events (
                event_type, source, amount_usd, currency, recurring,
                customer_email, customer_id, external_id, external_data
            )
            SELECT 'order_created', 'lemon_squeezy', $1, $2, false, $3, $4, $5, $6
            WHERE NOT EXISTS (
                SELECT 1 FROM revenue_events WHERE external_id = $5
            )
            """,
            amount_usd,
            str(order_attrs.get("currency") or "USD"),
            attrs.get("user_email"),
            str(attrs.get("customer_id")) if attrs.get("customer_id") is not None else None,
            f"ls_order_{order_id}",
            json.dumps({"via": "pro_delivery_poll", "subscription_id": sub.get("id")}),
        )
        # asyncpg returns a command tag like "INSERT 0 1".
        return 1 if str(result).endswith("1") else 0

    # -- the sync ----------------------------------------------------------

    async def sync(self) -> SyncOutcome:
        """One full reconcile pass: fetch LS state, upsert rows, converge
        GitHub access to the policy, record initial revenue."""
        cfg = await self._resolve_config()
        outcome = SyncOutcome()

        async with self._client() as client:
            subs, orders = await self._fetch_subscriptions(client, cfg)
            outcome.subscriptions_seen = len(subs)

            pending_invitations: list[dict[str, Any]] | None = None

            async with self._pool.acquire() as conn:
                for sub in subs:
                    attrs = sub.get("attributes") or {}
                    sub_id = str(sub.get("id"))
                    status = str(attrs.get("status") or "unknown").lower()
                    order_attrs = orders.get(str(attrs.get("order_id")))
                    username_from_ls = _extract_github_username(attrs, order_attrs)

                    try:
                        is_new = await self._upsert_row(conn, sub, username_from_ls)
                        if is_new:
                            outcome.revenue_rows += await self._record_initial_revenue(
                                conn, sub, order_attrs
                            )

                        row = await conn.fetchrow(
                            """
                            SELECT github_username, github_invited_at,
                                   github_revoked_at
                              FROM pro_subscriptions
                             WHERE subscription_id = $1
                            """,
                            sub_id,
                        )
                        username = row["github_username"] if row else None
                        invited_at = row["github_invited_at"] if row else None
                        revoked_at = row["github_revoked_at"] if row else None

                        wants_access = status in ACCESS_STATUSES
                        wants_revoke = status in REVOKE_STATUSES

                        if wants_access and not username:
                            outcome.missing_username.append(sub_id)
                            emit_finding(
                                source="pro_delivery",
                                kind="pro_delivery_action_needed",
                                title="Pro subscriber has no GitHub username",
                                body=(
                                    f"Subscription {sub_id} "
                                    f"({attrs.get('user_email') or 'no email'}, "
                                    f"status={status}) is entitled to repo access "
                                    "but carries no GitHub username. Link one "
                                    "with: poindexter pro link "
                                    f"{sub_id} <github-username>"
                                ),
                                severity="warn",
                                dedup_key=f"pro_delivery_username_{sub_id}",
                            )
                        elif wants_access and username and (
                            invited_at is None or revoked_at is not None
                        ):
                            await self._github_invite(client, cfg, username)
                            await conn.execute(
                                """
                                UPDATE pro_subscriptions
                                   SET github_invited_at = NOW(),
                                       github_revoked_at = NULL,
                                       updated_at = NOW()
                                 WHERE subscription_id = $1
                                """,
                                sub_id,
                            )
                            outcome.invited.append(username)
                            logger.info(
                                "[ProDelivery] invited %s (sub %s, %s)",
                                username, sub_id, status,
                            )
                        elif (
                            wants_revoke
                            and username
                            and invited_at is not None
                            and revoked_at is None
                        ):
                            if pending_invitations is None:
                                pending_invitations = (
                                    await self._github_pending_invitations(client, cfg)
                                )
                            await self._github_revoke(
                                client, cfg, username, pending_invitations
                            )
                            await conn.execute(
                                """
                                UPDATE pro_subscriptions
                                   SET github_revoked_at = NOW(),
                                       updated_at = NOW()
                                 WHERE subscription_id = $1
                                """,
                                sub_id,
                            )
                            outcome.revoked.append(username)
                            logger.info(
                                "[ProDelivery] revoked %s (sub %s, %s)",
                                username, sub_id, status,
                            )
                    except Exception as exc:
                        # Per-subscription isolation: one bad row (GitHub 5xx,
                        # malformed payload) must not strand every other
                        # subscriber's delivery.
                        outcome.errors.append(f"{sub_id}: {exc}")
                        logger.error(
                            "[ProDelivery] sync failed for subscription %s: %s",
                            sub_id, exc, exc_info=True,
                        )

        if outcome.errors:
            emit_finding(
                source="pro_delivery",
                kind="pro_delivery_error",
                title=f"Pro delivery sync hit {len(outcome.errors)} error(s)",
                body="\n".join(outcome.errors[:5]),
                severity="warn",
                dedup_key="pro_delivery_sync_errors",
            )
        return outcome


# -- module-level entry points (job + CLI adapters call these) --------------


async def run_sync(
    pool: Any, site_config: Any, *, transport: httpx.AsyncBaseTransport | None = None
) -> SyncOutcome:
    """One reconcile pass. Raises ProDeliveryConfigError when unconfigured."""
    service = ProDeliveryService(
        pool=pool, site_config=site_config, transport=transport
    )
    return await service.sync()


async def resolve_subscription(conn: Any, ref: str) -> Any:
    """Resolve a subscription row by exact id, id prefix, or customer email.

    Returns the row, or raises ValueError naming the problem (not found /
    ambiguous) so adapters can surface it verbatim.
    """
    rows = await conn.fetch(
        """
        SELECT subscription_id, status, customer_email, github_username,
               github_invited_at, github_revoked_at, ends_at
          FROM pro_subscriptions
         WHERE subscription_id = $1
            OR subscription_id LIKE $1 || '%'
            OR LOWER(customer_email) = LOWER($1)
         ORDER BY last_seen_at DESC
        """,
        ref,
    )
    if not rows:
        raise ValueError(f"no pro_subscriptions row matches {ref!r} — run a sync first")
    if len(rows) > 1 and not any(str(r["subscription_id"]) == ref for r in rows):
        ids = ", ".join(str(r["subscription_id"]) for r in rows[:5])
        raise ValueError(f"{ref!r} is ambiguous — matches: {ids}")
    exact = [r for r in rows if str(r["subscription_id"]) == ref]
    return exact[0] if exact else rows[0]


async def cli_link(
    pool: Any,
    site_config: Any,
    ref: str,
    username_raw: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Attach a GitHub username to a subscription and deliver immediately.

    Clears invite/revoke stamps so the reconcile pass treats the row as
    fresh — then runs a sync so the invite lands now, not next tick.
    """
    username = normalize_github_username(username_raw)
    if not username:
        raise ValueError(
            f"{username_raw!r} is not a valid GitHub username after normalization"
        )
    async with pool.acquire() as conn:
        row = await resolve_subscription(conn, ref)
        await conn.execute(
            """
            UPDATE pro_subscriptions
               SET github_username = $2,
                   github_invited_at = NULL,
                   github_revoked_at = NULL,
                   updated_at = NOW()
             WHERE subscription_id = $1
            """,
            str(row["subscription_id"]),
            username,
        )
    outcome = await run_sync(pool, site_config, transport=transport)
    return {
        "ok": True,
        "subscription_id": str(row["subscription_id"]),
        "github_username": username,
        "invited": username in outcome.invited,
        "sync": outcome.as_metrics(),
    }


async def cli_unlink(
    pool: Any,
    site_config: Any,
    ref: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Detach a subscription's GitHub account and revoke its access now.

    Clearing ``github_username`` is what makes this stick: with the column
    NULL the reconciler cannot re-invite on the next tick — it re-raises
    the ``pro_delivery_action_needed`` finding instead, so an active
    subscriber doesn't silently lose delivery.
    """
    service = ProDeliveryService(
        pool=pool, site_config=site_config, transport=transport
    )
    cfg = await service._resolve_config()
    async with pool.acquire() as conn:
        row = await resolve_subscription(conn, ref)
        username = row["github_username"]
        if username and row["github_invited_at"] and not row["github_revoked_at"]:
            async with service._client() as client:
                invitations = await service._github_pending_invitations(client, cfg)
                await service._github_revoke(client, cfg, username, invitations)
        await conn.execute(
            """
            UPDATE pro_subscriptions
               SET github_username = NULL,
                   github_revoked_at = CASE
                       WHEN github_invited_at IS NOT NULL THEN NOW()
                       ELSE github_revoked_at
                   END,
                   updated_at = NOW()
             WHERE subscription_id = $1
            """,
            str(row["subscription_id"]),
        )
    return {
        "ok": True,
        "subscription_id": str(row["subscription_id"]),
        "unlinked": username,
    }


async def cli_status(pool: Any, site_config: Any, *, limit: int = 50) -> dict[str, Any]:
    """Delivery-chain status for the CLI: config presence + row inventory."""
    sc = site_config
    config_state = {
        "pro_delivery_enabled": sc.get("pro_delivery_enabled", "false"),
        "pro_delivery_github_repo": sc.get("pro_delivery_github_repo", "") or "(unset)",
        "lemon_squeezy_api_key_set": bool(
            await sc.get_secret("lemon_squeezy_api_key", "")
        ),
        "pro_delivery_github_token_set": bool(
            await sc.get_secret("pro_delivery_github_token", "")
        ),
    }
    async with pool.acquire() as conn:
        counts = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM pro_subscriptions "
            "GROUP BY status ORDER BY n DESC"
        )
        rows = await conn.fetch(
            """
            SELECT subscription_id, status, customer_email, github_username,
                   github_invited_at, github_revoked_at, ends_at, last_seen_at
              FROM pro_subscriptions
             ORDER BY last_seen_at DESC
             LIMIT $1
            """,
            limit,
        )
    return {
        "config": config_state,
        "status_counts": {r["status"]: r["n"] for r in counts},
        "subscriptions": [dict(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# buyer-side seed apply — `poindexter pro apply` (#3216 follow-up)
#
# The Pro deliverable's seed is a REFERENCE tuning, not a drop-in: many values
# are tuned to the seller's hardware and model fleet. So apply is a
# diff-and-adopt, safe by default: dry-run unless told otherwise, and even
# then it only overwrites values still sitting at the OSS default — an
# operator's own tuning is never clobbered without an explicit flag.
# ---------------------------------------------------------------------------

# Key families held back for manual review even when adoptable: a model pin
# or GPU/VRAM number tuned on the seller's rig is the classic value that
# "applies clean" and then breaks a smaller machine at 2am.
_HARDWARE_KEY_HINTS = ("gpu", "vram", "num_ctx")


@dataclass
class SeedPlan:
    """Result of diffing a Pro seed against the live app_settings."""

    seed_path: str
    adoptable: dict[str, tuple[str, str]] = field(default_factory=dict)
    review: dict[str, tuple[str, str]] = field(default_factory=dict)
    conflicts: dict[str, tuple[str, str]] = field(default_factory=dict)
    identical: int = 0
    unknown_keys: list[str] = field(default_factory=list)
    secret_skipped: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "adoptable": len(self.adoptable),
            "review_held": len(self.review),
            "conflicts_kept": len(self.conflicts),
            "identical": self.identical,
            "unknown_to_this_engine": len(self.unknown_keys),
            "secret_skipped": len(self.secret_skipped),
        }


def resolve_seed_path(seed_ref: str | None) -> Path:
    """Resolve a seed reference to the seed JSON file, loudly.

    Accepts a direct path to ``seed-settings.json``, a pro-repo checkout
    directory, or nothing — in which case the two conventional locations
    (./ and ~/poindexter-pro) are tried. Raises ValueError naming every
    location tried; guessing silently is how a buyer applies the wrong file.
    """
    candidates: list[Path] = []
    if seed_ref:
        p = Path(seed_ref).expanduser()
        candidates = [p] if p.suffix == ".json" else [
            p / "config" / "seed-settings.json"
        ]
    else:
        candidates = [
            Path("config/seed-settings.json"),
            Path.home() / "poindexter-pro" / "config" / "seed-settings.json",
        ]
    for cand in candidates:
        if cand.is_file():
            return cand
    raise ValueError(
        "no seed-settings.json found — tried: "
        + "; ".join(str(c) for c in candidates)
        + ". Pass the path to your poindexter-pro checkout (git clone the "
        "repo your Pro invite granted, then `poindexter pro apply <path>`)."
    )


def _is_review_held(key: str, metadata: dict[str, Any]) -> bool:
    meta = metadata.get(key) or {}
    if meta.get("value_type") == "model":
        return True
    return any(hint in key for hint in _HARDWARE_KEY_HINTS)


def classify_seed(
    seed: dict[str, str],
    current: dict[str, tuple[str, bool]],
    defaults: dict[str, str],
    metadata: dict[str, Any],
    *,
    seed_path: str = "",
) -> SeedPlan:
    """Bucket every seed key by what applying it would mean here.

    ``current`` maps key -> (value, is_secret) from the live table. A key
    with no live row is effectively at its OSS default (the seeder is lazy),
    so it classifies exactly like a stock row.
    """
    plan = SeedPlan(seed_path=seed_path)
    for key in sorted(seed):
        seed_val = seed[key]
        if key not in defaults:
            plan.unknown_keys.append(key)
            continue
        row = current.get(key)
        if row is not None and row[1]:
            plan.secret_skipped.append(key)
            continue
        effective = row[0] if row is not None else defaults[key]
        if effective == seed_val:
            plan.identical += 1
            continue
        pair = (effective, seed_val)
        if effective == defaults[key]:
            if _is_review_held(key, metadata):
                plan.review[key] = pair
            else:
                plan.adoptable[key] = pair
        else:
            plan.conflicts[key] = pair
    return plan


async def cli_apply(
    pool: Any,
    seed_ref: str | None,
    *,
    apply: bool = False,
    include_models: bool = False,
    overwrite_conflicts: bool = False,
    defaults: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Diff a Pro seed against live settings; optionally adopt it.

    Dry-run unless ``apply``. The default apply set is the adoptable bucket
    only; ``include_models`` adds the review-held model/hardware keys and
    ``overwrite_conflicts`` adds keys the operator had customized — both
    opt-in, never implied.
    """
    if defaults is None or metadata is None:
        from services.settings_defaults import DEFAULTS, METADATA

        defaults = DEFAULTS if defaults is None else defaults
        metadata = METADATA if metadata is None else metadata

    path = resolve_seed_path(seed_ref)
    try:
        seed_raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read seed {path}: {exc}") from exc
    if not isinstance(seed_raw, dict):
        raise ValueError(f"{path} is not a JSON object of key -> value")
    seed = {str(k): str(v) for k, v in seed_raw.items()}

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value, is_secret FROM app_settings")
        current = {r["key"]: (r["value"], bool(r["is_secret"])) for r in rows}
        plan = classify_seed(
            seed, current, defaults, metadata, seed_path=str(path)
        )

        to_write: dict[str, str] = {}
        if apply:
            to_write.update({k: v for k, (_, v) in plan.adoptable.items()})
            if include_models:
                to_write.update({k: v for k, (_, v) in plan.review.items()})
            if overwrite_conflicts:
                to_write.update({k: v for k, (_, v) in plan.conflicts.items()})
            # Same proven upsert shape as the rest of the CLI settings path.
            for key, value in to_write.items():
                await conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = NOW()",
                    key,
                    value,
                )

    return {
        "seed_path": str(path),
        "dry_run": not apply,
        "counts": plan.counts(),
        "adoptable": plan.adoptable,
        "review_held": plan.review,
        "conflicts_kept": {
            k: v for k, v in plan.conflicts.items() if k not in to_write
        },
        "unknown_keys": plan.unknown_keys,
        "applied": sorted(to_write),
    }


__all__ = [
    "ACCESS_STATUSES",
    "REVOKE_STATUSES",
    "ProDeliveryConfigError",
    "ProDeliveryService",
    "SeedPlan",
    "SyncOutcome",
    "classify_seed",
    "cli_apply",
    "cli_link",
    "cli_status",
    "cli_unlink",
    "normalize_github_username",
    "resolve_seed_path",
    "resolve_subscription",
    "run_sync",
]
