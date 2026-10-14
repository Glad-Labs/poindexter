"""Postiz queue-wedge watch (self-heal-before-paging for the social hub).

Postiz's internal Temporal queue is a known wedge point: a Temporal restart
inside the ``poindexter-postiz`` container leaves accepted posts stuck in
``QUEUE`` state forever — our ``social_post_drafts`` rows read ``posted``
(the API accepted the group), but nothing ever reaches X/Bluesky. The
documented fix is ``docker restart poindexter-postiz``; until this probe the
failure mode was "notice days later that nothing went out".

Detection is Postiz-side (the DB can't see it): each cycle queries
``GET {postiz_api_url}/public/v1/posts?startDate=...&endDate=...`` and counts
posts whose ``state`` is ``QUEUE`` with a ``publishDate`` more than
``postiz_queue_overdue_minutes`` in the past. An unreachable API while
``postiz_api_key`` IS configured counts as wedged too (container down /
hung) — same restart fixes both. Terminal ``ERROR`` posts (platform
rejections, e.g. an X 402 credits-depleted) are deliberately NOT wedge
signals — a restart can't heal them; the worker's
``SyncPostizDeliveryStateJob`` owns those (see ``_STUCK_STATES``).

Per cycle (mirrors ``brain/auto_embed_watch.py``):
1. ``postiz_api_key`` unset => ``unconfigured`` no-op (consumer installs and
   operators without the ``--profile postiz`` stack never see this probe act).
2. 0 overdue posts => happy path; auto-resolve any firing
   ``postiz_queue_wedged`` alert.
3. Overdue (or API unreachable) => ``docker restart poindexter-postiz``,
   wait ``postiz_queue_watch_retry_delay_seconds`` (Postiz + its Temporal
   worker take a while to boot), re-check. Clean => recovered.
4. After ``postiz_queue_watch_max_retries`` cumulative fail-then-retry
   cycles => escalate: emit a firing ``postiz_queue_wedged`` alert_events row
   (``warning`` — social distribution is delayed, not lost; posts fire when
   the queue drains — so this routes to Discord, not Telegram).

Design parity with auto_embed_watch: DB-configurable through ``app_settings``,
injectable ``check_fn`` / ``restart_fn`` / ``sleep_fn`` / ``notify_fn`` seams
for unit tests, cumulative retry counter, Probe-Protocol wrapper.

A backend that crashes at boot is not a wedge (poindexter#1093)
---------------------------------------------------------------
From 2026-09-27 15:39 to 09-28 14:23 UTC the Postiz backend failed
deterministically at boot, so the API never answered, and an unreachable API
counted as wedged. The watch restarted Postiz 8 times, re-arming two more
restarts on each of four brain restarts, and wrote an escalate audit row 261
times with the same generic "API unreachable". On postiz-app up to v2.23.0
every container start also spent 22 of ``mastra_ai_spans``' 1600 column slots
(#1091), so the restarts helped use the table up. pm2 restarts the crashed
``backend`` inside the container, so Docker's RestartCount stayed 0 and the
restart-loop watch could not see it either.

So an unreachable API is classified before anything restarts. pm2 is asked
for the ``backend`` process (``docker exec poindexter-postiz pm2 jlist``).
If it is ``errored``/``stopped``, or its pm2 restart count rose since the last
cycle, the backend is crash-looping at boot. A hung process does not restart
itself, and a deterministic boot failure cannot be restarted away. Nothing
is restarted; a ``postiz_backend_crashloop`` alert fires once, carrying the
tail of ``backend-error.log``. Otherwise (pm2 stable, or pm2 unreadable) the
watch restarts as before.

The retry budget and the backend's restart count live in ``brain_knowledge``
(entity ``probe.postiz_queue_watch``), so a brain restart no longer re-arms
restarts against a Postiz already known to be bad. The escalate and crash-loop
audit rows are written when the state changes and then at most every
``postiz_queue_watch_reminder_hours``, not every 5-minute cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from poindexter.brain import docker_utils
from poindexter.brain.operator_notifier import notify_operator
from poindexter.brain.secret_reader import read_app_setting

logger = logging.getLogger("brain.postiz_queue_watch")

ENABLED_KEY = "postiz_queue_watch_enabled"
OVERDUE_MINUTES_KEY = "postiz_queue_overdue_minutes"
MAX_RETRIES_KEY = "postiz_queue_watch_max_retries"
RETRY_DELAY_KEY = "postiz_queue_watch_retry_delay_seconds"
API_URL_KEY = "postiz_api_url"
API_KEY_KEY = "postiz_api_key"
REMINDER_HOURS_KEY = "postiz_queue_watch_reminder_hours"

DEFAULT_ENABLED = True
# Our pipeline posts with type=now, so anything still QUEUE'd 30 minutes past
# its publishDate means the Temporal worker is not draining.
DEFAULT_OVERDUE_MINUTES = 30
DEFAULT_MAX_RETRIES = 2
# Postiz (Next.js app + Temporal worker + redis) takes noticeably longer than
# a thin sidecar to come back up — give it a generous window before re-check.
DEFAULT_RETRY_DELAY_SECONDS = 180
DEFAULT_API_URL = "http://postiz:3000"
# Hours between repeat audit rows while the same escalation or crash loop
# persists (0 = state changes only).
DEFAULT_REMINDER_HOURS = 1

_CONTAINER = "poindexter-postiz"
_ALERTNAME = "postiz_queue_wedged"
_CRASHLOOP_ALERTNAME = "postiz_backend_crashloop"
# pm2 inside the container: the app that serves the public API, and its log.
_BACKEND_APP = "backend"
_BACKEND_ERROR_LOG = "/root/.pm2/logs/backend-error.log"
_BACKEND_LOG_TAIL_LINES = 25
_DOCKER_EXEC_TIMEOUT_SECONDS = 20
_CRASHED_PM2_STATES = frozenset({"errored", "stopped"})
# brain_knowledge rows that outlive a brain restart.
_STATE_ENTITY = "probe.postiz_queue_watch"
_STATE_SOURCE = "postiz_queue_watch"
_API_TIMEOUT_SECONDS = 15
# How far back to scan for stuck posts. A wedge older than this window has
# long since been paged; keeps the response small.
_SCAN_WINDOW_DAYS = 7
PROBE_INTERVAL_SECONDS = 300

# Postiz post states that mean "accepted but not out the door" AND that a
# container restart can heal. That is QUEUE only: ERROR is a TERMINAL
# platform rejection (Postiz records a nonRetryable ApplicationFailure — the
# post never leaves ERROR no matter how many restarts), so counting it as
# "wedged" put the probe in a permanent escalate loop: five X 402
# credits-depleted rejections kept the postiz_queue_wedged alert firing from
# 2026-08-21 to 08-26, wrote an escalate audit row every 5 minutes, and
# bounced the Postiz container twice per brain restart — none of which could
# ever clear a 402. Terminal ERROR outcomes are owned by the worker's
# SyncPostizDeliveryStateJob (demotes the draft to failed + emits a
# social_post_delivery_failed finding).
_STUCK_STATES = frozenset({"QUEUE"})

# Retry counter — cumulative across cycles so escalation fires, like
# auto_embed_watch's _retry_count. Mirrored to brain_knowledge and read back
# on a new process's first cycle (poindexter#1093).
_retry_count = 0
_state_loaded = False
_state: dict[str, str] = {}


def _reset_retry_state() -> None:
    """Test helper — wipe the cross-cycle retry counter and loaded state."""
    global _retry_count, _state_loaded
    _retry_count = 0
    _state_loaded = False
    _state.clear()


async def _load_state(pool: Any) -> None:
    """Read the persisted retry count and pm2 baseline once per process."""
    global _retry_count, _state_loaded
    if _state_loaded:
        return
    _state_loaded = True
    try:
        rows = await pool.fetch(
            "SELECT attribute, value FROM brain_knowledge WHERE entity = $1", _STATE_ENTITY,
        )
    except Exception as exc:  # noqa: BLE001 — a cold start is the old behaviour, not a failure
        logger.warning("[POSTIZ_WATCH] could not restore state (%s) — starting fresh", exc)
        return
    for row in rows or []:
        try:
            _state[str(row["attribute"])] = str(row["value"])
        except (KeyError, TypeError):
            continue
    try:
        _retry_count = max(0, int(_state.get("retry_count", "0")))
    except ValueError:
        _retry_count = 0


async def _save_state(pool: Any, attribute: str, value: Any) -> None:
    text = str(value)
    if _state.get(attribute) == text:
        return
    _state[attribute] = text
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, $2, $3, 1.0, $4)
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            _STATE_ENTITY, attribute, text, _STATE_SOURCE,
        )
    except Exception as exc:  # noqa: BLE001 — losing it costs only the restart protection
        logger.warning("[POSTIZ_WATCH] state write %s failed: %s", attribute, exc)


async def _set_retry_count(pool: Any, value: int) -> None:
    global _retry_count
    _retry_count = value
    await _save_state(pool, "retry_count", value)


def _reminder_due(attribute: str, reminder_hours: int) -> bool:
    """Whether an audit row named by ``attribute`` is due: never written, or older than the reminder."""
    raw = _state.get(attribute)
    if not raw:
        return True
    if reminder_hours <= 0:
        return False
    try:
        last = float(raw)
    except ValueError:
        return True
    return time.time() - last >= reminder_hours * 3600


# --- pm2 inside the container (poindexter#1093) -----------------------------


def _docker_exec(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["docker", "exec", _CONTAINER, *argv],
            capture_output=True, text=True, timeout=_DOCKER_EXEC_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("[POSTIZ_WATCH] docker exec %s failed: %s", argv[0], exc)
        return None


def _parse_pm2_backend(raw: str) -> dict[str, Any] | None:
    """``{"status", "restarts"}`` for pm2's ``backend`` app in ``pm2 jlist`` output."""
    try:
        apps = json.loads(raw)
    except (TypeError, ValueError):
        return None
    for app in apps if isinstance(apps, list) else []:
        if not isinstance(app, dict) or app.get("name") != _BACKEND_APP:
            continue
        raw_env = app.get("pm2_env")
        env: dict[str, Any] = raw_env if isinstance(raw_env, dict) else {}
        try:
            restarts = int(env.get("restart_time") or 0)
        except (TypeError, ValueError):
            restarts = 0
        return {"status": str(env.get("status") or ""), "restarts": restarts}
    return None


