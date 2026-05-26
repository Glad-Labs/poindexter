"""Prefect stuck-flow probe — detect ``content_generation`` flow runs
that have been in state=RUNNING longer than humans would believe.

Captured 2026-05-26: a single ``content_generation`` flow run
(``romantic-harrier``) sat in state=RUNNING for **35 hours** with
``total_run_time=0.0s``. Prefect's state machine never received a
crash signal — the worker process backing it died/disconnected and
Prefect was happy to hold the slot indefinitely. With one stuck flow
run holding the deployment's concurrency slot, **every subsequent
cron-scheduled run piled up behind it**, the content pipeline went
idle, and the only operator signal was a downstream
``cost_freshness`` staleness alert. The brain's reasoner then
pattern-matched that into an Ollama-unresponsive guess that was
wrong — by which point the queue had been blocked for a day and a
half.

This probe closes that gap. Every brain cycle it queries Prefect for
``content_generation`` runs whose ``start_time`` is older than
``prefect_stuck_flow_threshold_minutes`` (default 30) AND whose
``state.type == 'RUNNING'``. Matches get:

  1. ``notify_operator()`` paged (Telegram + Discord) with the run id,
     age, and a one-liner fix instruction.
  2. ``audit_log`` row written (``probe.prefect_stuck_flow_detected``).
  3. *Optional* auto-CRASHED via Prefect's ``/set_state`` API — gated
     behind ``prefect_stuck_flow_auto_crash`` (default ``"false"`` so
     opt-in until the operator trusts the threshold).

Why no in-memory dedupe like ``glitchtip_triage_probe``: there should
never be MORE than one stuck flow run at a time on the
``content_generation`` deployment (concurrency=1). If the same run is
still stuck on the next cycle that's a re-page worth seeing —
operator hasn't acted. If two flow runs are simultaneously stuck,
that's a different bug and we want to see both.

Standalone — stdlib + ``httpx`` only (same brain-only deps as
``glitchtip_triage_probe``).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # flat import when brain/ is on sys.path (container runtime)
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified path
    from brain.operator_notifier import notify_operator

try:
    from docker_utils import localize_url
except ImportError:  # pragma: no cover — package-qualified path
    from brain.docker_utils import localize_url


logger = logging.getLogger("brain.prefect_stuck_flow_probe")


# ---------------------------------------------------------------------------
# Tunables — ALL read from app_settings at probe time so the operator can
# adjust without redeploying the brain. Defaults are conservative; expect
# the operator to tune the threshold downward as confidence builds.
# ---------------------------------------------------------------------------

ENABLED_SETTING_KEY = "prefect_stuck_flow_probe_enabled"
ENABLED_DEFAULT = "true"

# Where Prefect's REST API is reachable from the brain. Defaults match the
# in-stack compose service name. ``localize_url`` rewrites localhost →
# host.docker.internal only when the brain runs inside the container.
BASE_URL_SETTING_KEY = "prefect_api_base_url"
BASE_URL_DEFAULT = "http://prefect-server:4200/api"

# Which flow names to probe. The content pipeline is the one that bit us;
# operators can add more (e.g. dev_diary_compositor if it ever becomes a
# separate flow) by comma-separating slugs in the app_setting.
FLOW_NAMES_SETTING_KEY = "prefect_stuck_flow_flow_names"
FLOW_NAMES_DEFAULT = "content_generation"

# A flow run that's been RUNNING this long is suspicious. The captured
# romantic-harrier sat for 35h, so 30min is ~70x the normal duration
# (typical content_generation finishes in 5-10 min). Tune lower if the
# operator wants tighter detection; tune higher if a legitimately-long
# run is firing false positives.
THRESHOLD_MINUTES_SETTING_KEY = "prefect_stuck_flow_threshold_minutes"
THRESHOLD_MINUTES_DEFAULT = 30

# Opt-in auto-remediation. When enabled, every detected stuck run gets
# its state force-transitioned to CRASHED so subsequent scheduled runs
# can dispatch immediately instead of waiting on operator intervention.
# Defaults to false because the operator should see the page before
# the brain takes destructive action — once the threshold is tuned,
# flip this to true for hands-off recovery.
AUTO_CRASH_SETTING_KEY = "prefect_stuck_flow_auto_crash"
AUTO_CRASH_DEFAULT = "false"

# Probe interval — same 5-minute brain cycle as siblings. Internal
# behaviour: the probe is cheap (one POST + maybe one set_state per
# stuck run), so per-cycle is fine.
PROBE_INTERVAL_SECONDS = 300

# Per-request HTTP timeouts. Prefect's API is local so these are tight.
HTTP_CONNECT_TIMEOUT_S = 3.0
HTTP_READ_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Settings I/O
# ---------------------------------------------------------------------------


async def _read_setting(pool, key: str, default: str = "") -> str:
    """Read a string app_settings value. Defaults gracefully on failure."""
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:
        logger.warning(
            "[PREFECT_STUCK_FLOW] Could not read %s from app_settings: %s",
            key, exc,
        )
        return default
    if val is None:
        return default
    return str(val)


async def _read_int(pool, key: str, default: int) -> int:
    raw = (await _read_setting(pool, key, str(default))).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


async def _read_bool(pool, key: str, default: bool) -> bool:
    raw = (await _read_setting(pool, key, "true" if default else "false")).strip().lower()
    return raw in ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------
# Prefect API helpers
# ---------------------------------------------------------------------------


async def _fetch_running_flow_runs(
    client: Any, base_url: str, flow_names: list[str],
) -> list[dict[str, Any]]:
    """POST ``/flow_runs/filter`` for RUNNING runs across the named flows.

    Returns the raw list of run dicts. Prefect's filter accepts a flow
    name filter directly so we don't pull every running flow and post-
    filter — that's both cheaper and safer if the operator runs other
    flows on the same Prefect we don't care about.
    """
    payload = {
        "sort": "START_TIME_ASC",
        "limit": 50,
        "flow_runs": {
            "state": {"type": {"any_": ["RUNNING"]}},
        },
        "flows": {
            "name": {"any_": flow_names},
        },
    }
    resp = await client.post(
        f"{base_url.rstrip('/')}/flow_runs/filter",
        json=payload,
    )
    if resp.status_code >= 400:
        logger.warning(
            "[PREFECT_STUCK_FLOW] /flow_runs/filter returned %d: %s",
            resp.status_code, resp.text[:200],
        )
        return []
    return resp.json() or []


async def _crash_flow_run(
    client: Any, base_url: str, run_id: str, age_minutes: int,
) -> bool:
    """Force-transition a run to CRASHED. Returns True on 201.

    Mirrors the manual ``curl ... /set_state`` call the operator used
    on 2026-05-26 to unstick ``romantic-harrier``. ``force: true`` is
    REQUIRED — Prefect rejects naive RUNNING → CRASHED transitions as
    invalid state changes; the force flag is what makes
    operator-driven crashes go through.
    """
    payload = {
        "state": {
            "type": "CRASHED",
            "name": "Crashed",
            "message": (
                f"Auto-crashed by brain.prefect_stuck_flow_probe — "
                f"flow run held RUNNING for {age_minutes} minutes with "
                f"no progress, blocking subsequent dispatches."
            ),
        },
        "force": True,
    }
    try:
        resp = await client.post(
            f"{base_url.rstrip('/')}/flow_runs/{run_id}/set_state",
            json=payload,
        )
    except Exception as exc:
        logger.warning(
            "[PREFECT_STUCK_FLOW] set_state POST failed for %s: %s",
            run_id, exc,
        )
        return False
    if resp.status_code == 201:
        return True
    logger.warning(
        "[PREFECT_STUCK_FLOW] set_state returned %d for %s: %s",
        resp.status_code, run_id, resp.text[:200],
    )
    return False


def _age_minutes(start_time_iso: str | None) -> int | None:
    """Minutes between ``start_time_iso`` and now (UTC).

    Returns None if the timestamp is missing or unparseable — the
    caller treats None as "skip this run, can't reason about its
    age safely".
    """
    if not start_time_iso:
        return None
    try:
        # Prefect emits ISO-8601 with a trailing 'Z' or '+00:00'; both
        # are valid for fromisoformat once we normalize the Z form.
        normalized = start_time_iso.replace("Z", "+00:00")
        start = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - start
    return int(delta.total_seconds() // 60)


# ---------------------------------------------------------------------------
# Audit log helper (best-effort; never raises)
# ---------------------------------------------------------------------------


async def _emit_audit_event(
    pool, event_type: str, detail: str, *, payload: dict[str, Any] | None = None,
) -> None:
    """Write a row to ``audit_log`` for the daily roll-up consumers.

    Best-effort — if the table doesn't exist (fresh DB during setup)
    or the insert fails, we log and move on; the brain cycle is
    cheaper without DB exceptions bubbling up here.
    """
    import json
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, 'brain.prefect_stuck_flow_probe', $2::jsonb, 'warning')
            """,
            event_type,
            json.dumps({"detail": detail, **(payload or {})}),
        )
    except Exception as exc:
        logger.debug(
            "[PREFECT_STUCK_FLOW] audit_log insert skipped (%s)", exc,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_prefect_stuck_flow_probe(
    pool,
    *,
    notify_fn=None,
    http_client_factory=None,
) -> dict[str, Any]:
    """Single execution of the stuck-flow probe. Returns a structured summary.

    Args:
        pool: asyncpg pool for app_settings + audit_log.
        notify_fn: operator notifier callable (defaults to
            :func:`brain.operator_notifier.notify_operator`). Tests inject
            a spy here.
        http_client_factory: zero-arg callable returning an
            ``httpx.AsyncClient`` context manager — tests inject a mock.
    """
    notify_fn = notify_fn or notify_operator

    enabled_raw = (await _read_setting(pool, ENABLED_SETTING_KEY, ENABLED_DEFAULT)).strip().lower()
    if enabled_raw in ("false", "0", "no", "off"):
        return {
            "ok": True,
            "status": "disabled",
            "detail": f"Probe disabled via app_settings.{ENABLED_SETTING_KEY}",
        }

    if httpx is None:  # pragma: no cover — only when dep is uninstalled
        return {
            "ok": False,
            "status": "no_httpx",
            "detail": "httpx not installed in brain image",
        }

    base_url = (
        await _read_setting(pool, BASE_URL_SETTING_KEY, BASE_URL_DEFAULT)
    ).strip() or BASE_URL_DEFAULT
    base_url = localize_url(base_url).rstrip("/")

    flow_names_raw = (
        await _read_setting(pool, FLOW_NAMES_SETTING_KEY, FLOW_NAMES_DEFAULT)
    ).strip()
    flow_names = [n.strip() for n in flow_names_raw.split(",") if n.strip()]
    if not flow_names:
        flow_names = [FLOW_NAMES_DEFAULT]

    threshold_min = await _read_int(
        pool, THRESHOLD_MINUTES_SETTING_KEY, THRESHOLD_MINUTES_DEFAULT,
    )
    auto_crash = await _read_bool(pool, AUTO_CRASH_SETTING_KEY, False)

    timeout = httpx.Timeout(HTTP_READ_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Poindexter-PrefectStuckFlow/1.0",
    }
    if http_client_factory is None:
        client_cm = httpx.AsyncClient(timeout=timeout, headers=headers)
    else:
        client_cm = http_client_factory()

    started = time.time()
    stuck_runs: list[dict[str, Any]] = []
    crashed_runs: list[dict[str, Any]] = []
    crash_failed: list[dict[str, Any]] = []
    seen = 0

    try:
        async with client_cm as client:
            running = await _fetch_running_flow_runs(client, base_url, flow_names)
            seen = len(running)

            for run in running:
                run_id = str(run.get("id") or "")
                if not run_id:
                    continue
                name = run.get("name") or "(unnamed)"
                age = _age_minutes(run.get("start_time"))
                if age is None or age < threshold_min:
                    continue

                # Detail string used in both notify_operator + audit_log.
                detail = (
                    f"Prefect flow run '{name}' (id {run_id[:12]}…) has "
                    f"been in state=RUNNING for {age} minutes — threshold "
                    f"is {threshold_min}. While it holds the deployment's "
                    f"slot, subsequent scheduled runs queue up and the "
                    f"content pipeline stays idle. Manual unstick: "
                    f"curl -X POST {base_url}/flow_runs/{run_id}/set_state "
                    f"-H 'Content-Type: application/json' "
                    f'-d \'{{"state":{{"type":"CRASHED","name":"Crashed"}},"force":true}}\''
                )
                stuck_runs.append({
                    "id": run_id,
                    "name": name,
                    "age_minutes": age,
                    "start_time": run.get("start_time"),
                })

                await _emit_audit_event(
                    pool,
                    "probe.prefect_stuck_flow_detected",
                    detail,
                    payload={
                        "run_id": run_id, "name": name,
                        "age_minutes": age, "threshold_minutes": threshold_min,
                    },
                )

                notify_fn(
                    title=f"Prefect flow stuck: {name} ({age}m)",
                    detail=detail,
                    source="brain.prefect_stuck_flow_probe",
                    severity="warning",
                )

                if auto_crash:
                    crashed = await _crash_flow_run(
                        client, base_url, run_id, age,
                    )
                    if crashed:
                        crashed_runs.append({"id": run_id, "name": name, "age_minutes": age})
                        await _emit_audit_event(
                            pool,
                            "probe.prefect_stuck_flow_auto_crashed",
                            f"Auto-crashed {name} ({run_id[:12]}…) after {age}m",
                            payload={"run_id": run_id, "name": name, "age_minutes": age},
                        )
                    else:
                        crash_failed.append({"id": run_id, "name": name, "age_minutes": age})
    except Exception as exc:
        logger.warning(
            "[PREFECT_STUCK_FLOW] cycle raised: %s — returning unknown", exc,
        )
        return {
            "ok": False,
            "status": "error",
            "detail": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    elapsed_ms = int((time.time() - started) * 1000)
    summary = {
        "ok": True,
        "status": "ran",
        "detail": (
            f"Scanned {seen} running flow(s); "
            f"{len(stuck_runs)} stuck >= {threshold_min}m; "
            f"{len(crashed_runs)} auto-crashed; "
            f"{len(crash_failed)} crash failed"
        ),
        "running_seen": seen,
        "stuck_count": len(stuck_runs),
        "auto_crashed_count": len(crashed_runs),
        "crash_failed_count": len(crash_failed),
        "stuck_runs": stuck_runs,
        "elapsed_ms": elapsed_ms,
    }
    logger.info("[PREFECT_STUCK_FLOW] %s in %dms", summary["detail"], elapsed_ms)
    return summary


# ---------------------------------------------------------------------------
# Probe Protocol adapter — for the registry-driven path.
# ---------------------------------------------------------------------------


class PrefectStuckFlowProbe:
    """Probe-Protocol-compatible wrapper around
    :func:`run_prefect_stuck_flow_probe`."""

    name: str = "prefect_stuck_flow"
    description: str = (
        "Detects Prefect content_generation flow runs stuck in state=RUNNING "
        "beyond app_settings.prefect_stuck_flow_threshold_minutes (default 30m). "
        "Pages on every stuck run and optionally auto-crashes when "
        "prefect_stuck_flow_auto_crash=true so subsequent dispatches resume "
        "without operator intervention."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_prefect_stuck_flow_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                k: summary[k]
                for k in (
                    "running_seen",
                    "stuck_count",
                    "auto_crashed_count",
                    "crash_failed_count",
                    "elapsed_ms",
                    "status",
                )
                if k in summary
            },
            severity=(
                "warning"
                if summary.get("stuck_count", 0) > 0
                else "info"
            ),
        )
