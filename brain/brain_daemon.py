"""
Glad Labs Big Brain — standalone daemon.

Independent of FastAPI, OpenClaw, Ollama, and the worker.
Only dependency: Python + asyncpg + PostgreSQL.

Runs as its own process. If everything else dies, the brain survives.

Functions:
  1. Monitors all other services (FastAPI, worker, OpenClaw, Vercel)
  2. Processes its own reasoning queue (brain_queue)
  3. Self-maintains knowledge graph (expire stale, resolve contradictions)
  4. Generates proactive insights
  5. Sends alerts when services are down
  6. Can trigger restarts of other services

Usage:
    python brain/brain_daemon.py                # Run forever
    python brain/brain_daemon.py --once         # Run one cycle and exit
    pythonw brain/brain_daemon.py               # Run windowless (background)
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import urllib.error
import urllib.request
import time
from datetime import datetime, timezone

# Standalone — no imports from the FastAPI codebase
import asyncpg

from health_probes import run_health_probes
from seed_loader import seed_app_settings

# Brain-local secret reader — single source of truth for app_settings
# decryption (closes Glad-Labs/poindexter#342). Both flat and
# package-qualified imports are tried so this module works both in the
# Docker container (brain/ on PYTHONPATH) and in the test harness
# (where `from brain import ...` is the canonical path).
#
# The module is named secret_reader (not secrets) to avoid shadowing
# Python's stdlib ``secrets`` module on the brain's PYTHONPATH.
try:
    from secret_reader import read_app_setting as _read_app_setting
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.secret_reader import read_app_setting as _read_app_setting

try:
    # Flat import when brain/ is on sys.path (container runtime).
    from alert_sync import sync_alert_rules
except ImportError:  # pragma: no cover — package-qualified import for tests
    from brain.alert_sync import sync_alert_rules

try:
    from business_probes import run_business_probes
    _HAS_BUSINESS_PROBES = True
except ImportError:
    _HAS_BUSINESS_PROBES = False

# OAuth client for authenticated worker-API calls (#245). Pulled in
# lazily inside main() so the import is independent of the probe
# imports above (and the brain still boots if httpx is somehow
# unavailable — every other call path here uses urllib).
try:
    from oauth_client import oauth_client_from_pool, BRAIN_DEFAULT_SCOPES
    _HAS_OAUTH_CLIENT = True
except ImportError:  # pragma: no cover — package-qualified path for tests
    try:
        from brain.oauth_client import oauth_client_from_pool, BRAIN_DEFAULT_SCOPES
        _HAS_OAUTH_CLIENT = True
    except ImportError:
        _HAS_OAUTH_CLIENT = False

try:
    # GH#214 — operator-facing URL/IP drift probe. Runs on its own 15-min
    # cadence, gated inside maybe_run_operator_url_probe so we don't need
    # a separate scheduler.
    from operator_url_probe import maybe_run_operator_url_probe
    _HAS_OPERATOR_URL_PROBE = True
except ImportError:  # pragma: no cover — package-qualified for tests
    try:
        from brain.operator_url_probe import maybe_run_operator_url_probe
        _HAS_OPERATOR_URL_PROBE = True
    except ImportError:
        _HAS_OPERATOR_URL_PROBE = False

try:
    # GH#228 — migration-drift probe + auto-restart workflow. Runs on
    # the standard 5-min cycle; checks worker /api/health migrations
    # block, optionally restarts the worker if drift is detected and
    # the operator opted in via app_settings.
    from migration_drift_probe import run_migration_drift_probe
    _HAS_MIGRATION_DRIFT_PROBE = True
except ImportError:  # pragma: no cover — package-qualified for tests
    try:
        from brain.migration_drift_probe import run_migration_drift_probe
        _HAS_MIGRATION_DRIFT_PROBE = True
    except ImportError:
        _HAS_MIGRATION_DRIFT_PROBE = False

try:
    # GH#213 — compose-spec drift probe + optional auto-recreate. Runs
    # on the standard 5-min cycle; reads docker-compose.local.yml from
    # the bind-mounted path, compares against `docker inspect` for each
    # service, and notifies (or recreates) on drift.
    from compose_drift_probe import run_compose_drift_probe
    _HAS_COMPOSE_DRIFT_PROBE = True
except ImportError:  # pragma: no cover — package-qualified for tests
    try:
        from brain.compose_drift_probe import run_compose_drift_probe
        _HAS_COMPOSE_DRIFT_PROBE = True
    except ImportError:
        _HAS_COMPOSE_DRIFT_PROBE = False

try:
    # Writes Prometheus scrape secrets (uptime_kuma_api_key, etc.) to
    # the bind-mounted secrets dir so prometheus.yml's `password_file:`
    # directives find a fresh value on every scrape. Replaces the old
    # "literal placeholder + manual edit" workflow.
    from prometheus_secret_writer import write_prometheus_secrets
    _HAS_PROMETHEUS_SECRET_WRITER = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.prometheus_secret_writer import write_prometheus_secrets
        _HAS_PROMETHEUS_SECRET_WRITER = True
    except ImportError:
        _HAS_PROMETHEUS_SECRET_WRITER = False

try:
    # GlitchTip triage probe — pulls open issues every cycle, auto-resolves
    # known noise, pages on novel high-count issues. Config is DB-driven
    # via app_settings (seeded by migration 0133). No-ops without an API
    # token configured.
    from glitchtip_triage_probe import run_glitchtip_triage_probe
    _HAS_GLITCHTIP_TRIAGE_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.glitchtip_triage_probe import run_glitchtip_triage_probe
        _HAS_GLITCHTIP_TRIAGE_PROBE = True
    except ImportError:
        _HAS_GLITCHTIP_TRIAGE_PROBE = False

try:
    # Alert dispatcher — polls alert_events for undispatched rows and
    # routes them to Telegram/Discord. Replaces the worker-side inline
    # dispatch path; the webhook handler now persists only.
    # See brain/alert_dispatcher.py + Glad-Labs/poindexter#340.
    from alert_dispatcher import poll_and_dispatch as _poll_alert_events
    _HAS_ALERT_DISPATCHER = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.alert_dispatcher import poll_and_dispatch as _poll_alert_events
        _HAS_ALERT_DISPATCHER = True
    except ImportError:
        _HAS_ALERT_DISPATCHER = False

try:
    # GH#388 — backup-watcher probe + auto-retry. Stats the host-side
    # bind-mount used by the in-stack backup tiers (#385); on staleness,
    # `docker restart`s the responsible container before letting the
    # alert_dispatcher page the operator. Auto-resolves the original
    # alert via a status='resolved' row when the dump self-heals.
    from backup_watcher import run_backup_watcher_probe
    _HAS_BACKUP_WATCHER = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.backup_watcher import run_backup_watcher_probe
        _HAS_BACKUP_WATCHER = True
    except ImportError:
        _HAS_BACKUP_WATCHER = False

try:
    # GH#387 — SMART monitor probe. Polls smartctl per drive, parses
    # for warning/critical attributes (reallocated sectors, pending
    # sectors, SSD wear, SMART self-test failure), and writes
    # alert_events rows on regression. Degrades gracefully when
    # smartctl isn't installed (one-time notify, status='skipped').
    from smart_monitor import run_smart_monitor_probe
    _HAS_SMART_MONITOR = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.smart_monitor import run_smart_monitor_probe
        _HAS_SMART_MONITOR = True
    except ImportError:
        _HAS_SMART_MONITOR = False

try:
    # GH#222 — Docker port-forward stuck-state probe. Detects the
    # Windows wslrelay → com.docker.backend forwarding chain getting
    # stuck (TCP up, HTTP empty-reply via host.docker.internal, fine
    # via container hostname) and auto-recovers via `docker restart`.
    # Capped at N restarts per M-minute window per container so a
    # genuinely-broken service doesn't spin in a restart loop.
    from docker_port_forward_probe import run_docker_port_forward_probe
    _HAS_DOCKER_PORT_FORWARD_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.docker_port_forward_probe import run_docker_port_forward_probe
        _HAS_DOCKER_PORT_FORWARD_PROBE = True
    except ImportError:
        _HAS_DOCKER_PORT_FORWARD_PROBE = False

try:
    # GH#338 — auto-expire stale pending approval gates. Pulls every
    # post_approval_gates row in state='pending' older than
    # gate_pending_max_age_hours (default 168 = 7d), transitions them
    # to rejected with the sentinel ``auto_rejected_after_<N>_hours``
    # reason, writes one pipeline_gate_history + one audit_log row per
    # cycle, and sends a single coalesced Telegram notification when
    # the batch meets the configurable threshold.
    from gate_auto_expire_probe import run_gate_auto_expire_probe
    _HAS_GATE_AUTO_EXPIRE_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.gate_auto_expire_probe import run_gate_auto_expire_probe
        _HAS_GATE_AUTO_EXPIRE_PROBE = True
    except ImportError:
        _HAS_GATE_AUTO_EXPIRE_PROBE = False

try:
    # GH#338 — coalesced "N posts pending review" summary probe.
    # SELECTs count + oldest age from post_approval_gates each cycle and
    # sends ONE Telegram page per gate_pending_summary_telegram_dedup_minutes
    # window (default 60 min) when the queue is non-empty AND the oldest
    # gate is older than gate_pending_summary_min_age_minutes (default 60).
    # Inside the dedup window it re-pages only when the queue grew by
    # strictly more than gate_pending_summary_telegram_growth_threshold
    # (default 3) new gates. Optional low-noise Discord queue-status fires
    # every cycle (gate_pending_summary_discord_per_cycle, default true).
    # Pairs with the per-flip Telegram demotion in
    # services/gates/post_approval_gates.notify_gate_pending.
    from gate_pending_summary_probe import run_gate_pending_summary_probe
    _HAS_GATE_PENDING_SUMMARY_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.gate_pending_summary_probe import run_gate_pending_summary_probe
        _HAS_GATE_PENDING_SUMMARY_PROBE = True
    except ImportError:
        _HAS_GATE_PENDING_SUMMARY_PROBE = False

try:
    # PR staleness probe — every cycle, pull open PRs from GitHub and
    # flag any that have been sitting >24h with green CI but no merge.
    # Catches the "agent shipped a PR and the operator forgot" failure
    # mode. Routes a single coalesced Discord-ops alert per cycle.
    # All thresholds are DB-configurable via app_settings (master switch:
    # pr_staleness_probe_enabled). See brain/pr_staleness_probe.py.
    from pr_staleness_probe import run_pr_staleness_probe
    _HAS_PR_STALENESS_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.pr_staleness_probe import run_pr_staleness_probe
        _HAS_PR_STALENESS_PROBE = True
    except ImportError:
        _HAS_PR_STALENESS_PROBE = False

try:
    # poindexter#435 — Discord bot reachability probe. Pings
    # https://discord.com/api/v10/users/@me with the bot token every
    # configured interval (default 5 min). Pages the operator on 401/403
    # (token revoked); 5xx + network errors are transient and only logged.
    # Symmetric to the Telegram getMe check in claude-code-watchdog.ps1.
    from discord_bot_probe import run_discord_bot_probe
    _HAS_DISCORD_BOT_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.discord_bot_probe import run_discord_bot_probe
        _HAS_DISCORD_BOT_PROBE = True
    except ImportError:
        _HAS_DISCORD_BOT_PROBE = False

try:
    # poindexter#434 — MCP HTTP server (:8004) liveness probe. Pings
    # the OAuth discovery endpoint every configurable interval; alerts
    # on unreachable/5xx and optionally invokes the operator-configured
    # launcher script for auto-recovery (capped at N restarts per window).
    from mcp_http_probe import run_mcp_http_probe
    _HAS_MCP_HTTP_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.mcp_http_probe import run_mcp_http_probe
        _HAS_MCP_HTTP_PROBE = True
    except ImportError:
        _HAS_MCP_HTTP_PROBE = False


# --- Boot-time import audit (poindexter#504) ---------------------------------
#
# Each _HAS_* flag above is set by a try/except ImportError block. Every
# one of those modules SHIPS with the brain container (see
# brain/pyproject.toml + brain/Dockerfile COPY). If any flag is False at
# boot, that's a packaging regression — the brain image was built
# against a broken file copy or someone deleted a module without
# updating the import list. Loud-fail per feedback_no_silent_defaults
# so the operator notices instead of running with degraded probe
# coverage indefinitely.
#
# Called from main() AFTER config load so notify_operator's Telegram +
# Discord paths can read their credentials.

# Tuple shape: (flag_name, module_filename, what_breaks_when_missing).
# The module_filename is informational — used in the notify body so the
# operator knows which file to chase. Keep this list in sync with the
# _HAS_* try/except blocks above; the boot audit is only useful if it
# reflects current expectations.
_BRAIN_REQUIRED_MODULES: tuple[tuple[str, str, str], ...] = (
    ("_HAS_BUSINESS_PROBES", "brain/business_probes.py",
     "Mercury balance / GitHub PR / Discord webhook probes silently skip"),
    ("_HAS_OAUTH_CLIENT", "brain/oauth_client.py",
     "Authenticated worker-API calls skip — every probe that hits a protected endpoint is dark"),
    ("_HAS_OPERATOR_URL_PROBE", "brain/operator_url_probe.py",
     "Operator-facing URL/IP drift detection disabled"),
    ("_HAS_MIGRATION_DRIFT_PROBE", "brain/migration_drift_probe.py",
     "Worker migration-state drift goes unmonitored — schema regressions stay invisible"),
    ("_HAS_COMPOSE_DRIFT_PROBE", "brain/compose_drift_probe.py",
     "docker-compose vs running-container drift goes unmonitored"),
    ("_HAS_PROMETHEUS_SECRET_WRITER", "brain/prometheus_secret_writer.py",
     "Prometheus scrape secrets stop refreshing — Uptime-Kuma / GitHub / etc. scrapes start 401-ing"),
    ("_HAS_GLITCHTIP_TRIAGE_PROBE", "brain/glitchtip_triage_probe.py",
     "GlitchTip noise triage stops — known-issue auto-resolve dies, novel-issue paging dies"),
    ("_HAS_ALERT_DISPATCHER", "brain/alert_dispatcher.py",
     "alert_events rows pile up undispatched — Telegram/Discord stop receiving pages"),
    ("_HAS_BACKUP_WATCHER", "brain/backup_watcher.py",
     "Backup-staleness alerts go dark — 33h stale dumps go unnoticed (#388 redux)"),
    ("_HAS_SMART_MONITOR", "brain/smart_monitor.py",
     "SMART drive-health monitoring offline — failing drives detected only by total loss"),
    ("_HAS_DOCKER_PORT_FORWARD_PROBE", "brain/docker_port_forward_probe.py",
     "Windows wslrelay stuck-state auto-recovery offline (#222)"),
    ("_HAS_GATE_AUTO_EXPIRE_PROBE", "brain/gate_auto_expire_probe.py",
     "Stale pending approval gates stop auto-expiring — queue grows unboundedly"),
    ("_HAS_GATE_PENDING_SUMMARY_PROBE", "brain/gate_pending_summary_probe.py",
     "Pending-queue daily summary Telegram pages stop"),
    ("_HAS_PR_STALENESS_PROBE", "brain/pr_staleness_probe.py",
     "Stale PR detection offline — agent PRs sit unmerged forever"),
    ("_HAS_DISCORD_BOT_PROBE", "brain/discord_bot_probe.py",
     "Discord bot uptime monitor offline"),
    ("_HAS_MCP_HTTP_PROBE", "brain/mcp_http_probe.py",
     "MCP HTTP server reachability monitor offline"),
)


def _audit_brain_module_imports() -> None:
    """Inspect every _HAS_* flag; notify_operator on any packaging
    regression. Called from main() after config + Pyroscope are up.

    Best-effort: never raises, never blocks boot. Per
    feedback_no_silent_defaults: WARN logs are silent in practice, so
    this routes through notify_operator (Telegram + Discord +
    alerts.log) when something IS broken.
    """
    missing: list[tuple[str, str, str]] = []
    for flag_name, module_file, breaks_when_missing in _BRAIN_REQUIRED_MODULES:
        # Read from this module's globals so we don't have to thread
        # every flag through the function signature.
        flag_value = globals().get(flag_name)
        if flag_value is False:
            missing.append((flag_name, module_file, breaks_when_missing))

    if not missing:
        logger.info(
            "[BRAIN] Boot import audit clean (%d expected-present modules importable)",
            len(_BRAIN_REQUIRED_MODULES),
        )
        return

    for flag_name, module_file, breaks in missing:
        logger.error(
            "[BRAIN] PACKAGING REGRESSION: %s is False (%s missing) — %s",
            flag_name, module_file, breaks,
        )
    detail_lines = [
        f"• {module_file}: {breaks} (flag {flag_name})"
        for flag_name, module_file, breaks in missing
    ]
    try:
        from operator_notifier import notify_operator
    except ImportError:
        try:
            from brain.operator_notifier import notify_operator  # type: ignore[no-redef]
        except ImportError:
            logger.warning(
                "[BRAIN] operator_notifier unavailable — %d packaging "
                "regressions logged but NOT paged", len(missing),
            )
            return
    try:
        notify_operator(
            title=(
                f"Brain daemon boot audit failed — "
                f"{len(missing)} probe{'s' if len(missing) != 1 else ''} dark"
            ),
            detail=(
                "Brain daemon started but expected-present modules failed "
                "to import. These ship in the brain container and missing "
                "ones indicate a packaging regression.\n\n"
                + "\n".join(detail_lines)
                + "\n\nFix: pull latest, rebuild brain image: "
                "`docker compose build brain-daemon && docker compose "
                "up -d brain-daemon`."
            ),
            source="brain:boot-audit",
            severity="error",
        )
    except Exception as exc:  # noqa: BLE001 — notify is best-effort
        logger.warning("[BRAIN] notify_operator raised: %s", exc)


LOG_DIR = os.path.join(os.path.expanduser("~"), os.getenv("APP_LOG_DIR", ".content-pipeline"))
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "brain.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("brain")

# Local brain DB — the daemon writes ALL data here (brain_knowledge, brain_decisions, etc.)
# #198: resolve via bootstrap helper so ~/.poindexter/bootstrap.toml or any
# of DATABASE_URL / LOCAL_DATABASE_URL / POINDEXTER_MEMORY_DSN works. If
# none of those yield a value, require_database_url() notifies the operator
# and exits cleanly.
from brain.bootstrap import require_database_url

LOCAL_BRAIN_DB = require_database_url(source="brain_daemon")

# Telegram + Discord notification config — NOT cached at module level.
#
# Glad-Labs/poindexter#344: the brain image's Dockerfile mirrors files
# into both ``/app`` (flat layout) and ``/app/brain/`` (package layout)
# so ``from brain.X import Y`` and ``from X import Y`` both resolve.
# That import duality is fine for stateless helpers, but module-level
# globals (``TELEGRAM_BOT_TOKEN = ""``) created TWO independent copies
# of the secret cache — one per module instance — and ``_load_config_from_db``
# only updated the instance it was called from. Whichever path the
# alert_dispatcher used to reach ``send_telegram`` read the empty copy
# and silently dropped every page.
#
# Fix: ``send_telegram``/``send_discord`` re-fetch their secrets from
# app_settings on every call via ``read_app_setting`` (which decrypts
# the pgcrypto envelope, see #342). One DB roundtrip per alert is
# negligible — the dispatcher polls every 30s and notify is rare.
# The bootstrap-via-env path still works because ``read_app_setting``
# returns the supplied default when the row is missing, and we pass
# the env var as the default below.
#
# Detect Docker (set in docker-compose.local.yml)
IS_DOCKER = bool(os.getenv("IN_DOCKER"))

# Service URLs — loaded from DB at startup via _load_config_from_db().
# Initial values come from env only; no hardcoded localhost fallback (#198).
# The daemon's first monitoring cycle replaces these with app_settings values.
_SITE_URL = os.getenv("SITE_URL", "")
_API_BASE_URL = os.getenv("API_BASE_URL", "")

SERVICES = {
    "site": {"url": _SITE_URL, "type": "http", "critical": True},
    "api": {"url": _API_BASE_URL + "/api/health", "type": "json_status", "critical": True},
}


async def _load_config_from_db(pool):
    """Load non-secret identity config from app_settings.

    Pulls the operator-facing service URLs (``site_url``, ``api_base_url``)
    so the monitor cycle can probe the right hosts. The Telegram/Discord
    notification secrets used to be cached here too, but
    Glad-Labs/poindexter#344 traced an alert-dispatch outage to the
    module-instance landmine (two copies of ``TELEGRAM_BOT_TOKEN``, only
    one populated by this function). Those secrets are now lazy-fetched
    inline by ``send_telegram`` / ``send_discord`` via
    ``read_app_setting`` so there's nothing to cache and nothing to drift.
    """
    global _SITE_URL, _API_BASE_URL
    try:
        # Plaintext keys — one round-trip via the cheap SELECT.
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE key IN "
            "('site_url', 'api_base_url')"
        )
        config = {r["key"]: r["value"] for r in rows}
        if config.get("site_url"):
            _SITE_URL = config["site_url"]
            SERVICES["site"]["url"] = _SITE_URL
        if config.get("api_base_url"):
            _API_BASE_URL = config["api_base_url"]
            SERVICES["api"]["url"] = _API_BASE_URL + "/api/health"

        logger.info("[BRAIN] Loaded %d config values from DB", len(config))
    except Exception as e:
        logger.warning("[BRAIN] Could not load config from DB: %s (using defaults)", e, exc_info=True)

async def _setting_int(pool, key: str, default: int) -> int:
    """Read an integer app_settings value. Brain daemon is standalone
    (no site_config) so it hits the DB directly. Falls back to default
    if the row is missing or unparseable. (#198)
    """
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError, Exception):
        return default


async def _setup_brain_pyroscope(pool, service_name: str = "poindexter-brain") -> None:
    """Configure the pyroscope-io agent for the brain daemon.

    The brain is standalone (no SiteConfig DI seam, no
    services.profiling import) so it reads ``enable_pyroscope`` /
    ``pyroscope_server_url`` / ``environment`` directly from
    app_settings via the existing pool. Mirrors the worker-side
    services/profiling.py logic — opt-in gate, missing-package
    graceful path, structured success log line — so operators see the
    same breadcrumb regardless of which service shipped the profile.

    Closes Glad-Labs/poindexter#406. Best-effort: any failure is
    logged at WARNING and swallowed; the daemon must never refuse to
    start because the profiler couldn't decide whether to run.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT key, value FROM app_settings
            WHERE key IN ('enable_pyroscope', 'pyroscope_server_url', 'environment')
            """
        )
        cfg = {r["key"]: r["value"] for r in rows}
    except Exception as e:  # noqa: BLE001 — DB readiness is the broader concern
        logger.debug("[PYROSCOPE] could not read app_settings: %s — skipping", e)
        return

    enabled = (cfg.get("enable_pyroscope") or "false").lower() == "true"
    if not enabled:
        logger.debug("[PYROSCOPE] disabled via app_settings.enable_pyroscope")
        return

    server_url = cfg.get("pyroscope_server_url") or "http://pyroscope:4040"
    environment = cfg.get("environment") or "development"

    try:
        import pyroscope  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "[PYROSCOPE] pyroscope-io not installed but enable_pyroscope=true "
            "(brain). Install the brain image's profiling group with: "
            "poetry install --with profiling",
        )
        return

    try:
        pyroscope.configure(
            application_name=service_name,
            server_address=server_url,
            tags={
                "service": service_name,
                "environment": environment,
            },
        )
        logger.info(
            "[PYROSCOPE] agent configured — app=%s server=%s env=%s",
            service_name, server_url, environment,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[PYROSCOPE] configure failed (brain): %s", e, exc_info=True,
        )


# Local services always monitored (Poindexter runs on the operator's own machine).
# In Docker, other containers are on the Docker network; host services use host.docker.internal.
_local_host = "host.docker.internal" if IS_DOCKER else "localhost"
# Worker is a sibling container in Docker — use its container name.
_worker_host = "poindexter-worker" if IS_DOCKER else "localhost"
SERVICES.update({
    "worker": {"url": f"http://{_worker_host}:8002/api/health", "type": "json_status", "critical": False},
    "openclaw": {"url": f"http://{_local_host}:18789/status", "type": "http", "critical": False},
    "nvidia_exporter": {"url": "http://poindexter-prometheus:9090/-/healthy" if IS_DOCKER else f"http://{_local_host}:9835/metrics", "type": "http", "critical": False},
    "windows_exporter": {"url": f"http://{_local_host}:9182/metrics", "type": "http", "critical": False},
})

# External service status pages (always monitored from anywhere)
EXTERNAL_SERVICES = {
    "github": {
        "url": "https://www.githubstatus.com/api/v2/status.json",
        "type": "statuspage",  # Atlassian Statuspage format
    },
    "vercel": {
        "url": "https://www.vercel-status.com/api/v2/status.json",
        "type": "statuspage",
    },
    "anthropic": {
        # Re-added 2026-05-16 after a partial outage (claude.ai + API +
        # Console + Code + Cowork all Partial Outage simultaneously)
        # killed two cleanup-sweep agents mid-work and was only visible
        # via Matt manually checking status.anthropic.com on his phone.
        # The pipeline now relies on Claude for: dispatched cleanup
        # agents, MCP server consumers, and the dev_diary narrative
        # atom when premium tier is enabled. Outage awareness is no
        # longer optional.
        #
        # Status page uses Atlassian Statuspage v2 API at both
        # status.anthropic.com and status.claude.com (they share an
        # underlying page).
        "url": "https://status.anthropic.com/api/v2/status.json",
        "type": "statuspage",
    },
    # grafana_cloud removed — using local Grafana now (localhost:3000)
}

# Track previous external status to detect transitions
_prev_external_status = {}

CYCLE_SECONDS = 300  # 5 minutes between full cycles

# Alert dispatch poll cadence — Grafana → webhook → alert_events rows are
# time-sensitive (operator-facing pages), so we poll faster than the main
# 5-min cycle. 30s gives near-real-time delivery without hammering the DB
# while idle. The loop runs as its own asyncio task so a slow dispatch
# never stalls the main cycle.
ALERT_DISPATCH_INTERVAL_SECONDS = 30

# GH-28: Grafana alert sync cadence counter. Incremented each run_cycle;
# sync_alert_rules fires when the counter hits grafana_alert_sync_interval_cycles
# (default 3 = every 15 min). Reset to 0 after each sync.
_alert_sync_cycle_counter = 0

# OAuth client used for any authenticated worker-API call brain probes
# need to make. Initialised once in main() after the DB pool is ready.
# OAuth credentials are required (Phase 3 / Glad-Labs/poindexter#249
# removed the static-Bearer fallback); the helper raises loudly if
# `brain_oauth_client_id` / `_secret` aren't provisioned via
# `poindexter auth migrate-brain`.
_OAUTH_CLIENT = None


def get_oauth_client():
    """Return the brain's shared OAuth client, or ``None`` before init.

    Exposed so probes can opt into authenticated calls without needing
    to thread the pool through their signatures. Probes that don't
    need authentication (most of them — they hit /api/health which is
    open) keep using ``urllib`` directly.
    """
    return _OAUTH_CLIENT


# Vercel's edge protection (BotID + bot-blocking ruleset) returns 403
# for the default Python-urllib UA, which made the brain repeatedly
# alert "Service site is DOWN: Forbidden" against a perfectly healthy
# www.gladlabs.io. Identify as the brain probe so the public site
# treats us like any other infra check. Same fix as
# health_probes._http_json — keeping the helpers consistent.
_PROBE_UA = "brain-probe"


def check_http(url: str, timeout: int = 10) -> tuple:
    """Check if an HTTP endpoint responds. Returns (ok, status_code, detail)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _PROBE_UA})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, resp.status, "ok"
    except urllib.error.HTTPError as e:
        return False, e.code, str(e.reason)[:100]
    except Exception as e:
        return False, 0, str(e)[:100]