def _pm2_backend_state_blocking() -> dict[str, Any] | None:
    res = _docker_exec(["pm2", "jlist"])
    if res is None or res.returncode != 0:
        return None
    backend = _parse_pm2_backend(res.stdout)
    if backend is None:
        return None
    tail = _docker_exec(["tail", "-n", str(_BACKEND_LOG_TAIL_LINES), _BACKEND_ERROR_LOG])
    lines = [ln.rstrip() for ln in ((tail.stdout or "") if tail else "").splitlines() if ln.strip()]
    backend["error_tail"] = "\n".join(lines)[-2500:] or "(backend-error.log is empty)"
    return backend


async def _pm2_backend_state() -> dict[str, Any] | None:
    """pm2's view of the backend, or ``None`` when it can't be read. Never raises."""
    return await asyncio.to_thread(_pm2_backend_state_blocking)


# --- app_settings reads (same pattern as auto_embed_watch.py) ---------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POSTIZ_WATCH] read %s failed: %s — default %r", key, exc, default
        )
        return default
    return default if val is None else val


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


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": _coerce_bool(
            await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED
        ),
        "overdue_minutes": _coerce_int(
            await _read_setting(pool, OVERDUE_MINUTES_KEY, DEFAULT_OVERDUE_MINUTES),
            DEFAULT_OVERDUE_MINUTES,
        ),
        "max_retries": _coerce_int(
            await _read_setting(pool, MAX_RETRIES_KEY, DEFAULT_MAX_RETRIES),
            DEFAULT_MAX_RETRIES,
        ),
        "retry_delay_seconds": _coerce_int(
            await _read_setting(pool, RETRY_DELAY_KEY, DEFAULT_RETRY_DELAY_SECONDS),
            DEFAULT_RETRY_DELAY_SECONDS,
        ),
    }


