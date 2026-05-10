"""Operator URL probe — flag operator-facing URLs/IPs that have drifted or gone dark.

Closes GitHub Glad-Labs/poindexter#214.

The brain daemon already monitors *services* (FastAPI, Ollama, Postgres). What it
historically did NOT monitor is the *surfaces* operators actually click on —
Grafana dashboard links pointing at internal Tailscale IPs, app_setting URLs that
got renamed but never updated in the dashboards, public storefront URLs that
404'd because of a routing change. This probe walks every operator-facing surface
the system knows about and pings them. Anything that doesn't respond gets a
``notify_operator()`` call with the surface name + recommended fix.

Surfaces inspected each cycle:

1. **Dashboard links** — every `infrastructure/grafana/dashboards/*.json`. We pull
   URLs from dashboard-level ``links``, panel-level ``links`` AND
   ``fieldConfig.defaults.links`` (data-link templates).
2. **system_devices.tailscale_ip** — compared against the live ``tailscale
   status --json`` output. Drift between DB and Tailscale is the exact bug class
   that triggered #214.
3. **app_settings keys ending in ``_url``** — ``site_url``, ``storefront_url``,
   ``oauth_issuer_url`` and friends. These are what the Grafana templates
   substitute in.
4. **Internal compose URLs** — ``prefect_api_url``, ``grafana_url``, ``loki_url``
   and similar app_settings keys (matched by a curated list, not just suffix,
   because some don't end in ``_url``).

Concurrency cap: ~10 in-flight HTTP probes via an ``asyncio.Semaphore`` — short
connect/read timeouts so a single dead URL can't stall the cycle. We never run
more than once per surface per cycle, and we cap operator notifications at 1 per
surface per cycle to avoid blasting Telegram when (e.g.) the whole observability
stack is down at the same time.

Standalone module — only depends on stdlib + ``httpx`` (already a project dep).
``tailscale`` CLI is optional; if it's missing we skip drift detection without
failing the probe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.operator_notifier import notify_operator

# Module-level import so monkey-patching `brain.docker_utils.IN_DOCKER`
# in tests reaches the function. Importing inside the function would
# re-resolve through the import system each call, but pytest patches
# the package-qualified module — having the reference here pinned to
# the same module makes the test deterministic across local + CI.
try:
    from docker_utils import localize_url as _localize_url_impl
    from docker_utils import IN_DOCKER as _IN_DOCKER  # noqa: F401 — re-export for clarity
except ImportError:  # pragma: no cover — package-qualified path
    from brain.docker_utils import localize_url as _localize_url_impl  # type: ignore[no-redef]
    from brain.docker_utils import IN_DOCKER as _IN_DOCKER  # type: ignore[no-redef] # noqa: F401


def _localize(url: str) -> str:
    """Adapter so tests can monkey-patch this module's `_localize`
    without caring which import path the probe used internally."""
    return _localize_url_impl(url)


logger = logging.getLogger("brain.operator_url_probe")

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Per-request timeouts — kept short so one dead URL doesn't stall the probe.
# Connect 3s + read 5s is well above the median for any service we monitor.
HTTP_CONNECT_TIMEOUT_S = 3.0
HTTP_READ_TIMEOUT_S = 5.0

# Cap concurrent in-flight probes so we don't hammer the network when many
# dashboards reference the same backend.
DEFAULT_CONCURRENCY = 10

# Curated list of internal-compose URL keys. These don't all end in ``_url`` —
# some are bare hostnames or include explicit ports. The probe checks both
# this set AND any app_setting key matching ``*_url`` so new keys get probed
# automatically. Order doesn't matter; duplicates are deduped on lookup.
INTERNAL_COMPOSE_URL_KEYS: tuple[str, ...] = (
    "prefect_api_url",
    "grafana_url",
    "loki_url",
    "tempo_url",
    "prometheus_url",
    "ollama_base_url",
    "ollama_url",
    "internal_api_base_url",
    "api_base_url",
    "openclaw_gateway_url",
    "sdxl_server_url",
)

# Suffix that triggers automatic probing for any app_settings key. Matches
# the convention used throughout the codebase (site_url, storefront_url,
# oauth_issuer_url, ...).
URL_KEY_SUFFIX = "_url"

# Default location for Grafana dashboard JSON. Resolved relative to the repo
# root because the brain daemon usually runs from there. The probe accepts
# an explicit override (used in tests) so it doesn't depend on cwd.
DEFAULT_DASHBOARDS_DIR = Path(__file__).resolve().parent.parent / "infrastructure" / "grafana" / "dashboards"

# Probe schedule: every 15 minutes per #214's "every ~15 min" guidance.
PROBE_INTERVAL_SECONDS = 15 * 60

# Identifying UA so probes show up clearly in any reverse-proxy logs and
# don't get tarpitted by Cloudflare's bot fight mode.
_USER_AGENT = "Poindexter-OperatorURLProbe/1.0 (+https://www.gladlabs.io)"

# Module-level state — track last run + per-surface notify-once accounting.
_last_run_ts: float = 0.0


# ---------------------------------------------------------------------------
# Dashboard link extraction
# ---------------------------------------------------------------------------


def _walk_panel_links(panel: dict[str, Any]) -> Iterable[tuple[str, str]]:
    """Yield (title, url) pairs from a single panel's link configuration.

    Grafana stores links in TWO places per panel:

    * ``panel["links"]`` — top-of-panel quick-jump links
    * ``panel["fieldConfig"]["defaults"]["links"]`` — per-data-point drill-down
      links (used in stat panels, heatmaps, etc.)

    We extract from both. Templating placeholders (``${var}``) are left as-is
    — the probe will skip them rather than try to substitute, so a templated
    URL won't generate a false positive.
    """
    title = panel.get("title") or "(untitled panel)"
    for link in panel.get("links") or []:
        url = (link or {}).get("url")
        if url:
            yield (f"{title} :: {link.get('title') or 'link'}", url)
    field_config = panel.get("fieldConfig") or {}
    defaults = field_config.get("defaults") or {}
    for link in defaults.get("links") or []:
        url = (link or {}).get("url")
        if url:
            yield (f"{title} :: data-link {link.get('title') or ''}".rstrip(), url)
    # Recurse into nested rows (Grafana puts panels inside row.panels)
    for nested in panel.get("panels") or []:
        if isinstance(nested, dict):
            yield from _walk_panel_links(nested)


def extract_dashboard_links(dashboards_dir: Path) -> list[dict[str, str]]:
    """Walk every dashboard JSON file and return [{dashboard, surface, url}, ...].

    A "surface" is the operator-readable identifier of where the URL appears,
    e.g. ``"Mission Control :: Prefect UI"``. Skips templated URLs (containing
    ``${``) so we don't probe ``http://${ip}:4200`` and report it as broken.
    """
    if not dashboards_dir.exists():
        logger.warning(
            "[OPERATOR_URL_PROBE] Dashboards dir not found: %s", dashboards_dir
        )
        return []

    found: list[dict[str, str]] = []
    for path in sorted(dashboards_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[OPERATOR_URL_PROBE] Could not parse %s: %s", path.name, exc
            )
            continue

        dashboard_title = data.get("title") or path.stem

        # Dashboard-level links
        for link in data.get("links") or []:
            url = (link or {}).get("url")
            if not url:
                continue
            found.append({
                "dashboard": dashboard_title,
                "surface": f"{dashboard_title} :: {link.get('title') or 'link'}",
                "url": url,
            })

        # Panel-level links + field-config data links
        for panel in data.get("panels") or []:
            if not isinstance(panel, dict):
                continue
            for surface_suffix, url in _walk_panel_links(panel):
                found.append({
                    "dashboard": dashboard_title,
                    "surface": f"{dashboard_title} :: {surface_suffix}",
                    "url": url,
                })

    # Drop templated URLs — we can't resolve ${var} without dashboard context,
    # and probing the literal would generate noisy false positives.
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in found:
        if "${" in item["url"] or "$__" in item["url"]:
            continue
        key = (item["surface"], item["url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


# ---------------------------------------------------------------------------
# Tailscale drift detection
# ---------------------------------------------------------------------------


def _run_tailscale_status() -> dict[str, str] | None:
    """Run ``tailscale status --json`` and return ``{hostname: tailscale_ip}``.

    Returns ``None`` if the CLI isn't available or the command fails — drift
    detection is best-effort. We never crash the probe on a missing tailscale
    install.
    """
    try:
        kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 10}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(["tailscale", "status", "--json"], **kwargs)
    except FileNotFoundError:
        logger.info("[OPERATOR_URL_PROBE] tailscale CLI not on PATH — skipping drift detection")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[OPERATOR_URL_PROBE] tailscale status timed out")
        return None
    except Exception as exc:
        logger.warning("[OPERATOR_URL_PROBE] tailscale status failed: %s", exc)
        return None

    if result.returncode != 0:
        logger.warning(
            "[OPERATOR_URL_PROBE] tailscale status returned %d: %s",
            result.returncode, (result.stderr or "")[:200],
        )
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("[OPERATOR_URL_PROBE] tailscale status JSON parse failed: %s", exc)
        return None

    name_to_ip: dict[str, str] = {}

    # The local node lives at data["Self"], peers at data["Peer"][nodekey].
    # Both expose ``HostName`` (short) and ``DNSName`` (FQDN) plus a
    # ``TailscaleIPs`` list whose first entry is the v4 address.
    def _add(node: dict[str, Any]) -> None:
        ips = node.get("TailscaleIPs") or []
        if not ips:
            return
        v4 = next((ip for ip in ips if ":" not in ip), ips[0])
        for label_key in ("HostName", "DNSName"):
            label = node.get(label_key)
            if label:
                # Strip the trailing dot Tailscale appends to MagicDNS names.
                name_to_ip.setdefault(label.split(".")[0].lower(), v4)

    self_node = data.get("Self")
    if isinstance(self_node, dict):
        _add(self_node)
    for peer in (data.get("Peer") or {}).values():
        if isinstance(peer, dict):
            _add(peer)
    return name_to_ip


async def detect_tailscale_drift(pool) -> list[dict[str, str]]:
    """Return list of system_devices rows whose tailscale_ip drifted.

    Each row: ``{"surface", "name", "db_ip", "live_ip", "fix"}``. Empty list
    when there's no drift, when tailscale isn't installed, or when the
    system_devices table doesn't exist (fresh install).
    """
    live = _run_tailscale_status()
    if live is None:
        return []

    try:
        rows = await pool.fetch("SELECT name, tailscale_ip FROM system_devices")
    except Exception as exc:
        # Table may not exist on a fresh install — log + bail.
        if "does not exist" in str(exc):
            logger.info("[OPERATOR_URL_PROBE] system_devices table missing — skipping drift")
            return []
        logger.warning("[OPERATOR_URL_PROBE] system_devices read failed: %s", exc)
        return []

    drift: list[dict[str, str]] = []
    for row in rows:
        name = (row["name"] or "").lower()
        db_ip = row["tailscale_ip"]
        live_ip = live.get(name)
        if live_ip and db_ip and live_ip != db_ip:
            drift.append({
                "surface": f"system_devices.{name}",
                "name": name,
                "db_ip": db_ip,
                "live_ip": live_ip,
                "fix": (
                    f"UPDATE system_devices SET tailscale_ip = '{live_ip}', "
                    f"updated_at = NOW() WHERE name = '{name}';"
                ),
            })
    return drift


# ---------------------------------------------------------------------------
# app_settings URL collection
# ---------------------------------------------------------------------------


async def collect_app_setting_urls(pool) -> list[dict[str, str]]:
    """Return ``[{surface, key, url}, ...]`` for every probable URL in app_settings.

    "Probable URL" = the key ends in ``_url`` OR it's in
    ``INTERNAL_COMPOSE_URL_KEYS``, AND the value is an http/https URL.

    Skips:
      * Empty strings + values without ``://`` (misconfigured rows).
      * Non-HTTP schemes — postgresql://, redis://, ws://, amqp://, etc.
        These are valid URIs but HEAD/GET can't probe them and they'd
        fail every cycle.
      * Keys named in ``operator_url_probe_skip_keys`` (comma-separated
        app_setting). Operator-controlled mute list for surfaces like
        social profiles that are bot-protected and return 403.

    Localhost / 127.0.0.1 URLs are rewritten via ``localize_url()`` so
    URLs that work from the host become reachable from inside the brain
    container too.
    """
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE value IS NOT NULL AND value <> ''"
        )
    except Exception as exc:
        logger.warning("[OPERATOR_URL_PROBE] app_settings read failed: %s", exc)
        return []

    # Operator skip-list. Comma-separated, whitespace-tolerant. Empty
    # default keeps the probe behavior fully discoverable — operator
    # explicitly opts in to muting individual keys.
    try:
        skip_raw = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "operator_url_probe_skip_keys",
        )
    except Exception:
        skip_raw = None
    skip_keys: set[str] = {
        s.strip() for s in (skip_raw or "").split(",") if s.strip()
    }

    explicit = set(INTERNAL_COMPOSE_URL_KEYS)
    out: list[dict[str, str]] = []
    seen_urls: set[tuple[str, str]] = set()
    for row in rows:
        key = row["key"]
        if key in skip_keys:
            continue
        if not (key.endswith(URL_KEY_SUFFIX) or key in explicit):
            continue
        value = (row["value"] or "").strip()
        # Has to look like a URL to be probable; bare hostnames aren't
        # something HEAD/GET can hit reliably.
        if "://" not in value:
            continue
        # Only http/https are HEAD/GET-probable. Database, message-broker,
        # and websocket URIs are valid but unreachable via this probe.
        scheme = value.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            continue
        # Translate host-side URLs (localhost, 127.0.0.1) into the
        # container-reachable host.docker.internal equivalent. No-op when
        # the brain runs on the host (IN_DOCKER not set).
        value = _localize(value)
        dedup_key = (key, value)
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)
        out.append({
            "surface": f"app_settings.{key}",
            "key": key,
            "url": value,
        })
    return out


# ---------------------------------------------------------------------------
# HTTP probing
# ---------------------------------------------------------------------------


def _parse_alive_codes(spec: str) -> list[range]:
    """Parse an alive_codes spec into a list of ranges.

    Accepts:
      - ``"200-399"`` — inclusive single range
      - ``"200-399,418"`` — comma-separated mix of ranges + singletons
      - ``"200,201,204"`` — comma-separated singletons only

    Returns a list of Python ``range`` objects (each ``stop`` is
    exclusive in the standard way). Default when parse fails: the
    standard 200–399 range, so a malformed override never makes
    ALL URLs look alive.
    """
    out: list[range] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                out.append(range(int(lo), int(hi) + 1))
            except (ValueError, TypeError):
                continue
        else:
            try:
                v = int(part)
                out.append(range(v, v + 1))
            except (ValueError, TypeError):
                continue
    return out or [range(200, 400)]


def _is_alive_per_override(
    status_code: int, override: dict[str, Any] | None,
) -> bool:
    """Apply per-URL alive_codes override if present.

    Default behavior (override is None or missing alive_codes): the
    standard ``200 <= status < 400``. Overrides that mark e.g.
    ``alive_codes='200-499'`` extend the alive range to cover legit
    4xx responses from outbound-only APIs.
    """
    if not override or "alive_codes" not in override:
        return 200 <= status_code < 400
    for r in _parse_alive_codes(override["alive_codes"]):
        if status_code in r:
            return True
    return False


async def _probe_one_url(
    client: "httpx.AsyncClient",
    semaphore: asyncio.Semaphore,
    surface: str,
    url: str,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """HEAD an URL; if HEAD isn't supported, retry with GET. Always returns a
    dict, never raises — surface name is preserved for downstream notify.

    ``override`` is a per-URL probe-config dict from
    ``operator_url_probe_target_overrides``. When supplied it can widen
    the ``alive_codes`` range (e.g. include 4xx for outbound-only APIs)
    and/or change the request method. The override is preserved in the
    result dict so downstream notify formatting can show why a 4xx
    was treated as alive.
    """
    method = (override or {}).get("method", "HEAD").upper()
    async with semaphore:
        try:
            if method == "GET":
                resp = await client.get(
                    url, follow_redirects=True,
                    headers={"Range": "bytes=0-64"},
                )
            elif method == "OPTIONS":
                resp = await client.options(url, follow_redirects=True)
            else:
                resp = await client.head(url, follow_redirects=True)
                # Some servers return 405 for HEAD even when GET works. Retry
                # once with a small range request to avoid pulling a full body.
                # Skip the retry when the operator's override already
                # accepts 405 as alive — the explicit config wins.
                if resp.status_code in (405, 501):
                    if not _is_alive_per_override(resp.status_code, override):
                        resp = await client.get(
                            url, headers={"Range": "bytes=0-64"},
                        )
            return {
                "surface": surface,
                "url": url,
                "ok": _is_alive_per_override(resp.status_code, override),
                "status": resp.status_code,
                "detail": f"HTTP {resp.status_code}",
                "override_applied": bool(override),
                "override_reason": (override or {}).get("reason", ""),
            }
        except Exception as exc:
            return {
                "surface": surface,
                "url": url,
                "ok": False,
                "status": 0,
                "detail": f"{type(exc).__name__}: {str(exc)[:160]}",
                "override_applied": False,
                "override_reason": "",
            }


async def _load_target_overrides(pool) -> dict[str, dict[str, Any]]:
    """Read ``app_settings.operator_url_probe_target_overrides``.

    Returns a dict mapping app_setting key → override config dict.
    Missing setting / unparseable JSON / DB error all return ``{}``
    so the probe degrades to its default (strict 200–399 = alive)
    rather than crashing. The override file existing-but-broken is
    itself a state worth seeing in logs; we WARN on JSON parse errors
    so the operator notices.
    """
    try:
        raw = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "operator_url_probe_target_overrides",
        )
    except Exception as exc:
        logger.warning(
            "[OPERATOR_URL_PROBE] target-overrides read failed: %s", exc,
        )
        return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[OPERATOR_URL_PROBE] target-overrides JSON parse failed (%s) — "
            "ignoring overrides this cycle. Fix the JSON in app_settings.",
            exc,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "[OPERATOR_URL_PROBE] target-overrides isn't a JSON object "
            "(got %s) — ignoring.", type(parsed).__name__,
        )
        return {}
    return parsed


async def probe_urls(
    targets: list[dict[str, str]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Probe ``targets`` (list of {surface, url, key?, ...}) and return per-URL results.

    Returns one dict per input target. Failures are reported, not raised — the
    caller decides whether to escalate. ``httpx`` is required; if it's missing
    every target is reported as ``ok=False`` so the operator notices the dep
    is broken instead of silently passing.

    ``overrides`` (loaded by ``_load_target_overrides``) maps app_setting
    keys to per-URL probe behavior — see the migration docstring at
    ``20260510_152609_url_probe_per_target_overrides.py`` for the schema.
    Targets carrying a ``key`` field (i.e. those collected from
    ``app_settings``) get override lookup; dashboard-extracted targets
    keep the strict default.
    """
    if not targets:
        return []

    if httpx is None:  # pragma: no cover — only when dep is uninstalled
        logger.error(
            "[OPERATOR_URL_PROBE] httpx is not installed — cannot probe URLs"
        )
        return [
            {
                "surface": t["surface"],
                "url": t["url"],
                "ok": False,
                "status": 0,
                "detail": "httpx dependency missing",
            }
            for t in targets
        ]

    overrides = overrides or {}
    timeout = httpx.Timeout(HTTP_READ_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S)
    headers = {"User-Agent": _USER_AGENT}
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        # follow_redirects=True is set per-request so we still surface
        # redirects when a HEAD bounces; the per-request settings win.
    ) as client:
        coros = [
            _probe_one_url(
                client, semaphore, t["surface"], t["url"],
                override=overrides.get(t.get("key", "")),
            )
            for t in targets
        ]
        # return_exceptions guards against a misbehaving probe taking down the
        # whole gather — _probe_one_url is already exception-safe but defence
        # in depth keeps the cycle running.
        results = await asyncio.gather(*coros, return_exceptions=True)

    out: list[dict[str, Any]] = []
    for r, t in zip(results, targets):
        if isinstance(r, Exception):
            out.append({
                "surface": t["surface"],
                "url": t["url"],
                "ok": False,
                "status": 0,
                "detail": f"unexpected: {type(r).__name__}: {str(r)[:120]}",
            })
        else:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Top-level probe entry point