def check_statuspage(url: str, timeout: int = 10) -> tuple:
    """Check an Atlassian Statuspage API. Returns (ok, indicator, description)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _PROBE_UA})
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        indicator = data.get("status", {}).get("indicator", "unknown")
        description = data.get("status", {}).get("description", "unknown")
        ok = indicator == "none"  # "none" = all systems operational
        return ok, indicator, description
    except Exception as e:
        return False, "unreachable", str(e)[:100]


def check_instatus(url: str, timeout: int = 10) -> tuple:
    """Check an Instatus summary endpoint. Returns (ok, status, description)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _PROBE_UA})
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        status = data.get("page", {}).get("status", "UNKNOWN")
        ok = status in ("UP", "HASISSUES")  # UP = good, HASISSUES = degraded but alive
        return ok, status.lower(), status
    except Exception as e:
        return False, "unreachable", str(e)[:100]


def check_json_status(url: str, timeout: int = 10) -> tuple:
    """Check a JSON health endpoint. Returns (ok, status, detail)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _PROBE_UA})
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        status = data.get("status", "unknown")
        ok = status in ("healthy", "degraded", "ok")
        return ok, 200, status
    except Exception as e:
        return False, 0, str(e)[:100]


# Cross-module-instance pool registry.
#
# The brain Docker image mirrors source files into both ``/app`` (flat)
# and ``/app/brain/`` (package) so two import paths resolve. Module-level
# variables on this file therefore live in TWO namespaces — see the
# Glad-Labs/poindexter#344 fix for the gory details. ``sys.modules`` is
# the only namespace guaranteed to be shared, so we stash the daemon's
# pool on a sentinel ``ModuleType`` keyed there. Both module instances
# look up the same key and read back the same pool, even when ``main()``
# only ran on one of them.
_POOL_REGISTRY_KEY = "_brain_daemon_pool_registry"


def _set_brain_pool(pool) -> None:
    """Register the brain's main asyncpg pool for cross-instance reads.

    Called from ``main()`` once the pool is alive. ``send_telegram`` /
    ``send_discord`` / ``notify`` look this up when no explicit ``pool=``
    arg is supplied, so callers in restart_service-style sync paths
    (which can't easily thread a pool through) still get a real pool to
    decrypt secrets with.
    """
    import types
    holder = sys.modules.get(_POOL_REGISTRY_KEY)
    if holder is None:
        holder = types.ModuleType(_POOL_REGISTRY_KEY)
        sys.modules[_POOL_REGISTRY_KEY] = holder
    holder.pool = pool


async def _resolve_pool(pool):
    """Return ``pool`` if provided, else the registered brain pool, else None.

    No-op when called with an explicit pool. The registry lookup tolerates
    a missing holder (e.g. when ``send_telegram`` is invoked from a unit
    test that never called ``_set_brain_pool``) and returns ``None`` so
    the caller can fall through to its "no pool" branch.
    """
    if pool is not None:
        return pool
    holder = sys.modules.get(_POOL_REGISTRY_KEY)
    if holder is None:
        return None
    return getattr(holder, "pool", None)


async def send_telegram(
    message: str,
    *,
    pool=None,
    reply_to_message_id: int | None = None,
) -> int | None:
    """Send alert to Telegram — direct bot API, no dependencies.

    Returns the Telegram ``message_id`` (int) on success, or ``None`` on
    any failure (no token, malformed URL, transport error, non-2xx
    response). The message_id is what Glad-Labs/poindexter#347 step 5
    needs so the firefighter follow-up can quote-reply the original
    alert in the same Telegram thread.

    Args:
        message: Body text. Prefixed with "🧠 Brain: " before send.
        pool: Optional asyncpg pool. When ``None``, falls back to
            ``_resolve_pool`` to discover the cross-instance pool.
        reply_to_message_id: When set, Telegram threads the new message
            as a quote-reply to the supplied message id. Used by the
            firefighter ops follow-up (#347 step 5) so the diagnosis
            lands under the raw alert in the operator's chat.

    History — #344: this function previously read a module-level
    ``TELEGRAM_BOT_TOKEN`` global. The brain Docker image mirrors files
    into both ``/app`` and ``/app/brain/`` so a process that imports
    ``brain.brain_daemon`` AND has ``brain_daemon`` in ``sys.modules``
    sees TWO module instances each with their own copy of the global.
    The fix is to re-read the secret from app_settings on every call
    via ``read_app_setting`` (one DB roundtrip per alert is negligible).

    History — #342: callers like ``alert_dispatcher`` rely on the
    success/failure signal to set ``alert_events.dispatch_result``
    honestly instead of recording every cycle as ``'sent'``. The new
    return shape (``int | None``) preserves that contract — ``None`` is
    falsy and the dispatcher's adapter still raises ``NotifyFailed``
    when no channel accepts.
    """
    pool = await _resolve_pool(pool)
    if pool is None:
        logger.warning(
            "[BRAIN] No DB pool available — can't fetch telegram_bot_token"
        )
        return None

    token = await _read_app_setting(
        pool, "telegram_bot_token", default=os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    chat_id = await _read_app_setting(
        pool, "telegram_chat_id", default=os.getenv("TELEGRAM_CHAT_ID", "")
    )
    if not token:
        logger.warning("[BRAIN] No Telegram bot token — can't send alert")
        return None
    if not chat_id:
        logger.warning("[BRAIN] No Telegram chat ID — can't send alert")
        return None
    try:
        body: dict[str, object] = {
            "chat_id": chat_id,
            "text": f"🧠 Brain: {message}",
        }
        if reply_to_message_id is not None:
            # Telegram Bot API: ``reply_to_message_id`` threads the new
            # message under the referenced one. Coupled with
            # ``allow_sending_without_reply=True`` so a deleted parent
            # doesn't make the API reject the follow-up — the operator
            # gets the diagnosis even if the original alert message was
            # cleaned up.
            body["reply_to_message_id"] = int(reply_to_message_id)
            body["allow_sending_without_reply"] = True
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        # urllib is sync — run in a thread so we don't block the event loop.
        def _do_post():
            return urllib.request.urlopen(req, timeout=10)
        resp = await asyncio.to_thread(_do_post)
        if not (200 <= resp.status < 300):
            return None
        # Parse the message_id out of the response so callers can thread
        # follow-ups against it. Telegram returns
        # ``{"ok": true, "result": {"message_id": N, ...}}``. Unparseable
        # bodies degrade to "1" so we still report success — the brain's
        # alert_dispatcher only needs truthiness, the firefighter follow-up
        # is a best-effort enhancement.
        try:
            response_text = await asyncio.to_thread(resp.read)
            if isinstance(response_text, bytes):
                response_data = json.loads(response_text.decode("utf-8"))
                if isinstance(response_data, dict):
                    message_id = response_data.get("result", {}).get("message_id")
                    if isinstance(message_id, int):
                        return message_id
        except Exception as parse_err:  # noqa: BLE001 — best-effort parse
            logger.debug(
                "[BRAIN] Telegram response parse failed: %s — "
                "send succeeded but message_id unavailable",
                parse_err,
            )
        # Sentinel — non-zero falsy-safe value preserves the "send
        # accepted" semantics for the dispatcher when message_id parsing
        # fails (e.g. mocked responses without a body).
        return 1
    except Exception as e:
        logger.error("[BRAIN] Telegram send failed: %s", e, exc_info=True)
        return None


async def send_discord(
    message: str,
    webhook_url: str | None = None,
    *,
    pool=None,
    message_reference_id: str | int | None = None,
) -> str | None:
    """Send message to Discord via webhook — no dependencies.

    Returns the Discord ``message.id`` on success (when ``?wait=true``
    surfaces the created message), or a sentinel ``"1"`` when the POST
    succeeded but the response was empty (vanilla webhooks return 204
    No Content), or ``None`` on any failure. The string return preserves
    truthiness for the dispatcher's send-failure detection.

    Args:
        message: Body text. Truncated to Discord's 1900-char soft cap to
            leave room for follow-up suffixes.
        webhook_url: Explicit webhook to use; otherwise resolved from
            ``discord_lab_logs_webhook_url`` (or env fallback).
        pool: Optional asyncpg pool for lazy app_settings fetches.
        message_reference_id: Discord ``message_reference.message_id``.
            Used by the firefighter ops follow-up (#347 step 5) to
            quote-reply the diagnosis under the original alert. Webhook
            replies require ``?wait=true`` on the URL so Discord returns
            the created message body — we add it transparently.

    Resolution order for the webhook URL:
        1. Explicit ``webhook_url=`` arg (used by ``notify`` for the
           ops-channel route).
        2. ``app_settings.discord_lab_logs_webhook_url`` (the public
           lab-logs channel — daily digest fallback).
        3. ``DISCORD_LAB_LOGS_WEBHOOK_URL`` env var (legacy bootstrap).
    """
    if not webhook_url:
        pool = await _resolve_pool(pool)
        if pool is not None:
            webhook_url = await _read_app_setting(
                pool,
                "discord_lab_logs_webhook_url",
                default=os.getenv("DISCORD_LAB_LOGS_WEBHOOK_URL", ""),
            )
        else:
            webhook_url = os.getenv("DISCORD_LAB_LOGS_WEBHOOK_URL", "")
    if not webhook_url:
        logger.debug("[BRAIN] No Discord webhook URL — skipping")
        return None
    try:
        body: dict[str, object] = {"content": message}
        post_url = webhook_url
        if message_reference_id is not None:
            # Discord webhooks support ``message_reference`` to thread a
            # message under another. ``?wait=true`` makes the API echo
            # the created message back so we can capture its id for
            # downstream chains. Some Discord webhook variants reject
            # message_reference for cross-channel replies — best-effort
            # only; failure to thread is logged and the message still
            # ships normally.
            body["message_reference"] = {"message_id": str(message_reference_id)}
            sep = "&" if "?" in post_url else "?"
            post_url = f"{post_url}{sep}wait=true"
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            post_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "PoinDexterBrain/1.0",
            },
        )
        def _do_post():
            return urllib.request.urlopen(req, timeout=10)
        resp = await asyncio.to_thread(_do_post)
        if not (200 <= resp.status < 300):
            return None
        # Try to extract message.id when ``?wait=true`` was set. Vanilla
        # webhook posts return 204 No Content — the sentinel "1" keeps
        # truthiness so the dispatcher counts the send as accepted.
        try:
            response_text = await asyncio.to_thread(resp.read)
            if isinstance(response_text, bytes) and response_text:
                response_data = json.loads(response_text.decode("utf-8"))
                if isinstance(response_data, dict):
                    message_id = response_data.get("id")
                    if message_id:
                        return str(message_id)
        except Exception as parse_err:  # noqa: BLE001 — best-effort parse
            logger.debug(
                "[BRAIN] Discord response parse failed: %s — "
                "send succeeded but message_id unavailable",
                parse_err,
            )
        return "1"
    except Exception as e:
        logger.error("[BRAIN] Discord send failed: %s", e)
        return None


async def notify(message: str, *, pool=None) -> dict[str, object]:
    """Send to both Telegram (urgent) and Discord #ops (ops log).

    Telegram = alarm bell (phone push notification).
    Discord #ops = system's voice (scrollable ops history).
    Discord #lab-logs = public-facing (daily digest only).

    Returns a dict carrying the per-channel message ids so callers can
    thread follow-ups (Glad-Labs/poindexter#347 step 5)::

        {
            "telegram_message_id": int | None,
            "discord_message_id": str | None,
            "ok": bool,                     # True iff at least one channel accepted
        }

    Backward compat: the dict is truthy when ``ok=True`` (mappings are
    truthy when non-empty) and falsy-ish via the ``ok`` key, so the
    alert_dispatcher's adapter only needs ``not result.get("ok")`` to
    detect total failure. Raising ``NotifyFailed`` on that path keeps
    the dispatched_at write honest (#342).

    Lazy-fetches ``discord_ops_webhook_url`` from app_settings each
    call (#344). Callers that already hold a pool should pass it as
    ``pool=`` to share the connection across the two sends.
    """
    pool = await _resolve_pool(pool)
    tg_id = await send_telegram(message, pool=pool)
    # Operational messages go to #ops; fall back to lab-logs only if
    # ops isn't configured. The ops webhook is read from app_settings
    # on every call (no cached global — see #344 docstring above).
    ops_url = ""
    if pool is not None:
        ops_url = await _read_app_setting(
            pool,
            "discord_ops_webhook_url",
            default=os.getenv("DISCORD_OPS_WEBHOOK_URL", ""),
        )
    else:
        ops_url = os.getenv("DISCORD_OPS_WEBHOOK_URL", "")
    if ops_url:
        dc_id = await send_discord(message, webhook_url=ops_url, pool=pool)
    else:
        dc_id = await send_discord(message, pool=pool)  # fallback to lab-logs
    return {
        "telegram_message_id": tg_id if isinstance(tg_id, int) else None,
        "discord_message_id": dc_id if isinstance(dc_id, str) else None,
        "ok": bool(tg_id) or bool(dc_id),
    }


async def send_followup(
    text: str,
    *,
    parent_telegram_message_id: int | None = None,
    parent_discord_message_id: str | int | None = None,
    pool=None,
) -> dict[str, object]:
    """Send a follow-up message threaded under the original notify.

    Glad-Labs/poindexter#347 step 5 — the firefighter ops LLM produces
    a diagnosis paragraph that should appear as a quote-reply to the
    raw alert in the same Telegram (and Discord) thread, so the
    operator sees both messages together on their phone.

    Both parent ids are optional. When ``parent_telegram_message_id``
    is set the Telegram send threads via ``reply_to_message_id``;
    similarly for Discord via ``message_reference``. When neither is
    provided the message goes through as a normal notify (degraded —
    the operator still gets the diagnosis, just not threaded).

    Mirrors :func:`notify`'s return shape so callers can chain (e.g.
    quote-reply a follow-up to the follow-up). The ``[triage]`` prefix
    on the text is the caller's job — this helper makes no assumption
    about content shape.

    No retry / backoff in this helper. The ``alert_dispatcher`` owns
    the retry policy (per ``ops_triage_retry_*`` settings) and decides
    when to give up; this is the leaf send.
    """
    pool = await _resolve_pool(pool)
    tg_id: int | None = None
    if parent_telegram_message_id is not None:
        tg_id = await send_telegram(
            text,
            pool=pool,
            reply_to_message_id=parent_telegram_message_id,
        )
    elif parent_discord_message_id is None:
        # Caller has neither id — fall back to a normal notify so the
        # operator still gets the message. This branch is the "degraded
        # but useful" path; the dispatcher logs when it lands here.
        tg_id = await send_telegram(text, pool=pool)

    dc_id: str | None = None
    if parent_discord_message_id is not None:
        ops_url = ""
        if pool is not None:
            ops_url = await _read_app_setting(
                pool,
                "discord_ops_webhook_url",
                default=os.getenv("DISCORD_OPS_WEBHOOK_URL", ""),
            )
        if ops_url:
            dc_id = await send_discord(
                text,
                webhook_url=ops_url,
                pool=pool,
                message_reference_id=parent_discord_message_id,
            )
        else:
            dc_id = await send_discord(
                text,
                pool=pool,
                message_reference_id=parent_discord_message_id,
            )
    return {
        "telegram_message_id": tg_id if isinstance(tg_id, int) else None,
        "discord_message_id": dc_id if isinstance(dc_id, str) else None,
        "ok": bool(tg_id) or bool(dc_id),
    }


async def restart_service(name: str, *, pool=None):
    """Attempt to restart a local service on the operator's PC."""
    if IS_DOCKER:
        # Docker socket is mounted — restart sibling containers directly.
        _container_map = {
            "worker": "poindexter-worker",
            "api": "poindexter-worker",
            "site": "poindexter-worker",
            "sdxl": "poindexter-sdxl-server",
            "sdxl-server": "poindexter-sdxl-server",
        }
        container = _container_map.get(name)
        if container:
            try:
                # Pre-check container existence. ``docker compose up
                # --force-recreate`` runs stop → rm → run sequentially and
                # leaves the container name unbound for ~1-2 seconds; a
                # health probe firing in that window would otherwise
                # ``docker restart`` against a missing container, the
                # operator gets a noisy "No such container" notification,
                # and the next cycle (≤5 min later) sees the recreated
                # container as healthy. Treat absence as transient and
                # skip the alert — if the container is genuinely gone the
                # next cycle will see the same state and the upstream
                # health probe (not this restart helper) is the right
                # surface to escalate it.
                inspect = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Status}}", container],
                    capture_output=True, text=True, timeout=10,
                )
                if inspect.returncode != 0:
                    logger.info(
                        "[BRAIN] container %s not found (likely mid-recreate) — "
                        "skipping auto-restart this cycle", container,
                    )
                    return

                result = subprocess.run(
                    ["docker", "restart", container],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    logger.info("[BRAIN] Docker-restarted container %s", container)
                    await notify(f"Auto-restarted {container}", pool=pool)
                else:
                    logger.warning("[BRAIN] Docker restart failed for %s: %s", container, result.stderr[:100])
                    await notify(f"Failed to restart {container}: {result.stderr[:100]}", pool=pool)
            except FileNotFoundError:
                logger.warning("[BRAIN] Docker CLI not available in container — install docker-cli or mount the binary")
                await notify(f"Service {name} is down. Docker CLI not found in brain container.", pool=pool)
            except Exception as e:
                logger.warning("[BRAIN] Docker restart error for %s: %s", name, e)
                await notify(f"Service {name} is down. Restart failed: {e}", pool=pool)
        else:
            await notify(f"Service {name} is down — no container mapping for auto-restart.", pool=pool)
        return
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        if name == "worker":
            # The host-side worker restart script lives at
            # ``<repo-root>/scripts/start-worker.ps1``. The brain runs
            # inside its own container and doesn't know the host repo
            # path — operators set ``app_settings.worker_restart_script``
            # (Windows absolute path) once at install time.
            restart_script = await _read_app_setting(
                pool, "worker_restart_script", default="",
            )
            if not restart_script:
                logger.warning(
                    "[BRAIN] worker restart requested but "
                    "app_settings.worker_restart_script is unset — "
                    "operator must seed it with the absolute path to "
                    "scripts/start-worker.ps1 on the host",
                )
                await notify(
                    "Service worker is down. Set "
                    "app_settings.worker_restart_script to enable "
                    "auto-restart.", pool=pool,
                )
                return
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", restart_script],
                **kwargs,
            )
            logger.info("[BRAIN] Restarted worker via %s", restart_script)
        elif name == "openclaw":
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", "openclaw gateway restart"],
                **kwargs,
            )
            logger.info("[BRAIN] Restarted OpenClaw")
    except Exception as e:
        logger.error("[BRAIN] Failed to restart %s: %s", name, e, exc_info=True)


_last_openclaw_doctor = 0.0  # Track last doctor run to avoid running every cycle
_openclaw_cli_missing = False  # Latch once we know the CLI isn't installed


def _run_openclaw_doctor():
    """Run 'openclaw doctor --fix' to heal degraded channels (Telegram 409, WhatsApp disconnect).

    The daemon may run in a container where the openclaw CLI isn't installed
    (it's a host-side tool). If we've already discovered the CLI is missing
    on this process, skip silently instead of retrying and logging every
    cycle.
    """
    global _last_openclaw_doctor, _openclaw_cli_missing
    if _openclaw_cli_missing:
        return
    try:
        kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        result = subprocess.run(
            ["openclaw", "doctor", "--fix"],
            capture_output=True, text=True, timeout=30, **kwargs,
        )
        _last_openclaw_doctor = time.time()
        if "error" in result.stdout.lower() or result.returncode != 0:
            logger.warning("[BRAIN] openclaw doctor reported issues: %s", result.stdout[-200:])
        else:
            logger.info("[BRAIN] openclaw doctor --fix ran OK")
    except FileNotFoundError:
        # CLI isn't installed here — log once, then quiet down.
        _openclaw_cli_missing = True
        logger.info("[BRAIN] openclaw CLI not on PATH — skipping periodic doctor runs")
    except Exception as e:
        logger.warning("[BRAIN] openclaw doctor failed: %s", e)


async def monitor_services(pool) -> list:
    """Check all services, log to knowledge graph, alert on failures."""
    global _last_openclaw_doctor
    issues = []
    for name, config in SERVICES.items():
        if config["type"] == "json_status":
            ok, code, detail = check_json_status(config["url"])
        else:
            ok, code, detail = check_http(config["url"])

        # Store in knowledge graph
        await pool.execute("""
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
            VALUES ($1, $2, $3, $4, 'brain_monitor', $5)
            ON CONFLICT (entity, attribute) DO UPDATE SET
                value = EXCLUDED.value, updated_at = NOW()
        """, f"service.{name}", "status", "up" if ok else "down", 1.0, ["monitoring"])

        if not ok:
            issues.append({"service": name, "code": code, "detail": detail, "critical": config["critical"]})
            logger.warning("[BRAIN] Service %s is DOWN: %s", name, detail)

            # Auto-restart local services
            if name in ("worker", "openclaw"):
                await restart_service(name, pool=pool)
                logger.info("[BRAIN] Auto-restarted %s", name)

            # Auto-triage: check alert_actions table before escalating
            if config["critical"]:
                try:
                    pattern = f"{name}_down" if name != "api" else "api_down"
                    action = await pool.fetchrow(
                        "SELECT id, action_type, cooldown_minutes, last_triggered_at, consecutive_failures, escalate_after_failures "
                        "FROM alert_actions WHERE pattern = $1 AND enabled = true", pattern
                    )
                    if action:
                        # Check cooldown
                        in_cooldown = False
                        if action["last_triggered_at"]:
                            elapsed = (datetime.now(timezone.utc) - action["last_triggered_at"]).total_seconds() / 60
                            in_cooldown = elapsed < action["cooldown_minutes"]

                        if not in_cooldown:
                            await pool.execute(
                                "UPDATE alert_actions SET last_triggered_at = NOW(), total_triggers = total_triggers + 1, "
                                "consecutive_failures = consecutive_failures + 1 WHERE id = $1", action["id"]
                            )
                            await pool.execute(
                                "INSERT INTO alert_log (alert_action_id, pattern, trigger_detail, action_taken, result) "
                                "VALUES ($1, $2, $3, $4, 'logged')",
                                action["id"], pattern, f"{name}: {detail}"[:500], action["action_type"]
                            )
                            failures = (action["consecutive_failures"] or 0) + 1
                            if failures >= action["escalate_after_failures"] and action["escalate_after_failures"] > 0:
                                await notify(f"🚨 {name} DOWN ({failures}x): {detail}", pool=pool)
                            else:
                                logger.info("[BRAIN] Alert '%s' logged (failure %d/%d before escalation)",
                                            pattern, failures, action["escalate_after_failures"])
                        else:
                            logger.debug("[BRAIN] Alert '%s' in cooldown", pattern)
                    else:
                        await notify(f"ALERT: {name} is DOWN — {detail}", pool=pool)
                except Exception as alert_err:
                    logger.warning("[BRAIN] Alert triage failed: %s — falling back to Telegram", alert_err, exc_info=True)
                    await notify(f"ALERT: {name} is DOWN — {detail}", pool=pool)
        else:
            logger.debug("[BRAIN] Service %s: OK", name)

    # Run openclaw doctor every 15 minutes to heal degraded channels
    # (Telegram 409 conflicts, WhatsApp disconnects) that appear "up" to HTTP checks
    if time.time() - _last_openclaw_doctor > 900:
        _run_openclaw_doctor()

    return issues


async def monitor_external_services(pool) -> list:
    """Check external service status pages, log to knowledge graph, alert on changes."""
    global _prev_external_status
    issues = []

    for name, config in EXTERNAL_SERVICES.items():
        if config["type"] == "statuspage":
            ok, indicator, description = check_statuspage(config["url"])
        elif config["type"] == "instatus":
            ok, indicator, description = check_instatus(config["url"])
        else:
            continue

        # Store in knowledge graph
        await pool.execute("""
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
            VALUES ($1, $2, $3, $4, 'brain_monitor', $5)
            ON CONFLICT (entity, attribute) DO UPDATE SET
                value = EXCLUDED.value, updated_at = NOW()
        """, f"external.{name}", "status", f"{indicator}: {description}",
            1.0, ["monitoring", "external"])

        prev = _prev_external_status.get(name)
        _prev_external_status[name] = indicator

        if not ok:
            issues.append({"service": name, "indicator": indicator, "description": description})
            # Only Telegram alert on MAJOR outages (not minor/degraded — reduces spam)
            is_major = indicator in ("major", "critical", "major_outage")
            if prev != indicator:
                logger.warning("[BRAIN] External %s: %s — %s", name, indicator, description)
                if is_major:
                    await notify(f"🚨 {name.upper()} MAJOR OUTAGE: {description}", pool=pool)
        else:
            # Alert on recovery from major outage only
            if prev and prev in ("major", "critical", "major_outage") and prev != indicator:
                logger.info("[BRAIN] External %s recovered: %s", name, description)
                await notify(f"✅ {name.upper()} recovered: {description}", pool=pool)
            logger.debug("[BRAIN] External %s: OK", name)

    return issues


async def enqueue_brain_item(pool, item_type: str, content: str, context: dict = None, priority: int = 5):
    """Put an item into the brain queue. Callable by any service with a pool handle."""
    await pool.execute("""
        INSERT INTO brain_queue (item_type, content, context, priority, status)
        VALUES ($1, $2, $3::jsonb, $4, 'pending')
    """, item_type, content, json.dumps(context or {}), priority)
    logger.info("[BRAIN] Enqueued %s item (priority %d): %s", item_type, priority, content[:80])


# Brand pillars for topic relevance checks
BRAND_PILLARS = {"ai", "ml", "machine learning", "artificial intelligence", "hardware",
                 "gpu", "cpu", "gaming", "pc", "build", "benchmark", "linux", "llm",
                 "deep learning", "neural", "tech", "automation", "pipeline", "content"}


def _is_brand_relevant(text: str) -> bool:
    """Quick keyword check — does the topic touch at least one brand pillar?"""
    lower = text.lower()
    return any(kw in lower for kw in BRAND_PILLARS)


async def _handle_topic_suggestion(pool, item):
    """Validate a suggested topic and queue as content task if on-brand."""
    topic = item["content"]
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})

    if not _is_brand_relevant(topic):
        logger.info("[BRAIN] Topic rejected (off-brand): %s", topic[:80])
        return {"action": "rejected", "reason": "off-brand"}

    # Check for duplicate topics already in the pipeline
    existing = await pool.fetchval(
        "SELECT COUNT(*) FROM pipeline_tasks_view WHERE topic ILIKE $1 AND status NOT IN ('failed', 'rejected')",
        f"%{topic[:60]}%",
    )
    if existing:
        logger.info("[BRAIN] Topic rejected (duplicate): %s", topic[:80])
        return {"action": "rejected", "reason": "duplicate_topic"}

    # Queue as a content task
    metadata = json.dumps({"source": ctx.get("source", "brain_queue"), "suggested_by": ctx.get("suggested_by", "unknown")})
    await pool.execute("""
        INSERT INTO pipeline_tasks (task_id, task_type, topic, status)
        VALUES (gen_random_uuid()::text, 'blog_post', $1::text, 'pending')
    """, topic, metadata)
    logger.info("[BRAIN] Topic accepted and queued: %s", topic[:80])
    return {"action": "queued_as_content_task"}


