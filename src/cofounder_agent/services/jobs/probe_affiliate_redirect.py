"""ProbeAffiliateRedirectJob — active liveness detector for the /go Worker.

The content pipeline rewrites seeded keywords into ``[text](/go/<code>)``
links, and the ``affiliate-redirect`` Cloudflare Worker
(``infrastructure/cloudflare/affiliate-redirect/``) resolves ``<code>`` to a
merchant URL at click time while logging the click to Analytics Engine.
:class:`SyncAffiliateClicksJob` ingests those into ``affiliate_link_clicks``.

If that Worker stops resolving, affiliate revenue attribution dies silently:
readers land on the homepage, no click is logged, nothing errors, and the
per-code totals simply flatline. Nothing watched it. ``r2_static_drift``
is structurally blind to it — that job checks the R2 *object*, which stays
perfectly healthy while the Worker's *binding* is what broke.

## Why an unknown code is (now) a useful probe

Before glad-labs-stack#3520 every failure returned the same ``302`` to the
homepage as a healthy unknown-code lookup, so no external probe could tell a
dead Worker from a live one. #3520 made the states distinct:

    503  LINKS_URL / HOME_URL unset          -> Worker not wired
    502  link map unloadable                 -> bad URL, R2 outage, bad JSON
    302 -> HOME_URL   blank/unknown code     -> normal
    302 -> merchant   known code             -> normal, logs one AE click

Crucially the Worker loads and parses the map *before* it resolves the code,
and only writes an Analytics Engine data point once a target actually
resolves. So a request for a code that does not exist still exercises config
resolution and the full map fetch/parse — the two failure modes that take
every link down at once — while writing **nothing**. That is the cheap tier
below, and it is why this job can run often without polluting click data.

What an unknown code cannot prove is map *content*: a Worker pointed at a
valid-but-wrong map (a fork's bucket, a stale export missing new codes)
loads it fine and 302s home for our sentinel exactly like a healthy one. Only
resolving a code we know is in the map distinguishes that, and that request
does log a click. Hence two tiers:

* **cheap** (every run) — GET ``/go/<sentinel>`` for a code guaranteed absent.
  Expect ``302`` to the home URL. A 502/503/non-redirect means every affiliate
  link on the site is dead. Zero Analytics Engine writes.
* **deep** (throttled, default once per 24h) — GET ``/go/<real code>`` and
  assert the ``Location`` is that row's merchant URL. Catches content drift.
  Costs exactly one data point per run, and the probe's user agent is chosen
  to match ``affiliate_click_bot_ua_pattern`` so the resulting row lands
  ``is_bot=true`` — outside ``affiliate_link_clicks_human`` and therefore
  outside the ``_rollup_clicks`` totals and every reader surface.

Results publish on the two standard channels: the
``poindexter_affiliate_redirect_healthy`` gauge (0/1, alerted by
``PoindexterAffiliateRedirectDown``) and an ``affiliate_redirect_unhealthy``
finding routed through FindingsAlertRouter to Discord.

An install with no active affiliate links, or no ``site_url``, has nothing to
probe: the job skips and holds the gauge healthy. Absence of config must never
read as an outage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from plugins.job import JobResult
from services.metrics_exporter import AFFILIATE_REDIRECT_HEALTHY
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Watermark for the throttled deep check.
_DEEP_CHECK_KEY = "affiliate_redirect_probe_last_deep_check"

# A code that cannot collide with a real one. Verified against the DB at run
# time anyway — a sentinel that turned out to be a live code would silently
# convert the cheap tier into a click-logging one.
_SENTINEL_CODE = "__poindexter_liveness_probe__"

# Chosen to match `affiliate_click_bot_ua_pattern` (contains "monitor"), so the
# deep tier's click is classified is_bot=true by SyncAffiliateClicksJob and
# never reaches affiliate_link_clicks_human / _rollup_clicks. Changing this
# string without checking that pattern would start inflating real click counts.
_PROBE_UA = (
    "poindexter-affiliate-probe/1.0 (liveness monitor; "
    "+https://github.com/Glad-Labs/poindexter)"
)


def build_probe_url(base: str, site_url: str, code: str) -> str | None:
    """Resolve ``/go/<code>`` against both supported deploy shapes.

    ``affiliate_redirect_base_url`` is either a path on the site zone
    (``/go``) or an absolute subdomain origin (``https://go.example.com``).
    Returns None when the path form is configured but ``site_url`` is not,
    since there is then no origin to hang the path on.
    """
    base = (base or "").strip().rstrip("/")
    site_url = (site_url or "").strip().rstrip("/")
    if base.startswith("http://") or base.startswith("https://"):
        return f"{base}/{code}"
    if not site_url:
        return None
    return f"{site_url}{base}/{code}"


def _same_destination(location: str, home: str) -> bool:
    """True when a redirect Location points at the site's own home origin.

    The Worker normalises ``https://example.com`` to ``https://example.com/``,
    and an operator may configure ``site_url`` with or without a trailing
    slash, so compare on (scheme, netloc, normalised path) rather than raw
    string equality.
    """
    a, b = urlsplit(location), urlsplit(home)
    return (
        a.scheme == b.scheme
        and a.netloc == b.netloc
        and (a.path or "/").rstrip("/") == (b.path or "/").rstrip("/")
    )


class ProbeAffiliateRedirectJob:
    name = "probe_affiliate_redirect"
    description = (
        "Follow a real /go/<code> affiliate link end to end and verify it "
        "still resolves to its merchant URL; publish health as a Prometheus "
        "gauge and emit a finding when the Worker is broken."
    )
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            AFFILIATE_REDIRECT_HEALTHY.set(1)
            return JobResult(
                ok=True,
                detail="no _site_config in job config — skipping probe",
                changes_made=0,
            )

        site_url = (sc.get("site_url", "") or "").strip()
        base = (sc.get("affiliate_redirect_base_url", "/go") or "/go").strip()

        sentinel_url = build_probe_url(base, site_url, _SENTINEL_CODE)
        if sentinel_url is None:
            AFFILIATE_REDIRECT_HEALTHY.set(1)
            return JobResult(
                ok=True,
                detail="site_url unset and base is a path — nothing to probe",
                changes_made=0,
            )

        # The home URL the Worker falls back to. HOME_URL lives only as a
        # Worker secret (#3520), so site_url is our best local mirror of it.
        home_url = site_url or ""

        try:
            import httpx
        except ImportError:
            return JobResult(ok=False, detail="httpx not available", changes_made=0)

        # ---------------- cheap tier: config + map loadability -------------
        problems: list[str] = []
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0), follow_redirects=False
            ) as client:
                resp = await client.get(
                    sentinel_url, headers={"User-Agent": _PROBE_UA}
                )
                status = resp.status_code
                location = resp.headers.get("location", "")
        except Exception as e:  # noqa: BLE001 — any transport failure is a fault
            problems.append(f"sentinel request failed: {describe_exception(e)}")
            status, location = 0, ""

        if not problems:
            if status == 503:
                problems.append(
                    "503 — the Worker reports itself unconfigured (LINKS_URL "
                    "or HOME_URL missing). Every /go link is dead. Most likely "
                    "a `wrangler deploy` dropped the bindings without the "
                    "secrets being re-set."
                )
            elif status == 502:
                problems.append(
                    "502 — the Worker cannot load the affiliate link map "
                    "(bad LINKS_URL, R2 unreachable, or an unparseable "
                    "object). Every /go link is dead."
                )
            elif status != 302:
                problems.append(
                    f"expected 302 for an unknown code, got HTTP {status}"
                )
            elif home_url and not _same_destination(location, home_url):
                problems.append(
                    f"unknown code redirected to {location!r}, not the site "
                    f"home ({home_url!r})"
                )

        # ---------------- deep tier: does a real code still resolve? -------
        deep_detail = "skipped (throttled)"
        deep_ran = False
        if not problems and await self._deep_check_due(pool, sc):
            deep_ran = True
            deep_detail, deep_problem = await self._run_deep_check(
                pool, httpx, base, site_url
            )
            if deep_problem:
                problems.append(deep_problem)
            await self._record_deep_check(pool)

        # ---------------- publish -----------------------------------------
        healthy = not problems
        AFFILIATE_REDIRECT_HEALTHY.set(1 if healthy else 0)

        if healthy:
            return JobResult(
                ok=True,
                detail=f"/go healthy (sentinel HTTP {status}; deep: {deep_detail})",
                changes_made=0,
                metrics={"deep_check_ran": 1 if deep_ran else 0},
            )

        summary = "; ".join(problems)
        logger.warning("[AFFILIATE_PROBE] /go redirect unhealthy: %s", summary)
        emit_finding(
            source="probe_affiliate_redirect",
            kind="affiliate_redirect_unhealthy",
            severity="warn",
            title="Affiliate /go redirect is not resolving",
            body=(
                f"A liveness probe of the affiliate redirect Worker failed: "
                f"{summary}\n\n"
                f"While this is broken every affiliate link in every published "
                f"post silently sends readers to the homepage instead of the "
                f"merchant, and no click is recorded — revenue attribution "
                f"stops with no other error anywhere.\n\n"
                f"Probe URL: {sentinel_url}\n"
                f"Check: `npx wrangler deployments list` then `wrangler "
                f"versions view <id>` (LINKS_URL/HOME_URL must be present as "
                f"SECRETS, not vars), and `wrangler secret list`. See "
                f"infrastructure/cloudflare/affiliate-redirect/README.md."
            ),
            dedup_key="affiliate_redirect_unhealthy",
        )
        # ok=True: the probe ran fine — a broken Worker is the observed result,
        # not a job crash. Gauge + finding carry the signal; failing the job
        # would double-alert and trigger scheduler back-off on a working probe.
        return JobResult(
            ok=True,
            detail=f"/go UNHEALTHY ({summary}) — finding emitted",
            changes_made=0,
            metrics={"deep_check_ran": 1 if deep_ran else 0},
        )

    # ------------------------------------------------------------------
    async def _deep_check_due(self, pool: Any, sc: Any) -> bool:
        """True when the click-logging deep check is due (default 24h)."""
        try:
            hours = int(sc.get("affiliate_redirect_probe_deep_interval_hours", 24))
        except (TypeError, ValueError):
            hours = 24
        if hours <= 0:
            return False
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT value FROM app_settings WHERE key = $1", _DEEP_CHECK_KEY
                )
        except Exception as e:  # noqa: BLE001 — never fail the probe on this
            logger.warning(
                "[AFFILIATE_PROBE] deep-check watermark read failed: %s",
                describe_exception(e),
            )
            return False
        raw = (row["value"] if row else "") or ""
        if not raw:
            return True
        try:
            last = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last >= timedelta(hours=hours)

    async def _record_deep_check(self, pool: Any) -> None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    _DEEP_CHECK_KEY,
                    datetime.now(timezone.utc).isoformat(),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[AFFILIATE_PROBE] deep-check watermark write failed: %s",
                describe_exception(e),
            )

    async def _run_deep_check(
        self, pool: Any, httpx: Any, base: str, site_url: str
    ) -> tuple[str, str | None]:
        """Resolve one real code and confirm it reaches its merchant URL.

        Returns ``(detail, problem_or_None)``.
        """
        try:
            async with pool.acquire() as conn:
                # Least-recently-probed first would need another column; the
                # oldest active link is stable and adequate, and keeping it
                # deterministic means the one synthetic click a day lands on a
                # predictable code rather than smearing across the corpus.
                row = await conn.fetchrow(
                    "SELECT code, url FROM affiliate_links "
                    "WHERE is_active = true AND code <> $1 "
                    "ORDER BY created_at ASC LIMIT 1",
                    _SENTINEL_CODE,
                )
        except Exception as e:  # noqa: BLE001
            return f"link lookup failed: {describe_exception(e)}", None

        if row is None:
            # No affiliate links configured — nothing to resolve, and that is
            # not a fault.
            return "no active affiliate links", None

        code, expected = row["code"], row["url"]
        url = build_probe_url(base, site_url, code)
        if url is None:
            return "unresolvable probe URL", None

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0), follow_redirects=False
            ) as client:
                resp = await client.get(url, headers={"User-Agent": _PROBE_UA})
        except Exception as e:  # noqa: BLE001
            return (
                f"{code}: request failed",
                f"deep check {code!r} request failed: {describe_exception(e)}",
            )

        location = resp.headers.get("location", "")
        if resp.status_code != 302:
            return (
                f"{code}: HTTP {resp.status_code}",
                f"deep check {code!r} returned HTTP {resp.status_code}, expected 302",
            )
        if location != expected:
            # The clobber signature: the map loaded (no 502) but this code is
            # not in it, so the Worker fell through to the home redirect.
            return (
                f"{code}: wrong destination",
                (
                    f"deep check {code!r} redirected to {location!r} but "
                    f"affiliate_links says {expected!r} — the Worker is "
                    f"resolving against the wrong or a stale link map"
                ),
            )
        return f"{code} -> merchant OK", None