def _parse_publish_date(raw: Any) -> datetime | None:
    """Parse Postiz's ISO publishDate (``2026-06-29T13:36:00.000Z``)."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _overdue_queue_posts(
    pool: Any, overdue_minutes: int
) -> dict[str, Any] | None:
    """Query Postiz for posts stuck past their publishDate.

    Returns ``{"overdue": N, "sample": [...]}`` on a successful API read, or
    ``None`` when the API is unreachable / errored — the caller treats None
    as wedged (the same restart heals a hung container). The endpoint
    requires ``startDate``/``endDate`` ISO params (verified against the live
    instance 2026-07-02) and returns ``{"posts": [{state, publishDate,
    integration: {providerIdentifier}}]}``.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx ships in the brain image
        logger.warning("[POSTIZ_WATCH] httpx unavailable — cannot query Postiz")
        return None

    base = (
        await _read_setting(pool, API_URL_KEY, DEFAULT_API_URL) or DEFAULT_API_URL
    ).rstrip("/")
    api_key = await read_app_setting(pool, API_KEY_KEY, "")
    if not api_key:
        # Callers gate on the key before invoking; belt-and-braces here.
        return {"overdue": 0, "sample": [], "unconfigured": True}

    now = datetime.now(UTC)
    params = {
        "startDate": (now - timedelta(days=_SCAN_WINDOW_DAYS)).isoformat(),
        "endDate": (now + timedelta(days=1)).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=_API_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{base}/public/v1/posts",
                headers={"Authorization": api_key},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 — any API failure = maybe-wedged
        logger.warning("[POSTIZ_WATCH] Postiz API read failed: %s", exc)
        return None

    posts = data.get("posts", []) if isinstance(data, dict) else []
    cutoff = now - timedelta(minutes=overdue_minutes)
    overdue = _overdue_from_posts(posts, cutoff)
    return {"overdue": len(overdue), "sample": overdue[:5]}


def _overdue_from_posts(
    posts: list[dict[str, Any]], cutoff: datetime
) -> list[dict[str, Any]]:
    """Filter a Postiz posts list down to restart-healable wedge candidates.

    Pure so the state-classification is unit-testable: only ``_STUCK_STATES``
    (QUEUE) past *cutoff* count — a terminal ``ERROR`` must never register as
    a wedge (see the ``_STUCK_STATES`` comment for the 2026-08-21..26
    escalate-loop that rule earned).
    """
    overdue: list[dict[str, Any]] = []
    for p in posts:
        state = (p.get("state") or "").upper()
        if state not in _STUCK_STATES:
            continue
        publish_at = _parse_publish_date(p.get("publishDate"))
        if publish_at is None or publish_at > cutoff:
            continue
        integration = p.get("integration") or {}
        overdue.append(
            {
                "id": (p.get("id") or "")[:12],
                "state": state,
                "publish_date": p.get("publishDate"),
                "platform": integration.get("providerIdentifier")
                or integration.get("name"),
            }
        )
    return overdue


async def _firing_alert_exists(pool: Any, alertname: str) -> bool:
    try:
        row = await pool.fetchrow(
            "SELECT status FROM alert_events WHERE alertname = $1"
            " ORDER BY id DESC LIMIT 1",
            alertname,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[POSTIZ_WATCH] alert lookup failed: %s", exc)
        return False
    return bool(row) and (row["status"] or "").lower() == "firing"


async def _emit_alert(
    pool: Any, *, status: str, severity: str, detail: str, alertname: str = _ALERTNAME,
) -> bool:
    """Insert a ``postiz_queue_wedged`` (or crash-loop) alert_events row.

    Postiz is not Prometheus-scraped and the wedge is invisible to our DB
    (drafts read 'posted'), so this watch is the only alert source — it emits
    the firing row itself on escalate.
    """
    labels = {
        "source": "brain.postiz_queue_watch",
        "category": "social_distribution",
    }
    if alertname == _CRASHLOOP_ALERTNAME:
        summary = (
            "Postiz backend crash-looping at boot — not restarting it"
            if status == "firing"
            else "Postiz backend up again"
        )
    else:
        summary = (
            "Postiz queue wedged — accepted posts not publishing"
            if status == "firing"
            else "Postiz queue recovered after auto-restart"
        )
    annotations = {"summary": summary, "description": detail}
    prefix = "postiz-crashloop" if alertname == _CRASHLOOP_ALERTNAME else "postiz-queue"
    fingerprint = f"{prefix}-{status}-{int(time.time())}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, NOW(), $6)
            """,
            alertname,
            severity,
            status,
            json.dumps(labels),
            json.dumps(annotations),
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[POSTIZ_WATCH] alert insert (%s) failed: %s", status, exc)
        return False


async def _emit_audit_event(
    pool: Any, event: str, detail: str, *, extra: dict[str, Any] | None = None
) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity)"
            " VALUES ($1,$2,$3::jsonb,$4)",
            event,
            "brain.postiz_queue_watch",
            json.dumps(payload),
            "warning" if "wedged" in event or "escalate" in event else "info",
        )
    except Exception as exc:  # noqa: BLE001
        # silent-ok: the audit_log timeline write is best-effort; the probe's
        # real work — restart + the firing alert_events row — already happened
        # via their own paths.
        logger.debug("[POSTIZ_WATCH] audit write failed: %s", exc)


async def run_postiz_queue_watch_probe(
    pool: Any,
    *,
    check_fn: Callable[[], Awaitable[dict[str, Any] | None]] | None = None,
    restart_fn: docker_utils.RestartFn | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    notify_fn: Callable[..., Any] | None = None,
    crash_check_fn: Callable[[], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Single cycle of the Postiz queue-wedge watch.

    Args:
        pool: asyncpg pool for app_settings + audit_log + alert_events.
        check_fn: ``() -> {"overdue": N, "sample": [...]} | None`` — defaults
            to the live Postiz API read. Tests inject canned states; None
            means "API unreachable" and is treated as wedged.
        restart_fn: ``async (container, *, pool) -> ContainerRestart`` —
            defaults to :func:`brain.docker_utils.restart_container`, the
            brain's shared ``docker restart`` (inspect first, timeout from
            ``app_settings.brain_docker_restart_timeout_seconds``, off the
            event loop).
        sleep_fn: ``async (seconds) -> None`` — defaults to ``asyncio.sleep``
            so the between-retry wait yields the loop instead of freezing it.
        notify_fn: operator notifier — defaults to
            :func:`brain.operator_notifier.notify_operator`, used only for
            the "docker is unreachable" surface.
        crash_check_fn: ``() -> {"status", "restarts", "error_tail"} | None``
            — pm2's view of the backend, asked only when the API is
            unreachable. Defaults to ``docker exec poindexter-postiz pm2
            jlist``. None means pm2 couldn't be read: today's restart path.
    """
    restart_fn = restart_fn or docker_utils.restart_container
    crash_check_fn = crash_check_fn or _pm2_backend_state
    sleep_fn = sleep_fn or asyncio.sleep
    _notify: Callable[..., Any] = notify_fn or notify_operator

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "detail": f"{ENABLED_KEY}=false"}

    # Gate on the API key BEFORE any network call: installs without the
    # opt-in `--profile postiz` stack must never see a restart or a page.
    api_key = await read_app_setting(pool, API_KEY_KEY, "")
    if not api_key:
        return {
            "ok": True,
            "status": "unconfigured",
            "detail": f"{API_KEY_KEY} unset — Postiz not in use",
        }

    overdue_minutes = int(config["overdue_minutes"])
    max_retries = int(config["max_retries"])
    retry_delay = float(config["retry_delay_seconds"])
    reminder_hours = max(0, _coerce_int(
        await _read_setting(pool, REMINDER_HOURS_KEY, DEFAULT_REMINDER_HOURS),
        DEFAULT_REMINDER_HOURS,
    ))
    check_fn = check_fn or (lambda: _overdue_queue_posts(pool, overdue_minutes))
    await _load_state(pool)

    state = await check_fn()
    overdue = None if state is None else int(state.get("overdue", 0))

    # The API answered: any crash loop is over.
    if state is not None and await _firing_alert_exists(pool, _CRASHLOOP_ALERTNAME):
        await _emit_alert(
            pool, status="resolved", severity="info", alertname=_CRASHLOOP_ALERTNAME,
            detail="The Postiz API answers again.",
        )
        await _emit_audit_event(pool, "probe.postiz_backend_recovered", "Postiz API answers again")
        await _save_state(pool, "crashloop_audit_at", "")

    # 0) Unreachable: crash-looping at boot, or hung? Only a hung one is
    # restarted (poindexter#1093).
    if state is None:
        crash = await crash_check_fn()
        if crash is None:
            logger.warning(
                "[POSTIZ_WATCH] API unreachable and pm2 could not be read — "
                "treating it as hung, as before"
            )
        else:
            prev_raw = _state.get("backend_restarts", "")
            await _save_state(pool, "backend_restarts", int(crash.get("restarts") or 0))
            rising = prev_raw.isdigit() and int(crash.get("restarts") or 0) > int(prev_raw)
            if str(crash.get("status") or "") in _CRASHED_PM2_STATES or rising:
                return await _report_crash_loop(pool, crash, rising=rising, reminder_hours=reminder_hours)

    # 1) Clean queue => happy path / auto-resolve.
    if overdue == 0:
        prev = _retry_count
        await _set_retry_count(pool, 0)
        status = "clean"
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(
                pool,
                status="resolved",
                severity="info",
                detail=(
                    f"Postiz queue clean again after {prev} watch retry "
                    f"attempt(s)."
                ),
            )
            await _emit_audit_event(
                pool, "probe.postiz_queue_resolved", "queue clean again",
            )
            status = "auto_resolved"
        return {"ok": True, "status": status, "overdue": 0, "retries_used": prev}

    stuck_detail = (
        "API unreachable (container down/hung?)"
        if state is None
        else f"{overdue} post(s) stuck past publishDate+{overdue_minutes}m: "
        f"{json.dumps(state.get('sample', []))[:400]}"
    )

    # 2) Wedged — escalate if we've burned the retry budget.
    if _retry_count >= max_retries:
        detail = (
            f"Postiz queue wedged after {_retry_count} retry attempt(s) — "
            f"{stuck_detail} Escalating."
        )
        logger.warning("[POSTIZ_WATCH] %s", detail)
        newly_firing = not await _firing_alert_exists(pool, _ALERTNAME)
        if newly_firing:
            await _emit_alert(pool, status="firing", severity="warning", detail=detail)
        # One audit row when the escalation starts, then one per reminder,
        # not one per 5-minute cycle (261 in one incident, poindexter#1093).
        if newly_firing or _reminder_due("escalate_audit_at", reminder_hours):
            await _emit_audit_event(
                pool,
                "probe.postiz_queue_escalate",
                detail,
                extra={"overdue": overdue, "retries_used": _retry_count},
            )
            await _save_state(pool, "escalate_audit_at", f"{time.time():.0f}")
        return {
            "ok": False,
            "status": "escalated",
            "overdue": overdue,
            "retries_used": _retry_count,
        }

    # 2a) Retry via docker restart (the documented Temporal-wedge fix).
    await _set_retry_count(pool, _retry_count + 1)
    logger.info(
        "[POSTIZ_WATCH] wedged (%s) — restart %d/%d on %s",
        stuck_detail, _retry_count, max_retries, _CONTAINER,
    )
    restart = await restart_fn(_CONTAINER, pool=pool)
    if restart.status == docker_utils.RESTART_MISSING:
        # Mid-recreate (``docker compose up --force-recreate`` leaves the name
        # unbound for a second or two): nothing was restarted, so skip the
        # wait and re-check; the next cycle queries the replacement. The retry
        # still counts, so a Postiz that stays missing (an unreachable API
        # counts as wedged) reaches the escalation above.
        detail = (
            f"Postiz queue wedged but {restart.detail}; nothing was restarted "
            f"(retry {_retry_count}/{max_retries})."
        )
        logger.info("[POSTIZ_WATCH] %s", detail)
        await _emit_audit_event(
            pool,
            "probe.postiz_queue_restart_skipped",
            detail,
            extra={"retries_used": _retry_count},
        )
        return {"ok": False, "status": "container_missing", "retries_used": _retry_count}
    if restart.status == docker_utils.RESTART_RECENTLY_STARTED:
        # Something restarted Postiz moments ago (deploy-sync, compose, the
        # restart policy). An unreachable API counts as wedged above, so a
        # Postiz that is still starting up would be restarted mid-start.
        # Nothing was restarted here, so skip the wait and re-check; the next
        # cycle queries it again. The retry still counts, so a Postiz that
        # keeps restarting under us still reaches the escalation above.
        detail = (
            f"Postiz queue wedged but {restart.detail} "
            f"(retry {_retry_count}/{max_retries})."
        )
        logger.info("[POSTIZ_WATCH] %s", detail)
        await _emit_audit_event(
            pool,
            "probe.postiz_queue_restart_skipped",
            detail,
            extra={
                "retries_used": _retry_count,
                "restart_status": restart.status,
                "uptime_seconds": restart.uptime_seconds,
            },
        )
        return {
            "ok": False,
            "status": "container_recently_started",
            "retries_used": _retry_count,
        }
    if not restart.ok:
        msg = restart.detail
        detail = (
            f"Postiz queue wedged and docker restart failed: {msg} "
            f"(retry {_retry_count}/{max_retries})."
        )
        logger.warning("[POSTIZ_WATCH] %s", detail)
        await _emit_audit_event(
            pool,
            "probe.postiz_queue_restart_failed",
            detail,
            extra={"retries_used": _retry_count, "restart_error": msg},
        )
        if restart.status == docker_utils.RESTART_NO_DOCKER_CLI:
            try:
                _notify(
                    title="Postiz watch cannot restart container",
                    detail=detail,
                    source="brain.postiz_queue_watch",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[POSTIZ_WATCH] notify failed: %s", exc)
        return {"ok": False, "status": "restart_failed", "retries_used": _retry_count}

    # 2b) Wait + re-check.
    await sleep_fn(retry_delay)
    post_state = await check_fn()
    post_overdue = None if post_state is None else int(post_state.get("overdue", 0))
    if post_overdue == 0:
        await _set_retry_count(pool, 0)
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(
                pool,
                status="resolved",
                severity="info",
                detail="Postiz queue drained after auto-restart.",
            )
        await _emit_audit_event(
            pool, "probe.postiz_queue_recovered", "queue drained after restart",
        )
        return {
            "ok": True,
            "status": "recovered",
            "overdue": 0,
            "retries_used": _retry_count,
        }

    detail = (
        f"Postiz queue still wedged after restart "
        f"(overdue={post_overdue!r}). Used {_retry_count}/{max_retries}."
    )
    logger.warning("[POSTIZ_WATCH] %s", detail)
    await _emit_audit_event(
        pool,
        "probe.postiz_queue_retry_failed",
        detail,
        extra={"overdue": post_overdue, "retries_used": _retry_count},
    )
    return {
        "ok": False,
        "status": "retry_failed",
        "overdue": post_overdue,
        "retries_used": _retry_count,
    }


async def _report_crash_loop(
    pool: Any, crash: dict[str, Any], *, rising: bool, reminder_hours: int,
) -> dict[str, Any]:
    """Page a boot-time crash loop once, with its log tail, and restart nothing."""
    status = str(crash.get("status") or "?")
    restarts = int(crash.get("restarts") or 0)
    why = (
        f"pm2 has restarted it {restarts} time(s), and the count is still rising"
        if rising else f"pm2 reports it {status}"
    )
    detail = (
        f"The Postiz API is unreachable and its backend is crash-looping at boot ({why}). "
        f"A deterministic boot failure cannot be restarted away, so poindexter-postiz "
        f"was NOT restarted. Fix the cause below, then the API answers and this resolves.\n\n"
        f"Last lines of {_BACKEND_ERROR_LOG}:\n```\n{crash.get('error_tail') or '(none)'}\n```"
    )
    logger.warning("[POSTIZ_WATCH] backend crash loop (%s) — not restarting", why)
    newly_firing = not await _firing_alert_exists(pool, _CRASHLOOP_ALERTNAME)
    if newly_firing:
        await _emit_alert(
            pool, status="firing", severity="warning", alertname=_CRASHLOOP_ALERTNAME, detail=detail,
        )
    if newly_firing or _reminder_due("crashloop_audit_at", reminder_hours):
        await _emit_audit_event(
            pool, "probe.postiz_backend_crashloop", detail,
            extra={"pm2_status": status, "pm2_restarts": restarts},
        )
        await _save_state(pool, "crashloop_audit_at", f"{time.time():.0f}")
    return {
        "ok": False,
        "status": "crash_loop",
        "pm2_status": status,
        "pm2_restarts": restarts,
        "retries_used": _retry_count,
    }


class PostizQueueWatchProbe:
    """Probe-Protocol wrapper (mirrors AutoEmbedWatchProbe)."""

    name: str = "postiz_queue_watch"
    description: str = (
        "Watches Postiz for posts stuck in QUEUE past their publishDate "
        "(the Temporal-restart wedge); `docker restart`s poindexter-postiz "
        "before paging, and emits postiz_queue_wedged on escalate. Terminal "
        "ERROR posts belong to SyncPostizDeliveryStateJob, not this probe."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        from poindexter.brain.probe_interface import ProbeResult
        summary = await run_postiz_queue_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("status", ""),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