async def _handle_alert(pool, item):
    """Forward alert content to Telegram."""
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})
    severity = ctx.get("severity", "info")
    source = ctx.get("source", "unknown")
    await notify(f"[{severity.upper()}] {source}: {item['content']}", pool=pool)
    return {"action": "forwarded_to_telegram", "severity": severity}


async def _handle_config_change(pool, item):
    """Log a config change into brain_knowledge for audit trail."""
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})
    key = ctx.get("key", "unknown_key")
    await pool.execute("""
        INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
        VALUES ($1, 'config_change', $2, 1.0, 'brain_queue', $3)
        ON CONFLICT (entity, attribute) DO UPDATE SET
            value = EXCLUDED.value, updated_at = NOW()
    """, f"config.{key}", item["content"][:500], ["audit", "config"])
    logger.info("[BRAIN] Config change logged: %s", key)
    return {"action": "logged_to_knowledge", "key": key}


async def _handle_observation(pool, item):
    """Store an observation as a brain_knowledge fact."""
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})
    entity = ctx.get("entity", "general")
    await pool.execute("""
        INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
        VALUES ($1, 'observation', $2, $3, 'brain_queue', $4)
        ON CONFLICT (entity, attribute) DO UPDATE SET
            value = EXCLUDED.value, updated_at = NOW()
    """, entity, item["content"][:1000], ctx.get("confidence", 0.7), ctx.get("tags", ["observation"]))
    logger.info("[BRAIN] Observation stored for entity '%s'", entity)
    return {"action": "stored_as_knowledge", "entity": entity}


