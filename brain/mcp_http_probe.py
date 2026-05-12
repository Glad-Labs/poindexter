"""Poindexter MCP HTTP server (:8004) liveness probe.

Closes Glad-Labs/poindexter#434.

Why: the MCP HTTP server (used by the claude.ai-hosted connector
through the Tailscale Funnel) has a scheduled-task launcher that
fires at logon but no mid-session crash detection. On 2026-05-07
the process died silently and we only noticed because the connector
broke during a call.

This probe encodes detect + (optional) recover:

1. HTTP GET ``http://127.0.0.1:<port>/.well-known/oauth-protected-resource``
   with a short timeout.
2. 200/204 → healthy.
3. 5xx / non-2xx → page operator + (if configured) invoke the launcher
   script to bring the process back.
4. Network error → page operator. Unlike the Discord probe, the MCP
   server is on localhost — a network blip here means the process is
   genuinely down, not a flaky upstream.
5. Internal cadence gate (default 5 min). Dedup window (default 1h)
   collapses repeated alerts while the server stays down.

Auto-recovery is OPT-IN — operator sets
``mcp_http_probe_launcher_path`` to an absolute path of a launcher
(.cmd on Windows, .sh on POSIX). Empty string = detection only, no
recovery attempt. Restarts are capped at N per M-minute window so a
genuinely broken process can't trigger a restart loop.

Design parity with brain/discord_bot_probe.py + brain/pr_staleness_probe.py.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any, Callable, Optional

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # Flat import when brain/ is on sys.path (container runtime).
    from secret_reader import read_app_setting as _read_app_setting
except ImportError:  # pragma: no cover — package-qualified path
    from brain.secret_reader import read_app_setting as _read_app_setting

logger = logging.getLogger("brain.mcp_http_probe")


# ---------------------------------------------------------------------------
# App_settings keys.
# ---------------------------------------------------------------------------

ENABLED_KEY = "mcp_http_probe_enabled"
POLL_INTERVAL_MINUTES_KEY = "mcp_http_probe_interval_minutes"
HTTP_TIMEOUT_SECONDS_KEY = "mcp_http_probe_timeout_seconds"
DEDUP_HOURS_KEY = "mcp_http_probe_dedup_hours"
BASE_URL_KEY = "mcp_http_probe_base_url"
DISCOVERY_PATH_KEY = "mcp_http_probe_discovery_path"
LAUNCHER_PATH_KEY = "mcp_http_probe_launcher_path"
RESTART_CAP_KEY = "mcp_http_probe_restart_cap_per_window"
RESTART_WINDOW_MINUTES_KEY = "mcp_http_probe_restart_window_minutes"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 5
DEFAULT_HTTP_TIMEOUT_SECONDS = 3
DEFAULT_DEDUP_HOURS = 1
DEFAULT_BASE_URL = "http://127.0.0.1:8004"
DEFAULT_DISCOVERY_PATH = "/.well-known/oauth-protected-resource"
DEFAULT_RESTART_CAP = 3
DEFAULT_RESTART_WINDOW_MINUTES = 60


# ---------------------------------------------------------------------------
# Module state.
# ---------------------------------------------------------------------------

_last_real_check_at: float = 0.0
_last_alert_at: float = 0.0
_restart_attempts: list[float] = []  # monotonic timestamps within the rolling window


def _reset_state() -> None:
    """Test hook — drop module-level cadence + dedup + restart state."""
    global _last_real_check_at, _last_alert_at
    _last_real_check_at = 0.0
    _last_alert_at = 0.0
    _restart_attempts.clear()


# ---------------------------------------------------------------------------
# Tunable readers.
# ---------------------------------------------------------------------------


async def _read_bool(pool, key: str, default: bool) -> bool:
    raw = await _read_app_setting(pool, key, "")
    if not raw:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


async def _read_int(pool, key: str, default: int) -> int:
    raw = await _read_app_setting(pool, key, "")
    if not raw:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning(
            "[MCP_HTTP_PROBE] %s is not an integer (%r); falling back to %d",
            key, raw, default,
        )
        return default


# ---------------------------------------------------------------------------
# alert_events row writer.
# ---------------------------------------------------------------------------


async def _write_alert(
    pool,
    *,
    fingerprint_suffix: str,
    title: str,
    body: str,
) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                event_type, severity, channel_hint, fingerprint,
                title, body, details
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            "mcp_http_server_unreachable",
            "warning",
            "discord",
            f"mcp_http_probe:{fingerprint_suffix}",
            title,
            body,
            '{"probe": "mcp_http_probe"}',
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MCP_HTTP_PROBE] alert_events write failed: %s", exc)


# ---------------------------------------------------------------------------
# Auto-recovery launcher invocation.
# ---------------------------------------------------------------------------


def _try_launcher(launcher_path: str) -> tuple[bool, str]:
    """Spawn the operator-configured launcher. Returns (success, detail).

    On Windows we use ``CREATE_NO_WINDOW`` so the launcher doesn't pop
    a console window (Matt's "no popup windows" rule from his memory).
    """
    if not launcher_path:
        return False, "launcher not configured"
    if not os.path.exists(launcher_path):
        return False, f"launcher path missing: {launcher_path}"

    kwargs: dict[str, Any] = {"close_fds": True}
    # CREATE_NO_WINDOW is Windows-only (Matt's "no popup windows" rule).
    # getattr with a 0 default makes Pyright happy on POSIX where the
    # constant doesn't exist.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags

    try:
        # We DON'T wait — the launcher is fire-and-forget. The next
        # cadence cycle will re-probe and the resulting 200 (or another
        # failure) decides whether the restart worked.
        subprocess.Popen([launcher_path], **kwargs)  # noqa: S603 — operator-provided path
        return True, f"launcher dispatched: {launcher_path}"
    except Exception as exc:  # noqa: BLE001
        return False, f"launcher spawn failed: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Probe entry point.
# ---------------------------------------------------------------------------


async def run_mcp_http_probe(
    pool,
    *,
    now_fn: Optional[Callable[[], float]] = None,
    http_client_factory: Optional[Callable[..., Any]] = None,
    launcher_fn: Optional[Callable[[str], tuple[bool, str]]] = None,
) -> dict[str, Any]:
    """Single probe cycle. Returns a summary dict."""
    global _last_real_check_at, _last_alert_at
    clock = now_fn or time.monotonic

    enabled = await _read_bool(pool, ENABLED_KEY, DEFAULT_ENABLED)
    if not enabled:
        return {"ok": True, "status": "disabled", "detail": "mcp_http_probe disabled"}

    interval_min = await _read_int(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES)
    interval_s = max(1, interval_min) * 60
    now = clock()
    if _last_real_check_at and (now - _last_real_check_at) < interval_s:
        return {
            "ok": True,
            "status": "skipped_cadence",
            "detail": f"next check in {int(interval_s - (now - _last_real_check_at))}s",
        }

    base_url = (await _read_app_setting(pool, BASE_URL_KEY, "")).strip() or DEFAULT_BASE_URL
    discovery_path = (await _read_app_setting(pool, DISCOVERY_PATH_KEY, "")).strip() or DEFAULT_DISCOVERY_PATH
    probe_url = base_url.rstrip("/") + "/" + discovery_path.lstrip("/")
    timeout_s = await _read_int(pool, HTTP_TIMEOUT_SECONDS_KEY, DEFAULT_HTTP_TIMEOUT_SECONDS)

    if httpx is None and http_client_factory is None:
        logger.warning("[MCP_HTTP_PROBE] httpx not installed — skipping")
        _last_real_check_at = now
        return {"ok": False, "status": "no_httpx", "detail": "httpx not available"}

    _last_real_check_at = now
    _httpx: Any = httpx
    factory = http_client_factory or (lambda: _httpx.AsyncClient(timeout=timeout_s))

    try:
        async with factory() as client:
            response = await client.get(probe_url)
            status_code = response.status_code
    except Exception as exc:  # noqa: BLE001
        return await _handle_failure(
            pool,
            now=now,
            url=probe_url,
            fingerprint_suffix="network",
            title="MCP HTTP server unreachable",
            body=f"GET {probe_url} raised {type(exc).__name__}: {exc}",
            launcher_fn=launcher_fn,
        )

    if 200 <= status_code < 400:
        logger.info("[MCP_HTTP_PROBE] %s ok (HTTP %s)", probe_url, status_code)
        return {
            "ok": True,
            "status": "ok",
            "detail": "MCP HTTP server reachable",
            "status_code": status_code,
            "url": probe_url,
        }

    return await _handle_failure(
        pool,
        now=now,
        url=probe_url,
        fingerprint_suffix=f"http_{status_code}",
        title=f"MCP HTTP server returned HTTP {status_code}",
        body=f"GET {probe_url} returned HTTP {status_code}",
        status_code=status_code,
        launcher_fn=launcher_fn,
    )


async def _handle_failure(
    pool,
    *,
    now: float,
    url: str,
    fingerprint_suffix: str,
    title: str,
    body: str,
    status_code: int | None = None,
    launcher_fn: Optional[Callable[[str], tuple[bool, str]]] = None,
) -> dict[str, Any]:
    """Common failure path: alert (deduped) + optional auto-recover."""
    global _last_alert_at

    dedup_hours = await _read_int(pool, DEDUP_HOURS_KEY, DEFAULT_DEDUP_HOURS)
    dedup_s = max(1, dedup_hours) * 3600

    if _last_alert_at and (now - _last_alert_at) < dedup_s:
        logger.warning(
            "[MCP_HTTP_PROBE] failure (dedup window suppresses page): %s",
            body,
        )
    else:
        await _write_alert(pool, fingerprint_suffix=fingerprint_suffix, title=title, body=body)
        _last_alert_at = now

    # Auto-recovery — bounded by the rolling restart cap so we don't
    # busy-loop the launcher when the underlying problem is persistent.
    launcher_path = (await _read_app_setting(pool, LAUNCHER_PATH_KEY, "")).strip()
    recovery_detail = ""
    if launcher_path:
        restart_cap = await _read_int(pool, RESTART_CAP_KEY, DEFAULT_RESTART_CAP)
        window_min = await _read_int(pool, RESTART_WINDOW_MINUTES_KEY, DEFAULT_RESTART_WINDOW_MINUTES)
        window_s = max(1, window_min) * 60
        cutoff = now - window_s
        # Drop any restart attempts older than the rolling window.
        _restart_attempts[:] = [t for t in _restart_attempts if t >= cutoff]
        if len(_restart_attempts) < max(1, restart_cap):
            ok, detail = (launcher_fn or _try_launcher)(launcher_path)
            recovery_detail = detail
            if ok:
                _restart_attempts.append(now)
                logger.info("[MCP_HTTP_PROBE] auto-recovery: %s", detail)
            else:
                logger.warning("[MCP_HTTP_PROBE] auto-recovery skipped: %s", detail)
        else:
            recovery_detail = (
                f"restart cap reached ({len(_restart_attempts)}/{restart_cap} in "
                f"{window_min}m)"
            )
            logger.warning("[MCP_HTTP_PROBE] %s", recovery_detail)

    result: dict[str, Any] = {
        "ok": False,
        "status": "unreachable",
        "detail": body,
        "url": url,
    }
    if status_code is not None:
        result["status_code"] = status_code
    if recovery_detail:
        result["recovery_detail"] = recovery_detail
    return result