# ---------------------------------------------------------------------------


def _format_url_failure_detail(failure: dict[str, Any]) -> str:
    return (
        f"URL: {failure['url']}\n"
        f"Status: {failure['detail']}\n"
        f"Recommended fix: update the surface to point at a reachable URL, "
        f"or remove the link if the service was retired."
    )


def _format_drift_detail(drift: dict[str, str]) -> str:
    return (
        f"Device: {drift['name']}\n"
        f"DB tailscale_ip: {drift['db_ip']}\n"
        f"Live tailscale_ip: {drift['live_ip']}\n"
        f"Recommended fix:\n  {drift['fix']}"
    )


async def run_operator_url_probe(
    pool,
    *,
    dashboards_dir: Path | None = None,
    notify_fn=None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, Any]:
    """Single execution of the probe. Returns a summary dict.

    The brain daemon's periodic-cycle scheduler is the canonical caller (see
    ``_maybe_run_operator_url_probe``). Tests can call this directly.

    ``notify_fn`` defaults to :func:`brain.operator_notifier.notify_operator`
    but accepts an injected stub for tests. We cap notifications at 1 per
    surface per cycle so a fully-down stack doesn't blast Telegram.
    """
    dashboards_dir = dashboards_dir or DEFAULT_DASHBOARDS_DIR
    notify_fn = notify_fn or notify_operator

    # ---- 1) Collect all URL targets from the four sources -----------------
    dashboard_targets = extract_dashboard_links(dashboards_dir)
    appsetting_targets = await collect_app_setting_urls(pool)

    # Merge URL lists — keep the first surface that points at a given URL
    # so the failure notification has a clear "this is where the link
    # lives" anchor. (The same backend URL can appear in many panels.)
    all_targets: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for t in dashboard_targets + appsetting_targets:
        if t["url"] in seen_urls:
            continue
        seen_urls.add(t["url"])
        all_targets.append(t)

    # ---- 1b) Load per-URL probe overrides --------------------------------
    # See migration 20260510_152609 — operator-visible per-URL config that
    # widens alive_codes for outbound-only APIs (Google sitemap ping,
    # IndexNow, R2 public bucket etc.) where 4xx means "host alive,
    # request shape wrong" rather than "service down".
    overrides = await _load_target_overrides(pool)

    logger.info(
        "[OPERATOR_URL_PROBE] Probing %d URL(s) (%d dashboard, %d app_settings, "
        "%d with overrides)",
        len(all_targets), len(dashboard_targets), len(appsetting_targets),
        sum(1 for t in all_targets if t.get("key") in overrides),
    )

    # ---- 2) HTTP probe everything in parallel -----------------------------
    url_results = await probe_urls(
        all_targets, concurrency=concurrency, overrides=overrides,
    )

    # ---- 3) Tailscale drift detection -------------------------------------
    drift = await detect_tailscale_drift(pool)

    # ---- 4) Notify per-surface — cap at 1 per surface per cycle ----------
    notified: set[str] = set()
    failures = [r for r in url_results if not r["ok"]]
    for failure in failures:
        if failure["surface"] in notified:
            continue
        notified.add(failure["surface"])
        try:
            notify_fn(
                title=f"Operator surface unreachable: {failure['surface']}",
                detail=_format_url_failure_detail(failure),
                source="brain.operator_url_probe",
                severity="warning",
            )
        except Exception as exc:  # notify must never crash the cycle
            logger.warning(
                "[OPERATOR_URL_PROBE] notify_fn failed for %s: %s",
                failure["surface"], exc,
            )

    for d in drift:
        if d["surface"] in notified:
            continue
        notified.add(d["surface"])
        try:
            notify_fn(
                title=f"Tailscale IP drift: {d['name']}",
                detail=_format_drift_detail(d),
                source="brain.operator_url_probe",
                severity="warning",
            )
        except Exception as exc:
            logger.warning(
                "[OPERATOR_URL_PROBE] notify_fn failed for %s: %s",
                d["surface"], exc,
            )

    summary = {
        "total_urls_probed": len(url_results),
        "url_failures": len(failures),
        "tailscale_drift_count": len(drift),
        "notifications_sent": len(notified),
        # Keep the failing surfaces in the summary for callers who want to
        # log them or persist into brain_knowledge.
        "failing_surfaces": [
            {"surface": f["surface"], "url": f["url"], "detail": f["detail"]}
            for f in failures
        ],
        "drifted_devices": drift,
    }
    logger.info(
        "[OPERATOR_URL_PROBE] Cycle complete: %d/%d URLs failing, %d drifted device(s), %d notifications",
        summary["url_failures"], summary["total_urls_probed"],
        summary["tailscale_drift_count"], summary["notifications_sent"],
    )
    return summary


async def maybe_run_operator_url_probe(pool, *, notify_fn=None) -> dict[str, Any] | None:
    """Run the probe iff its 15-minute interval has elapsed.

    Designed to be called from the brain daemon's main cycle (which runs
    every 5 minutes). The interval gate keeps the probe at #214's "every
    ~15 min" cadence without spinning up a separate scheduler.

    Returns the summary dict on a real run, ``None`` when the gate skips.
    """
    global _last_run_ts
    import time as _time
    now = _time.time()
    if (now - _last_run_ts) < PROBE_INTERVAL_SECONDS:
        return None
    _last_run_ts = now
    try:
        return await run_operator_url_probe(pool, notify_fn=notify_fn)
    except Exception as exc:
        # Defence in depth — the brain cycle's _step wrapper will catch this
        # too, but logging here gives the failure context (the probe name)
        # without the operator having to grep brain_decisions.
        logger.warning(
            "[OPERATOR_URL_PROBE] probe crashed: %s", exc, exc_info=True
        )
        return {"error": f"{type(exc).__name__}: {exc}"}