_QUEUE_HANDLERS = {
    "topic_suggestion": _handle_topic_suggestion,
    "alert": _handle_alert,
    "config_change": _handle_config_change,
    "observation": _handle_observation,
}


async def process_queue(pool, max_items: int = 5):
    """Process pending items in the brain queue."""
    try:
        items = await pool.fetch("""
            SELECT id, item_type, content, context
            FROM brain_queue WHERE status = 'pending'
            ORDER BY priority ASC, created_at ASC LIMIT $1
        """, max_items)

        for item in items:
            try:
                handler = _QUEUE_HANDLERS.get(item["item_type"])
                if handler:
                    result = await handler(pool, item)
                else:
                    logger.info("[BRAIN] Unknown item_type '%s' — marking processed", item["item_type"])
                    result = {"processed_by": "brain_daemon", "note": "unknown_item_type"}

                await pool.execute(
                    "UPDATE brain_queue SET status = 'processed', processed_at = NOW(), result = $1 WHERE id = $2",
                    json.dumps(result), item["id"],
                )
            except Exception as e:
                await pool.execute(
                    "UPDATE brain_queue SET status = 'failed', result = $1, processed_at = NOW() WHERE id = $2",
                    str(e)[:500], item["id"],
                )

        if items:
            logger.info("[BRAIN] Processed %d queue items", len(items))
    except Exception as e:
        logger.error("[BRAIN] Queue processing failed: %s", e, exc_info=True)


