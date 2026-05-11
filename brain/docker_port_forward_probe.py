"""Docker port-forward stuck-state probe (Glad-Labs/poindexter#222).

On Windows + Docker Desktop, specific containers periodically enter a
state where their published port LOOKS bound — ``docker ps`` reports
``0.0.0.0:PORT->INTERNAL/tcp``, the healthcheck still passes — but the
Windows wslrelay → ``com.docker.backend`` forwarding chain is broken:

- TCP handshake to ``localhost:PORT`` succeeds (Test-NetConnection OK).
- HTTP requests via ``http://localhost:PORT/`` or
  ``http://host.docker.internal:PORT/`` return "connection closed
  unexpectedly" / ``curl: (52) Empty reply from server``.
- BUT the same service is reachable normally via the container's DNS
  hostname from inside the Docker network (200 OK).

Restarting the affected container re-establishes the port forward. The
operator-side pattern was stumbled into 2026-04-29 across four
containers simultaneously (Pyroscope, GlitchTip, Alertmanager,
pgAdmin) — every container we checked was reachable from inside the
network but unreachable from the host. ``docker restart`` on each
recovered them in seconds.

This probe encodes the detect-and-recover loop:

1. For each watched service, probe ``http://<internal_hostname>:<port><path>``
   AND ``http://host.docker.internal:<port><path>`` with short timeouts
   (default 3s).
2. If the internal probe returns 2xx AND the external probe fails →
   stuck port forward → ``docker restart <container>``.
3. After the configured recovery wait (default 5s), re-probe the
   external endpoint to confirm recovery.
4. Write an ``audit_log`` row tagged with ``event_type='docker_port_forward_recovered'``
   either way so the operator can track frequency in Grafana.
5. Cap restarts per container at N within an M-minute rolling window
   (defaults: 3 per 60 min). When the cap fires, suppress further
   restarts and write an ``alert_events`` row + a Telegram-routed
   notify so the operator hears about the persistent failure.

Design parity with ``brain/backup_watcher.py`` and
``brain/smart_monitor.py``:

- DB-configurable through ``app_settings`` — every tunable is a row.
- Standalone module: only stdlib + asyncpg.
- Subprocess calls degrade gracefully (logged, not raised).
- Per-service exception isolation — one stuck service raising does
  not skip the rest.
- Subprocess uses ``CREATE_NO_WINDOW`` on win32 (Matt's "no popup
  windows" rule).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.docker_port_forward_probe")


# ---------------------------------------------------------------------------
# App_settings keys — every tunable lives in the DB so an operator can
# adjust without redeploying. See migration 20260506_071020_*.
# ---------------------------------------------------------------------------

ENABLED_KEY = "docker_port_forward_probe_enabled"
POLL_INTERVAL_MINUTES_KEY = "docker_port_forward_poll_interval_minutes"
WATCH_LIST_KEY = "docker_port_forward_watch_list"
PROBE_TIMEOUT_SECONDS_KEY = "docker_port_forward_probe_timeout_seconds"
RECOVERY_WAIT_SECONDS_KEY = "docker_port_forward_recovery_wait_seconds"
RESTART_CAP_PER_WINDOW_KEY = "docker_port_forward_restart_cap_per_window"
RESTART_CAP_WINDOW_MINUTES_KEY = "docker_port_forward_restart_cap_window_minutes"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 5
DEFAULT_PROBE_TIMEOUT_SECONDS = 3
DEFAULT_RECOVERY_WAIT_SECONDS = 5
DEFAULT_RESTART_CAP_PER_WINDOW = 3
DEFAULT_RESTART_CAP_WINDOW_MINUTES = 60

# Container-name prefix the brain expects for its docker stack. The
# internal-DNS hostname is derived by stripping this prefix (per the
# issue's example: ``poindexter-pyroscope`` → ``pyroscope``). Per-entry
# ``internal_hostname`` overrides this when the heuristic doesn't fit.
_CONTAINER_PREFIX = "poindexter-"

# Subprocess timeout for ``docker restart`` / ``docker inspect``.
# Generous because Docker Desktop on Windows can be slow to respond.
DOCKER_COMMAND_TIMEOUT_SECONDS = 30

# How long the probe is willing to take in a single cycle. With the
# default ``poll_interval_minutes=5`` brain runs this every cycle; the
# per-cycle runtime is roughly N services * (2 * probe_timeout +
# optional restart + recovery_wait + 2 * probe_timeout) ≈ <2 min for
# 12 services even in the worst case (everything stuck).
PROBE_INTERVAL_SECONDS = 5 * 60  # 5 min, mirroring the default setting


# ---------------------------------------------------------------------------
# Module-level dedup bookkeeping — per-container list of restart timestamps
# for the rolling restart-cap window. Lives across cycles so the cap is
# enforced over time rather than per-cycle.
# ---------------------------------------------------------------------------

_restart_state: dict[str, list[float]] = {}

# Per-container flag: True iff we have already emitted a "cap reached"
# alert_events row for the current cap-firing window. Reset when the
# rolling window clears (i.e., the per-container timestamp list drops
# below the cap). Suppresses re-paging on every cycle while the cap
# remains in effect.
_cap_alert_emitted: dict[str, bool] = {}


def _reset_state() -> None:
    """Test helper — wipe the restart bookkeeping."""
    _restart_state.clear()
    _cap_alert_emitted.clear()


# ---------------------------------------------------------------------------
# app_settings reads — brain is standalone so we hit the DB directly.
# Each helper degrades to its default when the row is missing or the
# fetch raises, mirroring the pattern in brain/backup_watcher.py.
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PORT_FORWARD] Could not read %s from app_settings: %s "
            "— using default %r", key, exc, default,
        )
        return default
    if val is None:
        return default
    return val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


def _parse_watch_list(raw: Any) -> list[dict[str, Any]]:
    """Parse the JSON watch list. Returns an empty list on any error
    (logged) so a malformed setting can't crash the probe.
    """
    if raw is None or str(raw).strip() == "":
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[PORT_FORWARD] Could not parse %s JSON: %s — treating as empty",
            WATCH_LIST_KEY, exc,
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "[PORT_FORWARD] %s must be a JSON array; got %s — treating as empty",
            WATCH_LIST_KEY, type(parsed).__name__,
        )
        return []
    out: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        container = entry.get("container")
        port = entry.get("port")
        if not container or port is None:
            continue
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            continue
        path = entry.get("path") or "/"
        internal_hostname = entry.get("internal_hostname")
        if not internal_hostname:
            # Strip the conventional container prefix; fall back to the
            # full name if it doesn't match.
            if str(container).startswith(_CONTAINER_PREFIX):
                internal_hostname = str(container)[len(_CONTAINER_PREFIX):]
            else:
                internal_hostname = str(container)
        # Optional ``host_port`` override. Compose maps host:CONTAINER
        # ports separately from container-internal ports
        # (e.g. prometheus is ``9091:9090`` -- container listens on
        # 9090, host exposes 9091). Without this override the external
        # probe always failed for non-1:1-mapped services and the probe
        # falsely flagged "stuck port-forward" every cycle.
        host_port_raw = entry.get("host_port")
        try:
            host_port = (
                int(host_port_raw) if host_port_raw is not None else port_int
            )
        except (TypeError, ValueError):
            # Silent fallback to ``port`` would re-create the bug from
            # Glad-Labs/poindexter#472, where the external probe quietly
            # used the wrong port for 24h. Surface the misconfiguration.
            logger.warning(
                "[PORT_FORWARD] %s entry has unparseable host_port=%r — "
                "falling back to port %d for the external probe URL",
                container, host_port_raw, port_int,
            )
            host_port = port_int
        out.append({
            "container": str(container),
            "port": port_int,
            "host_port": host_port,
            "path": str(path),
            "internal_hostname": str(internal_hostname),
        })
    return out


async def _read_config(pool: Any) -> dict[str, Any]:
    """Pull every probe tunable in one helper.

    Returns a dict with all seven settings resolved + coerced. Cheap
    because the brain pool is local to the same Postgres instance.
    """
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"),
        DEFAULT_ENABLED,
    )
    poll_interval_minutes = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES),
        DEFAULT_POLL_INTERVAL_MINUTES,
    )
    watch_list_raw = await _read_setting(pool, WATCH_LIST_KEY, "")
    watch_list = _parse_watch_list(watch_list_raw)
    probe_timeout_seconds = _coerce_int(
        await _read_setting(pool, PROBE_TIMEOUT_SECONDS_KEY, DEFAULT_PROBE_TIMEOUT_SECONDS),
        DEFAULT_PROBE_TIMEOUT_SECONDS,
    )
    recovery_wait_seconds = _coerce_int(
        await _read_setting(pool, RECOVERY_WAIT_SECONDS_KEY, DEFAULT_RECOVERY_WAIT_SECONDS),
        DEFAULT_RECOVERY_WAIT_SECONDS,
    )
    restart_cap_per_window = _coerce_int(
        await _read_setting(pool, RESTART_CAP_PER_WINDOW_KEY, DEFAULT_RESTART_CAP_PER_WINDOW),
        DEFAULT_RESTART_CAP_PER_WINDOW,
    )
    restart_cap_window_minutes = _coerce_int(
        await _read_setting(pool, RESTART_CAP_WINDOW_MINUTES_KEY, DEFAULT_RESTART_CAP_WINDOW_MINUTES),
        DEFAULT_RESTART_CAP_WINDOW_MINUTES,
    )

    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "watch_list": watch_list,
        "probe_timeout_seconds": probe_timeout_seconds,
        "recovery_wait_seconds": recovery_wait_seconds,
        "restart_cap_per_window": restart_cap_per_window,
        "restart_cap_window_minutes": restart_cap_window_minutes,
    }


# ---------------------------------------------------------------------------
# HTTP probe — stdlib only (urllib.request) so the brain stays on its
# tight dep set. Returns True iff the request returned a 2xx response
# within the timeout. Any error (timeout, connection refused, empty
# reply, non-2xx status) is treated as failure — the caller distinguishes
# stuck-port-forward from actual-outage by combining internal + external
# probe outcomes.
# ---------------------------------------------------------------------------


def _http_probe(url: str, timeout_seconds: float) -> bool:
    """Issue a GET against ``url`` and return True iff the response
    status is 2xx within the timeout.

    Empty replies, RST resets, timeouts, DNS failures and non-2xx
    statuses all return False. Logged at debug level — the probe
    summary already captures the per-service outcome.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        # urllib follows redirects by default; we want the final status.
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 — internal probe
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(status) < 300
    except urllib.error.HTTPError as exc:
        # 4xx/5xx counts as "reachable but unhealthy" — that's an
        # application-layer issue, not a port-forward issue. We treat
        # only 2xx as ok so a 503 from a flapping service doesn't get
        # us to restart-the-container instead of paging.
        logger.debug(
            "[PORT_FORWARD] HTTP %d on %s: %s",
            exc.code, url, str(exc.reason)[:120],
        )
        return False
    except urllib.error.URLError as exc:
        logger.debug(
            "[PORT_FORWARD] URLError on %s: %s",
            url, str(exc.reason)[:160],
        )
        return False
    except Exception as exc:  # noqa: BLE001
        # http.client.RemoteDisconnected ("connection closed unexpectedly")
        # is a subclass of ConnectionError on 3.10+ — caught here.
        logger.debug(
            "[PORT_FORWARD] %s on %s: %s",
            type(exc).__name__, url, str(exc)[:160],
        )
        return False