async def _stamp_auto_cancelled(pool, task_ids: list) -> None:
    """Stamp ``pipeline_tasks.auto_cancelled_at`` for the given rows.

    GH-90 AC #4 + poindexter#366 phase 2: the sweeper used to write
    one row per cancellation into ``pipeline_events`` so the worker's
    metrics_exporter could COUNT them on Prometheus scrape (a raw
    in-memory counter would reset on brain restart). The signal now
    lives on ``pipeline_tasks.auto_cancelled_at`` directly — the
    worker reads ``COUNT(*) WHERE auto_cancelled_at IS NOT NULL`` for
    the same gauge, no separate event row.

    Idempotent: a re-stamp on the same task_ids leaves the original
    cancel timestamp in place (COALESCE) so a sweeper retry can't
    inflate the count.
    """
    if not task_ids:
        return
    await pool.execute(
        """
        UPDATE pipeline_tasks
           SET auto_cancelled_at = COALESCE(auto_cancelled_at, NOW())
         WHERE task_id = ANY($1::text[])
        """,
        [str(t) for t in task_ids],
    )


async def auto_remediate(pool):
    """Detect and fix pipeline problems automatically. Runs every cycle."""
    try:
        actions_taken = []

        # 1. Auto-cancel tasks stuck in_progress beyond stale_task_timeout_minutes
        #    (default 180m + brain_auto_cancel_grace_minutes extra safety).
        #
        #    GH-90: the sweeper MUST guard on updated_at < NOW() - interval, not
        #    just started_at, or we race the worker. The worker heartbeats
        #    updated_at every worker_heartbeat_interval_seconds during long
        #    stages, so a fresh updated_at is proof the worker is actively
        #    processing the row and the sweeper must back off.
        stale_minutes = await _setting_int(pool, "stale_task_timeout_minutes", 180)
        grace_minutes = await _setting_int(pool, "brain_auto_cancel_grace_minutes", 10)
        cutoff_minutes = stale_minutes + grace_minutes
        stuck = await pool.fetch(f"""
            UPDATE pipeline_tasks SET status = 'failed',
                error_message = 'Auto-cancelled: stuck in_progress > {stale_minutes}m',
                updated_at = NOW()
            WHERE status = 'in_progress'
              AND updated_at < NOW() - INTERVAL '{cutoff_minutes} minutes'
              AND COALESCE(started_at, updated_at) < NOW() - INTERVAL '{cutoff_minutes} minutes'
            RETURNING task_id, topic
        """)
        if stuck:
            topics = [r["topic"][:40] for r in stuck]
            task_ids = [r["task_id"] for r in stuck]
            actions_taken.append(f"cancelled {len(stuck)} stuck task(s): {', '.join(topics)}")
            # GH-90 AC #4: warn-level log with task_id + reason, one row per task,
            # so operators can grep/alert on individual IDs instead of a single
            # summary line. Also bump the Prometheus metric so the dashboard
            # surfaces the rate of sweeper cancellations over time.
            for _tid, _topic in zip(task_ids, topics):
                logger.warning(
                    "[BRAIN][auto-cancel] task_id=%s topic=%r reason='stuck in_progress > %dm'",
                    _tid, _topic, stale_minutes,
                )
            try:
                await _stamp_auto_cancelled(pool, task_ids)
            except Exception as _metric_err:
                logger.debug("[BRAIN] auto_cancelled stamp failed: %s", _metric_err)

        # 2. Auto-expire awaiting_approval tasks older than 7 days
        # #198: auto-reject stale approval window tunable via app_settings.
        _approval_days = await _setting_int(
            pool, "brain_stale_approval_auto_reject_days", 7
        )
        expired = await pool.fetch(f"""
            UPDATE pipeline_tasks SET status = 'rejected',
                error_message = 'Auto-rejected: awaiting_approval > {_approval_days} days',
                updated_at = NOW()
            WHERE status = 'awaiting_approval'
              AND updated_at < NOW() - INTERVAL '{_approval_days} days'
            RETURNING task_id, topic
        """)
        if expired:
            topics = [r["topic"][:40] for r in expired]
            actions_taken.append(f"auto-rejected {len(expired)} stale approval(s): {', '.join(topics)}")

        # 3. Detect and alert on pipeline stall (no new tasks in 48h + no pending tasks)
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'pending') as pending,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'in_progress') as active,
                (SELECT MAX(created_at) FROM pipeline_tasks_view) as last_task
        """)
        if row:
            pending = row["pending"] or 0
            active = row["active"] or 0
            last_task = row["last_task"]
            if pending == 0 and active == 0 and last_task:
                from datetime import datetime, timezone
                if last_task.tzinfo is None:
                    last_task = last_task.replace(tzinfo=timezone.utc)
                hours_idle = (datetime.now(timezone.utc) - last_task).total_seconds() / 3600
                if hours_idle > 48:
                    actions_taken.append(f"pipeline idle {hours_idle:.0f}h — no pending/active tasks")
                    # Store in knowledge graph for trend tracking
                    await pool.execute("""
                        INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
                        VALUES ('pipeline', 'idle_alert', $1, 0.8, 'auto_remediate', $2)
                        ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """, f"{hours_idle:.0f}h idle", ["pipeline", "alert"])

        # 4. Detect failed task ratio spike and alert (#198 tunable window)
        _fail_win_h = await _setting_int(pool, "brain_failure_rate_window_hours", 24)
        row = await pool.fetchrow(f"""
            SELECT
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE status = 'failed' AND updated_at > NOW() - INTERVAL '{_fail_win_h} hours') as recent_fails,
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE updated_at > NOW() - INTERVAL '{_fail_win_h} hours') as recent_total
        """)
        if row and row["recent_total"] and row["recent_total"] > 0:
            fail_rate = row["recent_fails"] / row["recent_total"]
            if fail_rate > 0.5 and row["recent_fails"] >= 3:
                actions_taken.append(
                    f"high failure rate: {row['recent_fails']}/{row['recent_total']} "
                    f"({fail_rate:.0%}) in {_fail_win_h}h"
                )

        if actions_taken:
            logger.info("[BRAIN] Auto-remediation: %s", "; ".join(actions_taken))
            # Alert on significant actions
            for action in actions_taken:
                if "cancelled" in action or "high failure" in action or "idle" in action:
                    await notify(f"🔧 Auto-remediation: {action}", pool=pool)

    except Exception as e:
        logger.debug("[BRAIN] Auto-remediation failed: %s", e)


async def generate_daily_digest(pool):
    """Send a daily summary to Telegram at ~9 AM (runs every cycle, fires once/day)."""
    try:
        # Check if we already sent today
        row = await pool.fetchrow("""
            SELECT value FROM brain_knowledge
            WHERE entity = 'digest' AND attribute = 'last_sent'
        """)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if row:
            last_sent = row["value"]
            # Parse date and skip if same day
            try:
                last_date = last_sent[:10]
                today = now.strftime("%Y-%m-%d")
                if last_date == today:
                    return  # Already sent today
            except Exception as exc:
                # poindexter#455 — used to be silent. If last_sent is
                # malformed (e.g. operator manually edited app_settings),
                # we fall through to "may send today" — log the malformed
                # value so the operator can clean it up.
                logger.warning(
                    "[BRAIN] daily summary last-sent timestamp malformed "
                    "(%r): %s: %s — will resend",
                    last_sent, type(exc).__name__, exc,
                )

        # Only send between 13:00-14:00 UTC (~9 AM ET)
        if not (13 <= now.hour < 14):
            return

        # Build digest — window is tunable for weekly / daily / hourly
        # digest cadence per operator preference (#198).
        _digest_h = await _setting_int(pool, "brain_digest_window_hours", 24)
        stats = await pool.fetchrow(f"""
            SELECT
                (SELECT COUNT(*) FROM posts WHERE status = 'published') as total_posts,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'awaiting_approval') as approval_queue,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'pending') as pending,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'failed'
                    AND updated_at > NOW() - INTERVAL '{_digest_h} hours') as failed_24h,
                (SELECT COUNT(*) FROM posts WHERE status = 'published'
                    AND published_at > NOW() - INTERVAL '{_digest_h} hours') as published_24h,
                (SELECT COUNT(*) FROM page_views WHERE created_at >= date_trunc('day', NOW())) as views_today,
                (SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs
                    WHERE created_at >= date_trunc('month', NOW())) as month_spend
        """)
        if not stats:
            return

        msg = (
            f"📊 Daily Digest ({now.strftime('%b %d')})\n"
            f"Posts: {stats['total_posts']} published, {stats['published_24h']} new today\n"
            f"Pipeline: {stats['pending']} pending, {stats['approval_queue']} awaiting approval, {stats['failed_24h']} failed\n"
            f"Traffic: {stats['views_today']} views today\n"
            f"Spend: ${float(stats['month_spend']):.2f} MTD"
        )
        await send_telegram(msg, pool=pool)
        await send_discord(msg, pool=pool)  # #lab-logs channel

        # Mark sent
        await pool.execute("""
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ('digest', 'last_sent', $1, 1.0, 'brain_daemon')
            ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, now.isoformat())

        logger.info("[BRAIN] Daily digest sent")

    except Exception as e:
        logger.debug("[BRAIN] Daily digest failed: %s", e)


async def self_maintain(pool):
    """Expire stale knowledge, clean old queue items."""
    try:
        # Expire old facts
        result = await pool.execute(
            "DELETE FROM brain_knowledge WHERE expires_at IS NOT NULL AND expires_at < NOW()"
        )
        expired = int(result.split()[-1]) if result else 0

        # brain_queue table was dropped in migration 0080 (2026-04-21).
        # Removed the DELETE that referenced it — was raising UndefinedTable
        # every cycle and getting logged as "[BRAIN] Maintenance failed".

        if expired:
            logger.info("[BRAIN] Maintenance: expired %d facts", expired)
    except Exception as e:
        logger.error("[BRAIN] Maintenance failed: %s", e, exc_info=True)


async def update_system_metrics(pool):
    """Pull current system metrics into knowledge graph."""
    try:
        # Post count
        row = await pool.fetchrow("SELECT COUNT(*) as c FROM posts WHERE status = 'published'")
        if row:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('system', 'posts_count', $1, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, str(row["c"]))

        # Task counts
        rows = await pool.fetch("SELECT status, COUNT(*) as c FROM pipeline_tasks_view GROUP BY status")
        for r in rows:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('pipeline', $1, $2, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, f"tasks_{r['status']}", str(r["c"]))

        # Page views today
        row = await pool.fetchrow("SELECT COUNT(*) as c FROM page_views WHERE created_at >= date_trunc('day', NOW())")
        if row:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('traffic', 'views_today', $1, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, str(row["c"]))

    except Exception as e:
        logger.debug("[BRAIN] Metrics update failed: %s", e)


async def log_electricity_cost(pool):
    """Log electricity cost for this 5-minute cycle based on real power data or estimates."""
    try:
        # Try to get real power data from nvidia-smi-exporter (local only)
        watts = None
        power_source = "default"
        exporter_url = "http://host.docker.internal:9835/metrics" if IS_DOCKER else "http://localhost:9835/metrics"
        try:
            resp = urllib.request.urlopen(exporter_url, timeout=3)
            body = resp.read().decode()
            psu_watts = None
            estimate_watts = None
            for line in body.split("\n"):
                if line.startswith("psu_total_power_watts"):
                    psu_watts = float(line.split()[-1])
                elif line.startswith("system_total_power_estimate_watts"):
                    estimate_watts = float(line.split()[-1])
            # HX1500i wall power is ground truth; fall back to software estimate
            if psu_watts:
                watts = psu_watts
                power_source = "hx1500i"
            elif estimate_watts:
                watts = estimate_watts
                power_source = "estimate"
        except Exception as exc:
            # poindexter#455 — used to be silent. Exporter unreachable
            # is normal during host reboots but a sustained outage
            # means the cost dashboard's per-cycle watts come from a
            # 150W static estimate. Debug-log keeps the cycle quiet
            # in steady state but flags a transition operator-side.
            logger.debug(
                "[BRAIN] PSU exporter unreachable (%s: %s) — using "
                "software estimate / static fallback this cycle",
                type(exc).__name__, exc,
            )

        if watts is None:
            # Fallback estimate: local PC idles around 150W
            watts = 150.0

        # Load electricity rate from DB, fallback to default
        rate_per_kwh = 0.29  # $/kWh default
        try:
            row = await pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'electricity_rate_kwh'"
            )
            if row:
                rate_per_kwh = float(row["value"])
        except Exception as exc:
            # poindexter#455 — used to be silent. If the operator pinned
            # a non-default electricity rate, a DB blip would silently
            # fall back to the compiled default and the cost dashboard
            # would silently underreport / overreport the per-cycle cost.
            logger.warning(
                "[BRAIN] electricity_rate_kwh read failed (%s: %s) — using "
                "compiled default $%.4f/kWh for this cycle",
                type(exc).__name__, exc, rate_per_kwh,
            )

        # Calculate cost for this 5-minute interval
        hours = CYCLE_SECONDS / 3600.0
        kwh = (watts / 1000.0) * hours
        cost_usd = kwh * rate_per_kwh

        # Determine if system is actively generating (check for in_progress tasks)
        active_row = await pool.fetchrow(
            "SELECT COUNT(*) as c FROM pipeline_tasks_view WHERE status = 'in_progress'"
        )
        is_generating = (active_row["c"] or 0) > 0
        cost_type = "electricity_active" if is_generating else "electricity_idle"
        phase = "generation" if is_generating else "idle"

        await pool.execute("""
            INSERT INTO cost_logs (
                task_id, phase, model, provider, cost_usd,
                input_tokens, output_tokens, total_tokens,
                duration_ms, success, cost_type, created_at, updated_at
            ) VALUES (
                NULL, $1, 'system', 'electricity', $2,
                0, 0, 0, $3, true, $4, NOW(), NOW()
            )
        """, phase, cost_usd, int(CYCLE_SECONDS * 1000), cost_type)

        logger.debug("[BRAIN] Electricity: %.0fW (%s), %.4f kWh, $%.6f (%s)",
                     watts, power_source, kwh, cost_usd, cost_type)

        # PSU sensor watchdog — alert if real PSU data isn't available
        try:
            prev = await pool.fetchrow(
                "SELECT value FROM brain_knowledge WHERE entity = 'psu_watchdog' AND attribute = 'last_source'"
            )
            prev_source = prev["value"] if prev else None

            if power_source == "hx1500i" and prev_source != "hx1500i":
                # PSU sensors recovered
                await notify("PSU sensors recovered — using real HX1500i wall power data", pool=pool)
                await send_discord("✅ PSU sensors recovered — using real HX1500i wall power data", pool=pool)
            elif power_source != "hx1500i" and prev_source == "hx1500i":
                # PSU sensors dropped
                await notify(f"⚠️ PSU sensors dropped — falling back to {power_source} ({watts:.0f}W). iCUE may have lost the HX1500i connection.", pool=pool)
                await send_discord(f"⚠️ PSU sensors dropped — falling back to {power_source} ({watts:.0f}W). iCUE may have lost the HX1500i connection.", pool=pool)

            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('psu_watchdog', 'last_source', $1, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, power_source)
        except Exception as exc:
            # poindexter#455 — used to be silent. The watchdog's
            # state-transition Telegram/Discord notifications above
            # already fired; this is just the DB write that powers
            # the NEXT-cycle comparison. Silent failure here means
            # the next cycle sees prev_source=None and may re-fire
            # the same "PSU dropped" alert. Debug-log so a stuck
            # state is traceable.
            logger.debug(
                "[BRAIN] psu_watchdog brain_knowledge write failed "
                "(%s: %s) — next cycle may re-notify",
                type(exc).__name__, exc,
            )

    except Exception as e:
        logger.debug("[BRAIN] Electricity cost logging failed: %s", e)