# ---------------------------------------------------------------------------
# Docker subprocess wrappers — same shape as backup_watcher's
# ``_restart_backup_container`` / compose_drift_probe's
# ``_docker_inspect``.
# ---------------------------------------------------------------------------


def _container_exists(container: str) -> bool:
    """True iff ``docker inspect <container>`` exits 0.

    A False return means either the container isn't running or docker
    isn't reachable; the caller treats both as ``unwatched`` (skip
    the probe; don't crash). Distinguishing the two would require an
    additional ``docker version`` probe which we don't need.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": DOCKER_COMMAND_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            **kwargs,
        )
        return result.returncode == 0
    except FileNotFoundError:
        logger.warning(
            "[PORT_FORWARD] docker CLI not on PATH — brain image is "
            "missing the docker binary; cannot enumerate containers."
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning(
            "[PORT_FORWARD] docker inspect %s timed out after %ds",
            container, DOCKER_COMMAND_TIMEOUT_SECONDS,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PORT_FORWARD] docker inspect %s error: %s", container, exc,
        )
        return False


def _restart_container(container: str) -> tuple[bool, str]:
    """Run ``docker restart <container>``. Returns (ok, message).

    Brain container has /var/run/docker.sock bind-mounted (see
    docker-compose.local.yml) and the docker CLI installed
    (brain/Dockerfile). Never raises — caller handles the bool. On
    Windows the Docker CLI is invoked from the host via the same
    PATH-discovered binary; the CREATE_NO_WINDOW flag suppresses the
    flash-and-vanish console window per Matt's "no popups" rule.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": DOCKER_COMMAND_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(
            ["docker", "restart", container],
            **kwargs,
        )
        if result.returncode == 0:
            return True, f"Restarted {container}"
        return False, (
            f"docker restart {container} exit {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except FileNotFoundError:
        return False, (
            "docker CLI not on PATH (brain image missing docker binary?)"
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"docker restart {container} timed out after "
            f"{DOCKER_COMMAND_TIMEOUT_SECONDS}s"
        )
    except Exception as exc:  # noqa: BLE001
        return False, (
            f"docker restart error: {type(exc).__name__}: {str(exc)[:160]}"
        )


# ---------------------------------------------------------------------------
# Restart cap — rolling-window per-container.
# ---------------------------------------------------------------------------


def _record_restart(container: str, *, now: float) -> None:
    """Record a restart timestamp for the rolling cap window."""
    _restart_state.setdefault(container, []).append(now)


def _restarts_in_window(
    container: str,
    *,
    now: float,
    window_seconds: float,
) -> int:
    """Prune old timestamps and return the count of restarts in window."""
    history = _restart_state.get(container, [])
    cutoff = now - window_seconds
    pruned = [t for t in history if t >= cutoff]
    if pruned != history:
        _restart_state[container] = pruned
    if not pruned:
        # Cap-alert flag clears once the window is empty so a future
        # cap firing pages the operator afresh.
        _cap_alert_emitted.pop(container, None)
    return len(pruned)


# ---------------------------------------------------------------------------
# Alert + audit emitters — same shape as backup_watcher / smart_monitor
# so the dispatcher renders the alerts uniformly.
# ---------------------------------------------------------------------------


async def _emit_cap_alert(
    pool: Any,
    *,
    container: str,
    cap: int,
    window_minutes: int,
) -> bool:
    """Insert a firing ``alert_events`` row when the restart cap fires.

    The dispatcher picks it up on its next 30s poll and pages
    Telegram + Discord. Idempotent at the cycle level via
    ``_cap_alert_emitted`` so we don't re-page every cycle while the
    cap remains in effect.
    """
    alertname = "docker_port_forward_restart_capped"
    labels = {
        "source": "brain.docker_port_forward_probe",
        "container": container,
        "category": "docker",
    }
    annotations = {
        "summary": (
            f"Restart cap reached for {container}: "
            f"{cap} restarts within {window_minutes} min"
        ),
        "description": (
            f"The Docker port-forward probe has restarted {container} "
            f"{cap} times within the last {window_minutes} min and "
            f"will not restart it again until the rolling window "
            f"clears. The underlying issue may not be a stuck port "
            f"forward — investigate the container manually."
        ),
    }
    # Stable per (alertname, container). int(time.time()) used to be
    # appended here, which made every cycle write a unique fingerprint
    # and defeated downstream dedup (Glad-Labs/poindexter#428).
    fingerprint = f"docker-port-forward-cap-{container}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES (
                $1, 'warning', 'firing', $2::jsonb, $3::jsonb, NOW(), $4
            )
            """,
            alertname,
            json.dumps(labels),
            json.dumps(annotations),
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PORT_FORWARD] Failed to write cap alert for %s: %s",
            container, exc,
        )
        return False


async def _emit_recovery_failed_alert(
    pool: Any,
    *,
    container: str,
    detail: str,
) -> bool:
    """Insert a firing ``alert_events`` row when a restart was attempted
    but the external probe still fails afterwards.

    Distinct alertname from the cap path so the operator sees the two
    failure modes separately in their alerts feed.
    """
    alertname = "docker_port_forward_recovery_failed"
    labels = {
        "source": "brain.docker_port_forward_probe",
        "container": container,
        "category": "docker",
    }
    annotations = {
        "summary": (
            f"Docker restart did not recover {container}'s port forward"
        ),
        "description": detail,
    }
    # Stable per (alertname, container) — see _emit_cap_alert above
    # (Glad-Labs/poindexter#428).
    fingerprint = f"docker-port-forward-recovery-failed-{container}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES (
                $1, 'warning', 'firing', $2::jsonb, $3::jsonb, NOW(), $4
            )
            """,
            alertname,
            json.dumps(labels),
            json.dumps(annotations),
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PORT_FORWARD] Failed to write recovery_failed alert for %s: %s",
            container, exc,
        )
        return False