async def _maybe_sync_grafana_alerts(pool) -> None:
    """GH-28: Push DB alert_rules to Grafana every N cycles.

    N = grafana_alert_sync_interval_cycles (default 3 = 15 min). Cadence
    is tracked via ``_alert_sync_cycle_counter`` so a slow Grafana never
    stalls the main brain cycle — the worst-case outcome is one WARNING
    log per sync attempt.

    Swallows all exceptions: the sync loop must never crash the brain.
    """
    global _alert_sync_cycle_counter
    interval = await _setting_int(pool, "grafana_alert_sync_interval_cycles", 3)
    if interval <= 0:
        return
    _alert_sync_cycle_counter += 1
    if _alert_sync_cycle_counter < interval:
        return
    _alert_sync_cycle_counter = 0
    try:
        await sync_alert_rules(pool)
    except Exception as e:
        logger.warning("[BRAIN] Grafana alert sync failed: %s", e, exc_info=True)


async def _record_operator_paged(pool, payload: dict, detail: str) -> None:
    """Best-effort write of an ``operator_paged`` row to ``audit_log``.

    Called from the ``set_notify_audit_sink`` shim wired in ``main()``.
    Failures are swallowed — the operator has already been paged via
    Telegram/Discord by the time this runs, so audit-recording is purely
    observability. The silent-alerter watchdog reads these to confirm
    the alerting plane delivered.
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (event_type, source, severity, details)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                "operator_paged",
                payload.get("source") or "brain",
                payload.get("severity") or "warning",
                json.dumps({
                    "title": payload.get("title"),
                    "detail_excerpt": (detail or "")[:500],
                    "channels": payload.get("channels") or {},
                }),
            )
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[brain_daemon] operator_paged audit insert failed: %s", e,
        )


async def alert_dispatch_loop(pool, shutdown_event):
    """Background task: poll alert_events for undispatched rows.

    Runs independently of the 5-min ``run_cycle`` so a slow Telegram
    or Discord round-trip never delays the main monitoring loop.
    Cadence is ``ALERT_DISPATCH_INTERVAL_SECONDS`` (30s by default).

    Best-effort: any exception in a single poll is logged and the loop
    continues. ``poll_and_dispatch`` already swallows per-row errors and
    marks the rows with their dispatch_result; this wrapper only has
    to handle wholesale failures (DB pool death, etc.).
    """
    if not _HAS_ALERT_DISPATCHER:
        logger.info(
            "[BRAIN] Alert dispatcher unavailable — alert_events rows "
            "will accumulate undispatched. Check brain image build."
        )
        return

    logger.info(
        "[BRAIN] Alert dispatcher loop started (interval=%ds)",
        ALERT_DISPATCH_INTERVAL_SECONDS,
    )
    while not shutdown_event.is_set():
        try:
            await _poll_alert_events(pool)
        except Exception as e:
            logger.warning(
                "[BRAIN] alert_dispatcher poll failed: %s", e, exc_info=True,
            )

        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=ALERT_DISPATCH_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass  # Normal — interval elapsed, continue polling.

    logger.info("[BRAIN] Alert dispatcher loop stopping")


async def run_cycle(pool):
    """One full brain cycle: monitor → process → maintain → update."""
    logger.info("[BRAIN] === Cycle start ===")

    issues = await monitor_services(pool)
    ext_issues = await monitor_external_services(pool)
    # process_queue is dead since migration 0080 dropped brain_queue
    # (2026-04-21). Nothing enqueues to it anymore. The handler
    # functions (enqueue_brain_item, accept_topic, etc.) are kept in
    # this module pending a fuller cleanup pass — see follow-up issue.
    await auto_remediate(pool)
    await self_maintain(pool)
    await update_system_metrics(pool)
    await log_electricity_cost(pool)
    await generate_daily_digest(pool)
    await _maybe_sync_grafana_alerts(pool)

    # Health probes — exercise services with real inputs (each on its own schedule)
    probe_results = await run_health_probes(pool, notify_fn=notify)
    probe_failures = [name for name, r in probe_results.items() if not r.get("ok")]

    # Business probes — operator-level monitoring (Glad Labs private, #215)
    if _HAS_BUSINESS_PROBES:
        try:
            biz_results = await run_business_probes(pool, notify_fn=notify)
            probe_results.update(biz_results)
        except Exception as e:
            logger.warning("[BRAIN] Business probes failed: %s", e)

    # Operator URL/IP drift probe (#214). Internally gated to ~15 min so it
    # doesn't run every 5-min cycle. Returns None when the gate skips, a
    # summary dict on real runs.
    if _HAS_OPERATOR_URL_PROBE:
        try:
            url_summary = await maybe_run_operator_url_probe(pool, notify_fn=None)
            if url_summary is not None:
                probe_results["operator_url_probe"] = {
                    "ok": (
                        url_summary.get("url_failures", 0) == 0
                        and url_summary.get("tailscale_drift_count", 0) == 0
                    ),
                    "detail": (
                        f"{url_summary.get('url_failures', 0)} URL failures, "
                        f"{url_summary.get('tailscale_drift_count', 0)} drifted device(s)"
                    ),
                    "summary": url_summary,
                }
        except Exception as e:
            logger.warning("[BRAIN] operator_url_probe failed: %s", e)

    # Migration drift probe (#228). Runs every cycle (5-min); detects drift
    # via worker /api/health migrations block and optionally auto-restarts
    # the worker when migration_drift_auto_recover_enabled=true.
    if _HAS_MIGRATION_DRIFT_PROBE:
        try:
            md_summary = await run_migration_drift_probe(pool)
            probe_results["migration_drift"] = {
                "ok": bool(md_summary.get("ok", False)),
                "detail": md_summary.get("detail", ""),
                "summary": md_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] migration_drift probe failed: %s", e)

    # Compose-spec drift probe (#213). Runs every cycle (5-min); compares
    # docker-compose.local.yml against `docker inspect` for each service
    # to catch the "compose was edited but the container was never
    # recreated" failure mode. Optionally auto-recreates drifted services
    # when compose_drift_auto_recover_enabled=true.
    if _HAS_COMPOSE_DRIFT_PROBE:
        try:
            cd_summary = await run_compose_drift_probe(pool)
            probe_results["compose_drift"] = {
                "ok": bool(cd_summary.get("ok", False)),
                "detail": cd_summary.get("detail", ""),
                "summary": cd_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] compose_drift probe failed: %s", e)

    # Backup watcher (#388). Stats the in-stack backup bind-mount each
    # cycle, kicks the backup containers when dumps go stale, and writes
    # a status='resolved' alert_events row when freshness recovers.
    # Disabled gracefully via app_settings.backup_watcher_enabled=false.
    if _HAS_BACKUP_WATCHER:
        try:
            bw_summary = await run_backup_watcher_probe(pool)
            probe_results["backup_watcher"] = {
                "ok": bool(bw_summary.get("ok", False)),
                "detail": bw_summary.get("detail", ""),
                "summary": bw_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] backup_watcher probe failed: %s", e)

    # SMART monitor (#387). Polls smartctl per drive, parses for
    # warning/critical attributes, and writes alert_events rows on
    # regression. Degrades gracefully when smartctl isn't installed
    # (one-time notify, status='skipped' on subsequent cycles). Per-
    # (drive, attribute) dedup window prevents re-fires for the
    # lifetime of a bad sector.
    if _HAS_SMART_MONITOR:
        try:
            sm_summary = await run_smart_monitor_probe(pool)
            probe_results["smart_monitor"] = {
                "ok": bool(sm_summary.get("ok", False)),
                "detail": sm_summary.get("detail", ""),
                "summary": sm_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] smart_monitor probe failed: %s", e)

    # Docker port-forward stuck-state probe (#222). Detects the
    # Windows wslrelay → com.docker.backend forwarding chain getting
    # stuck (internal hostname OK, host.docker.internal returns empty
    # reply) and auto-recovers via `docker restart`. Cap-protected
    # against runaway restart loops. Disabled gracefully via
    # app_settings.docker_port_forward_probe_enabled=false.
    if _HAS_DOCKER_PORT_FORWARD_PROBE:
        try:
            pf_summary = await run_docker_port_forward_probe(pool)
            probe_results["docker_port_forward"] = {
                "ok": bool(pf_summary.get("ok", False)),
                "detail": pf_summary.get("detail", ""),
                "summary": pf_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] docker_port_forward probe failed: %s", e)

    # Gate auto-expire (#338). Sweeps post_approval_gates for pending
    # rows older than gate_pending_max_age_hours (default 168 = 7d),
    # transitions them to rejected with the sentinel reason
    # ``auto_rejected_after_<N>_hours``.
    if _HAS_GATE_AUTO_EXPIRE_PROBE:
        try:
            ge_summary = await run_gate_auto_expire_probe(pool)
            probe_results["gate_auto_expire"] = {
                "ok": bool(ge_summary.get("ok", False)),
                "detail": ge_summary.get("detail", ""),
                "summary": ge_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] gate_auto_expire probe failed: %s", e)

    # Gate pending summary (#338). Coalesces the per-flip Telegram noise
    # from the HITL gate spine into ONE "N posts pending review" page per
    # dedup window. Pairs with the per-flip Telegram demotion in
    # services/gates/post_approval_gates.notify_gate_pending.
    if _HAS_GATE_PENDING_SUMMARY_PROBE:
        try:
            gps_summary = await run_gate_pending_summary_probe(pool)
            probe_results["gate_pending_summary"] = {
                "ok": bool(gps_summary.get("ok", False)),
                "detail": gps_summary.get("detail", ""),
                "summary": gps_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] gate_pending_summary probe failed: %s", e)

    # GlitchTip triage probe — pulls open issues every cycle, auto-resolves
    # known noise per glitchtip_triage_auto_resolve_patterns, and pages on
    # novel high-count issues. No-ops if the API token isn't configured
    # (status=unconfigured in the summary). See brain/glitchtip_triage_probe.py
    # and migration 0133.
    if _HAS_GLITCHTIP_TRIAGE_PROBE:
        try:
            gt_summary = await run_glitchtip_triage_probe(pool)
            probe_results["glitchtip_triage"] = {
                "ok": bool(gt_summary.get("ok", False)),
                "detail": gt_summary.get("detail", ""),
                "summary": gt_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] glitchtip_triage probe failed: %s", e)

    # PR staleness probe — surfaces open PRs older than the configured
    # threshold with green CI but no merge decision. Coalesced Discord-ops
    # alert per cycle, per-PR dedup via alert_dedup_state. Catches the
    # "agent shipped a PR and the operator forgot" failure mode. Internal
    # cadence gate (default 60 min) keeps the actual GitHub round-trip
    # to once per hour even though the probe is dispatched every brain
    # cycle. See brain/pr_staleness_probe.py.
    if _HAS_PR_STALENESS_PROBE:
        try:
            pr_summary = await run_pr_staleness_probe(pool)
            probe_results["pr_staleness"] = {
                "ok": bool(pr_summary.get("ok", False)),
                "detail": pr_summary.get("detail", ""),
                "summary": pr_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] pr_staleness probe failed: %s", e)

    # Discord bot reachability probe (poindexter#435). Pings
    # https://discord.com/api/v10/users/@me with the bot token every
    # configured interval; pages the operator (via alert_events) only on
    # 401/403, treats 5xx + transient network errors as info-level.
    # Internal cadence gate (default 5 min) inside the probe — dispatched
    # every brain cycle but skips between intervals.
    if _HAS_DISCORD_BOT_PROBE:
        try:
            disc_summary = await run_discord_bot_probe(pool)
            probe_results["discord_bot"] = {
                "ok": bool(disc_summary.get("ok", False)),
                "detail": disc_summary.get("detail", ""),
                "summary": disc_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] discord_bot probe failed: %s", e)

    # MCP HTTP server (:8004) liveness probe (poindexter#434). Pings the
    # OAuth discovery endpoint; pages the operator on unreachable/5xx
    # and optionally invokes the configured launcher path for auto-
    # recovery. Restart count is capped per rolling window so a
    # genuinely broken server can't busy-loop the launcher.
    if _HAS_MCP_HTTP_PROBE:
        try:
            mcp_summary = await run_mcp_http_probe(pool)
            probe_results["mcp_http"] = {
                "ok": bool(mcp_summary.get("ok", False)),
                "detail": mcp_summary.get("detail", ""),
                "summary": mcp_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] mcp_http probe failed: %s", e)

    # Refresh Prometheus scrape secrets (uptime_kuma_api_key, etc.)
    # from app_settings → bind-mounted password_file paths so the next
    # prometheus scrape uses the current value. No-op when secrets are
    # already up to date. See brain/prometheus_secret_writer.py.
    if _HAS_PROMETHEUS_SECRET_WRITER:
        try:
            # Thin SiteConfig-shaped wrapper. Brain doesn't use the
            # worker's full DI seam, but write_prometheus_secrets only
            # needs ``get_secret(key, default)``. Delegates the actual
            # read+decrypt to ``brain.secret_reader`` so this module
            # doesn't carry yet another copy of the pgcrypto envelope
            # logic (consolidated in #342).
            class _BrainSecretReader:
                def __init__(self, _pool):
                    self._pool = _pool

                async def get_secret(self, key, default=""):
                    return await _read_app_setting(self._pool, key, default)

            secret_reader = _BrainSecretReader(pool)
            secret_results = await write_prometheus_secrets(secret_reader)
            probe_results["prometheus_secrets"] = {
                "ok": all(not v.startswith("error:") for v in secret_results.values()),
                "detail": ", ".join(f"{k}={v}" for k, v in secret_results.items()),
            }
        except Exception as e:
            logger.warning("[BRAIN] prometheus_secret_writer failed: %s", e)

    all_issues = issues + ext_issues

    # Log cycle result
    await pool.execute("""
        INSERT INTO brain_decisions (decision, reasoning, context, confidence)
        VALUES ($1, $2, $3::jsonb, $4)
    """, f"Cycle complete: {len(all_issues)} issues ({len(issues)} internal, {len(ext_issues)} external), {len(probe_results)} probes ({len(probe_failures)} failed)",
        f"Monitored {len(SERVICES)} internal + {len(EXTERNAL_SERVICES)} external services, ran {len(probe_results)} probes, processed queue, updated metrics",
        json.dumps({"issues": issues, "external_issues": ext_issues, "probe_failures": probe_failures, "timestamp": datetime.now(timezone.utc).isoformat()}),
        1.0,
    )

    logger.info("[BRAIN] === Cycle end: %d issues (%d internal, %d external), %d probes (%d failed) ===",
                len(all_issues), len(issues), len(ext_issues), len(probe_results), len(probe_failures))


async def main():
    one_shot = "--once" in sys.argv

    db_url = LOCAL_BRAIN_DB
    if not db_url:
        logger.error("[BRAIN] No DATABASE_URL — cannot start")
        sys.exit(1)

    logger.info("[BRAIN] Connecting to local brain DB...")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    logger.info("[BRAIN] Connected. Starting brain daemon (once=%s)", one_shot)

    # Register the pool in the cross-instance registry so send_telegram /
    # send_discord / notify can lazy-fetch their secrets even when called
    # from sibling code paths that hold a different module instance of
    # brain_daemon (see Glad-Labs/poindexter#344). Callers that already
    # hold a pool should still pass it explicitly via ``pool=`` to skip
    # the registry indirection.
    _set_brain_pool(pool)

    # Boot-controller phase 1: seed app_settings from the embedded core seed
    # if the table is empty or missing required keys. Idempotent — safe to
    # call every boot. See GitHub #63 (brain-as-boot-controller) and
    # brain/seed_app_settings.json for the core-seed inventory.
    try:
        async with pool.acquire() as conn:
            seed_result = await seed_app_settings(conn)
        logger.info(
            "[BRAIN] Seed phase complete: %d inserted, %d already present, %d total in seed",
            seed_result["inserted"],
            seed_result["skipped_existing"],
            seed_result["total_seed"],
        )
    except Exception as e:
        logger.error("[BRAIN] Seed phase FAILED: %s — continuing with existing app_settings", e, exc_info=True)

    # Load config from DB (site URLs, Telegram tokens, etc.)
    await _load_config_from_db(pool)

    # Boot-time import audit (poindexter#504). Every _HAS_* flag in this
    # module is set at import time by a try/except ImportError block. If
    # any are False at this point, a brain-side module that SHIPS with
    # the brain container failed to import — that's a packaging
    # regression, not "optional feature missing." Loud-fail per
    # feedback_no_silent_defaults so the operator notices instead of
    # silently running with degraded probe coverage for weeks.
    _audit_brain_module_imports()

    # Pyroscope continuous profiling (Glad-Labs/poindexter#406). Opt-in
    # via app_settings.enable_pyroscope; ships CPU samples to Pyroscope
    # under service="poindexter-brain" so the daemon shows up alongside
    # the worker / voice agents in the Grafana flame-graph panel.
    await _setup_brain_pyroscope(pool)

    # Build the shared OAuth client so probes can hit authenticated
    # worker endpoints with cached JWTs (#245). Falls back to the
    # legacy static Bearer if app_settings.brain_oauth_client_id /
    # _secret aren't set yet — exactly the dual-auth bridge the
    # middleware was built for.
    global _OAUTH_CLIENT
    if _HAS_OAUTH_CLIENT and _API_BASE_URL:
        try:
            _OAUTH_CLIENT = await oauth_client_from_pool(
                pool,
                base_url=_API_BASE_URL,
                scopes=BRAIN_DEFAULT_SCOPES,
            )
            mode = "oauth" if _OAUTH_CLIENT.using_oauth else "static-bearer (legacy)"
            logger.info("[BRAIN] OAuth client ready (mode=%s, base=%s)", mode, _API_BASE_URL)
        except Exception as e:
            logger.warning("[BRAIN] OAuth client init failed: %s — probes that need auth will skip", e, exc_info=True)
            _OAUTH_CLIENT = None
    else:
        logger.info(
            "[BRAIN] OAuth client unavailable (has_oauth=%s, api_base=%r) — "
            "authenticated probes disabled",
            _HAS_OAUTH_CLIENT, _API_BASE_URL,
        )

    # Legacy: the brain used to hot-patch ``TELEGRAM_BOT_TOKEN`` from the
    # OpenClaw workspace .env if app_settings was empty. That fallback is
    # gone — the canonical path is ``app_settings.telegram_bot_token``
    # (with ``read_app_setting`` decrypting on the fly per #342). Operators
    # who haven't migrated their token to app_settings should run
    # ``poindexter setup`` or ``poindexter settings set telegram_bot_token <value>``.

    # Wire the operator-notifier audit sink so every successful page
    # leaves an ``operator_paged`` audit_log trail. The silent-alerter
    # watchdog reads these to distinguish "alerter is broken" from "no
    # alerts in the last N hours because nothing's wrong". Bug 2026-05-12:
    # the watchdog only saw alert_events rows (Grafana → webhook path)
    # and treated direct-to-Telegram brain notifications as silent.
    try:
        from operator_notifier import set_notify_audit_sink  # type: ignore[import-not-found]
    except ImportError:
        try:
            from brain.operator_notifier import set_notify_audit_sink  # type: ignore[import-not-found]
        except ImportError:
            set_notify_audit_sink = None

    if set_notify_audit_sink is not None:
        def _audit_sink(*, source, severity, title, detail, results):
            """Sync wrapper that drops the audit row via a one-shot
            asyncpg task. notify_operator is sync (kept stdlib-only for
            bootstrap-time callers), so we schedule the write on the
            running loop without blocking the caller. Loop unavailable
            fallback (CLI usage outside an event loop): silently skip —
            the page itself already went out via Telegram/Discord."""
            payload = {
                "event_type": "operator_paged",
                "source": source,
                "severity": severity,
                "title": title,
                "channels": {
                    k: ("ok" if v == "ok" else v)
                    for k, v in (results or {}).items()
                },
            }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            try:
                loop.create_task(
                    _record_operator_paged(pool, payload, detail),
                    name="record_operator_paged",
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "[brain_daemon] could not schedule operator_paged "
                    "audit write: %s", e,
                )

        set_notify_audit_sink(_audit_sink)
        logger.info(
            "[BRAIN] notify_operator → audit_log sink wired (operator_paged events)"
        )

    shutdown = asyncio.Event()

    def _signal_handler():
        logger.info("[BRAIN] Shutdown signal received")
        shutdown.set()

    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except (NotImplementedError, AttributeError):
        # Windows doesn't support add_signal_handler — use KeyboardInterrupt instead
        pass

    # Heartbeat file — Layer 1 of the redundancy model.
    # An OS-level watchdog monitors this file's freshness and restarts the
    # brain if it goes stale (>15 min). Works on any OS, zero dependencies.
    _heartbeat_dir = os.path.join(os.path.expanduser("~"), ".poindexter")
    os.makedirs(_heartbeat_dir, exist_ok=True)
    _heartbeat_path = os.path.join(_heartbeat_dir, "heartbeat")
    # Also keep Docker path for container healthcheck compatibility
    _docker_heartbeat = "/tmp/brain_heartbeat" if IS_DOCKER else None

    def _touch_heartbeat(cycle_issues=0, probe_failures=0):
        """Write structured heartbeat — timestamp + cycle stats."""
        data = json.dumps({
            "ts": time.time(),
            "iso": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "cycle_ok": cycle_issues == 0 and probe_failures == 0,
            "issues": cycle_issues,
            "probe_failures": probe_failures,
        })
        try:
            with open(_heartbeat_path, "w") as hb:
                hb.write(data)
            if _docker_heartbeat:
                with open(_docker_heartbeat, "w") as hb:
                    hb.write(data)
        except OSError as e:
            logger.warning("[BRAIN] Failed to write heartbeat: %s", e)

    # Touch heartbeat on startup so watchdog knows we're alive immediately
    _touch_heartbeat()

    # Alert dispatcher loop — poll alert_events for undispatched rows on
    # its own 30s cadence so operator-facing pages aren't gated on the
    # 5-min monitoring cycle. Skipped in --once mode (one-shot is for
    # debug/CI; the dispatch loop's value is its persistent cadence).
    alert_dispatch_task = None
    if not one_shot:
        alert_dispatch_task = asyncio.create_task(
            alert_dispatch_loop(pool, shutdown),
            name="alert_dispatch_loop",
        )

    while not shutdown.is_set():
        try:
            await run_cycle(pool)
            # Update heartbeat after successful cycle
            _touch_heartbeat()
        except Exception as e:
            logger.error("[BRAIN] Cycle failed: %s", e, exc_info=True)

        if one_shot:
            break

        try:
            await asyncio.wait_for(shutdown.wait(), timeout=CYCLE_SECONDS)
        except asyncio.TimeoutError:
            pass  # Normal — timeout means no shutdown signal, continue loop

    logger.info("[BRAIN] Shutting down gracefully")
    if alert_dispatch_task is not None:
        try:
            await asyncio.wait_for(alert_dispatch_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            alert_dispatch_task.cancel()
        except Exception as e:  # noqa: BLE001
            logger.debug("[BRAIN] alert_dispatch_task close failed: %s", e)
    if _OAUTH_CLIENT is not None:
        try:
            await _OAUTH_CLIENT.aclose()
        except Exception as e:  # noqa: BLE001
            logger.debug("[BRAIN] OAuth client close failed: %s", e)
    await pool.close()
    logger.info("[BRAIN] Pool closed, exiting")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("brain").info("[BRAIN] Interrupted, exiting")