async def _emit_audit_event(
    pool: Any,
    event: str,
    detail: str,
    *,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Same shape as backup_watcher's audit emitter so the timeline
    shows port-forward activity beside the rest of the brain's probes.
    """
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    severity = "warning" if (
        "capped" in event or "failed" in event or "stuck" in event
    ) else "info"
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            event,
            "brain.docker_port_forward_probe",
            json.dumps(payload),
            severity,
        )
    except Exception as exc:  # noqa: BLE001
        # audit_log table may not exist on a very fresh install — log
        # and carry on so the probe still does its job.
        logger.debug(
            "[PORT_FORWARD] Could not write audit event %s: %s",
            event, exc,
        )


# ---------------------------------------------------------------------------
# Per-service check — the heart of the probe. Called once per service
# per cycle. Returns a structured per-service summary dict.
# ---------------------------------------------------------------------------


async def _check_one_service(
    pool: Any,
    *,
    service: dict[str, Any],
    config: dict[str, Any],
    http_probe_fn: Callable[[str, float], bool],
    container_exists_fn: Callable[[str], bool],
    restart_fn: Callable[[str], tuple[bool, str]],
    sleep_fn: Callable[[float], None],
    notify_fn: Callable[..., None],
    now_fn: Callable[[], float],
) -> dict[str, Any]:
    """Probe a single service.

    Returns a per-service summary dict with at minimum ``container``,
    ``ok``, ``status``. Additional keys depend on the path taken
    (``recovery_ms``, ``retried_n``, ``restart_error``, etc.).
    """
    container = service["container"]
    port = service["port"]
    path = service["path"]
    internal_hostname = service["internal_hostname"]
    timeout = float(config["probe_timeout_seconds"])
    recovery_wait = float(config["recovery_wait_seconds"])
    cap = int(config["restart_cap_per_window"])
    window_seconds = float(config["restart_cap_window_minutes"]) * 60.0

    # 0) Skip silently if the container isn't running. Operators may
    # have stripped a service from their compose file; we don't want
    # the probe to crash or spam alerts about a deliberately missing
    # container.
    if not container_exists_fn(container):
        return {
            "ok": True,
            "status": "unwatched",
            "container": container,
            "detail": f"container {container} not running; skipping probe",
        }

    # ``host_port`` defaults to ``port`` for 1:1 mappings; override it
    # via the watch list entry when compose maps host:CONTAINER on
    # different sides (e.g. ``9091:9090``). The internal probe always
    # uses the container-side port; the external probe uses the host
    # side, since that's what wslrelay actually forwards.
    host_port = service.get("host_port", port)
    internal_url = f"http://{internal_hostname}:{port}{path}"
    external_url = f"http://host.docker.internal:{host_port}{path}"

    ok_internal = http_probe_fn(internal_url, timeout)
    ok_external = http_probe_fn(external_url, timeout)

    # 1) Both ok → happy path.
    if ok_internal and ok_external:
        return {
            "ok": True,
            "status": "ok",
            "container": container,
            "internal_url": internal_url,
            "external_url": external_url,
        }

    # 2) Both failing → real outage, not a port-forward bug. Skip the
    # restart (the existing healthcheck / Uptime Kuma path will handle
    # it) and surface the state in the summary so the brain cycle's
    # probe_results reflects it.
    if not ok_internal and not ok_external:
        await _emit_audit_event(
            pool,
            "docker_port_forward_service_down",
            (
                f"Service {container} unreachable from both internal "
                f"({internal_url}) and external ({external_url}); not "
                f"a stuck port forward. Letting other monitoring page."
            ),
            extra={
                "container": container,
                "internal_url": internal_url,
                "external_url": external_url,
            },
        )
        return {
            "ok": False,
            "status": "service_down",
            "container": container,
            "internal_url": internal_url,
            "external_url": external_url,
        }

    # 3) Internal fails, external ok — odd inverse pattern. Not the
    # bug we're hunting; just log and move on so we don't restart
    # something we shouldn't.
    if not ok_internal and ok_external:
        return {
            "ok": True,
            "status": "internal_only_down",
            "container": container,
            "internal_url": internal_url,
            "external_url": external_url,
            "detail": (
                "External reachable but internal not — probably a brain-"
                "side DNS or networking glitch, not a stuck port forward."
            ),
        }

    # 4) Internal ok, external fails → THIS is the stuck-port-forward
    # signature. Check the restart cap before kicking the container.
    now = now_fn()
    restarts_done = _restarts_in_window(container, now=now, window_seconds=window_seconds)
    if restarts_done >= cap:
        # Only emit the cap alert once per active window — the flag is
        # cleared in _restarts_in_window when the window empties.
        if not _cap_alert_emitted.get(container, False):
            await _emit_cap_alert(
                pool,
                container=container,
                cap=cap,
                window_minutes=int(config["restart_cap_window_minutes"]),
            )
            _cap_alert_emitted[container] = True
        await _emit_audit_event(
            pool,
            "docker_port_forward_restart_capped",
            (
                f"Restart cap ({cap}/{int(config['restart_cap_window_minutes'])}m) "
                f"reached for {container}; suppressing further restarts."
            ),
            extra={
                "container": container,
                "cap": cap,
                "window_minutes": int(config["restart_cap_window_minutes"]),
                "restarts_in_window": restarts_done,
            },
        )
        try:
            notify_fn(
                title=(
                    f"Docker port-forward restart cap reached for {container}"
                ),
                detail=(
                    f"Restarted {container} {restarts_done} times within "
                    f"the last {int(config['restart_cap_window_minutes'])} "
                    f"minutes. Cap is {cap}; suppressing further restarts. "
                    f"Investigate manually — the underlying issue may not "
                    f"be a stuck port forward."
                ),
                source="brain.docker_port_forward_probe",
                severity="warning",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PORT_FORWARD] notify_fn failed: %s", exc)
        return {
            "ok": False,
            "status": "restart_capped",
            "container": container,
            "internal_url": internal_url,
            "external_url": external_url,
            "restarts_in_window": restarts_done,
            "cap": cap,
        }

    # 5) Restart!
    logger.warning(
        "[PORT_FORWARD] Stuck port forward detected on %s: internal "
        "(%s) ok, external (%s) failing — restarting container.",
        container, internal_url, external_url,
    )
    restart_started = now_fn()
    restart_ok, restart_msg = restart_fn(container)
    _record_restart(container, now=restart_started)
    retried_n = len(_restart_state.get(container, []))

    if not restart_ok:
        await _emit_audit_event(
            pool,
            "docker_port_forward_restart_failed",
            (
                f"docker restart {container} failed: {restart_msg}. "
                f"External probe was {external_url}."
            ),
            extra={
                "container": container,
                "external_url": external_url,
                "restart_error": restart_msg,
                "retried_n": retried_n,
            },
        )
        if "docker CLI" in restart_msg or "docker socket" in restart_msg.lower():
            try:
                notify_fn(
                    title=(
                        "Docker port-forward probe cannot restart container"
                    ),
                    detail=(
                        f"{restart_msg}\n\nRecommended fix: ensure "
                        f"/var/run/docker.sock is bind-mounted into the "
                        f"brain container and the docker CLI is "
                        f"installed in the brain image."
                    ),
                    source="brain.docker_port_forward_probe",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PORT_FORWARD] notify_fn failed: %s", exc)
        return {
            "ok": False,
            "status": "restart_failed",
            "container": container,
            "internal_url": internal_url,
            "external_url": external_url,
            "restart_error": restart_msg,
            "retried_n": retried_n,
        }

    # 6) Wait briefly, then re-probe to confirm recovery.
    sleep_fn(recovery_wait)
    recovered = http_probe_fn(external_url, timeout)
    recovery_ms = int((now_fn() - restart_started) * 1000)

    audit_extra = {
        "container": container,
        "port": port,
        "internal_url": internal_url,
        "external_url": external_url,
        "recovery_ms": recovery_ms,
        "retried_n": retried_n,
        "recovered": bool(recovered),
    }
    await _emit_audit_event(
        pool,
        "docker_port_forward_recovered",
        (
            f"Restarted {container} after stuck-port-forward detection: "
            f"recovered={recovered}, recovery_ms={recovery_ms}, "
            f"retried_n={retried_n}."
        ),
        extra=audit_extra,
    )

    if recovered:
        return {
            "ok": True,
            "status": "recovered",
            "container": container,
            "internal_url": internal_url,
            "external_url": external_url,
            "recovery_ms": recovery_ms,
            "retried_n": retried_n,
        }

    # 7) Restart accepted but external probe still failing. Page the
    # operator via alert_events so they know the auto-recovery isn't
    # working for this container.
    detail = (
        f"Restarted {container} but {external_url} still not reachable "
        f"after {recovery_wait:.0f}s recovery wait. The stuck-port-forward "
        f"recovery did not work — investigate the container manually."
    )
    logger.warning("[PORT_FORWARD] %s", detail)
    await _emit_recovery_failed_alert(
        pool,
        container=container,
        detail=detail,
    )
    return {
        "ok": False,
        "status": "recovery_failed",
        "container": container,
        "internal_url": internal_url,
        "external_url": external_url,
        "recovery_ms": recovery_ms,
        "retried_n": retried_n,
    }


# ---------------------------------------------------------------------------
# Top-level probe entry point — called once per brain cycle.
# ---------------------------------------------------------------------------


async def run_docker_port_forward_probe(
    pool: Any,
    *,
    http_probe_fn: Optional[Callable[[str, float], bool]] = None,
    container_exists_fn: Optional[Callable[[str], bool]] = None,
    restart_fn: Optional[Callable[[str], tuple[bool, str]]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    notify_fn: Optional[Callable[..., None]] = None,
    now_fn: Optional[Callable[[], float]] = None,
) -> dict[str, Any]:
    """Single execution of the Docker port-forward probe.

    Args:
        pool: asyncpg pool for app_settings + alert_events + audit_log.
        http_probe_fn: ``(url, timeout) -> bool`` — defaults to the
            stdlib HTTP probe. Tests inject canned outcomes.
        container_exists_fn: ``(container) -> bool`` — defaults to
            ``docker inspect``. Tests inject a stub.
        restart_fn: ``(container) -> (ok, msg)`` — defaults to the
            real ``docker restart``. Tests inject a stub.
        sleep_fn: ``(seconds) -> None`` — defaults to ``time.sleep``.
            Tests inject a no-op so they don't wait.
        notify_fn: operator notifier callable. Defaults to
            :func:`brain.operator_notifier.notify_operator`. Used for
            cap-fired and docker-broken paths only.
        now_fn: ``() -> float`` — defaults to ``time.time``.

    Returns a structured summary suitable for inclusion in
    ``brain_decisions`` / the cycle's ``probe_results`` map.
    """
    http_probe_fn = http_probe_fn or _http_probe
    container_exists_fn = container_exists_fn or _container_exists
    restart_fn = restart_fn or _restart_container
    sleep_fn = sleep_fn or time.sleep
    notify_fn = notify_fn or notify_operator
    now_fn = now_fn or time.time

    config = await _read_config(pool)
    if not config["enabled"]:
        return {
            "ok": True,
            "status": "disabled",
            "detail": (
                f"Docker port-forward probe disabled "
                f"(app_settings.{ENABLED_KEY}=false)"
            ),
            "services": {},
        }

    watch_list = config["watch_list"]
    if not watch_list:
        return {
            "ok": True,
            "status": "no_watch_list",
            "detail": (
                f"No services configured in app_settings.{WATCH_LIST_KEY}; "
                f"probe is a no-op."
            ),
            "services": {},
        }

    service_summaries: dict[str, dict[str, Any]] = {}
    for service in watch_list:
        container = service["container"]
        try:
            service_summaries[container] = await _check_one_service(
                pool,
                service=service,
                config=config,
                http_probe_fn=http_probe_fn,
                container_exists_fn=container_exists_fn,
                restart_fn=restart_fn,
                sleep_fn=sleep_fn,
                notify_fn=notify_fn,
                now_fn=now_fn,
            )
        except Exception as exc:  # noqa: BLE001
            # One service blowing up shouldn't take the others down —
            # log and carry on so at least the partial check runs.
            logger.warning(
                "[PORT_FORWARD] %s check raised: %s", container, exc,
                exc_info=True,
            )
            service_summaries[container] = {
                "ok": False,
                "status": "exception",
                "container": container,
                "error": str(exc)[:200],
            }

    overall_ok = all(s.get("ok", False) for s in service_summaries.values())
    statuses = ", ".join(
        f"{container}={s.get('status', 'unknown')}"
        for container, s in service_summaries.items()
    )
    return {
        "ok": overall_ok,
        "status": "ok" if overall_ok else "degraded",
        "detail": f"Docker port-forward probe cycle: {statuses}",
        "services": service_summaries,
        "config": {k: v for k, v in config.items() if k not in ("enabled", "watch_list")},
    }


# ---------------------------------------------------------------------------
# Probe-Protocol adapter — for the registry-driven path. Mirrors
# BackupWatcherProbe / SmartMonitorProbe so this slots into the same
# registry without new infrastructure.
# ---------------------------------------------------------------------------


class DockerPortForwardProbe:
    """Probe-Protocol-compatible wrapper around
    :func:`run_docker_port_forward_probe`.
    """

    name: str = "docker_port_forward"
    description: str = (
        "Detects stuck Docker Desktop port forwards (TCP up, HTTP "
        "broken via host.docker.internal but reachable via container "
        "hostname) and auto-recovers via `docker restart`."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_docker_port_forward_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                "status": summary.get("status"),
                "services": {
                    container: s.get("status")
                    for container, s in (summary.get("services") or {}).items()
                },
            },
            severity="warning" if not summary.get("ok") else "info",
        )
