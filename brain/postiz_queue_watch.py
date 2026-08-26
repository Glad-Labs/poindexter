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
for unit tests, module-level cumulative retry counter, Probe-Protocol wrapper.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
    from secret_reader import read_app_setting
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.operator_notifier import notify_operator
    from brain.secret_reader import read_app_setting

logger = logging.getLogger("brain.postiz_queue_watch")

ENABLED_KEY = "postiz_queue_watch_enabled"
OVERDUE_MINUTES_KEY = "postiz_queue_overdue_minutes"
MAX_RETRIES_KEY = "postiz_queue_watch_max_retries"
RETRY_DELAY_KEY = "postiz_queue_watch_retry_delay_seconds"
API_URL_KEY = "postiz_api_url"
API_KEY_KEY = "postiz_api_key"

DEFAULT_ENABLED = True
# Our pipeline posts with type=now, so anything still QUEUE'd 30 minutes past
# its publishDate means the Temporal worker is not draining.
DEFAULT_OVERDUE_MINUTES = 30
DEFAULT_MAX_RETRIES = 2
# Postiz (Next.js app + Temporal worker + redis) takes noticeably longer than
# a thin sidecar to come back up — give it a generous window before re-check.
DEFAULT_RETRY_DELAY_SECONDS = 180
DEFAULT_API_URL = "http://postiz:3000"

_CONTAINER = "poindexter-postiz"
_ALERTNAME = "postiz_queue_wedged"
_DOCKER_RESTART_TIMEOUT_SECONDS = 60
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

# Module-level retry counter — persists across cycles so escalation fires
# cumulatively, exactly like auto_embed_watch's _retry_count.
_retry_count = 0


def _reset_retry_state() -> None:
    """Test helper — wipe the cross-cycle retry counter."""
    global _retry_count
    _retry_count = 0


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


def _restart_postiz_container(container: str) -> tuple[bool, str]:
    """``docker restart <container>`` (shape mirrors auto_embed_watch)."""
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": _DOCKER_RESTART_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(["docker", "restart", container], **kwargs)
        if result.returncode == 0:
            return True, f"Restarted {container}"
        return False, (
            f"docker restart {container} exit {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"docker restart {container} timed out"
    except Exception as exc:  # noqa: BLE001
        return False, f"docker restart error: {type(exc).__name__}: {str(exc)[:160]}"


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


async def _emit_alert(pool: Any, *, status: str, severity: str, detail: str) -> bool:
    """Insert a ``postiz_queue_wedged`` alert_events row (firing or resolved).

    Postiz is not Prometheus-scraped and the wedge is invisible to our DB
    (drafts read 'posted'), so this watch is the only alert source — it emits
    the firing row itself on escalate.
    """
    labels = {
        "source": "brain.postiz_queue_watch",
        "category": "social_distribution",
    }
    annotations = {
        "summary": (
            "Postiz queue wedged — accepted posts not publishing"
            if status == "firing"
            else "Postiz queue recovered after auto-restart"
        ),
        "description": detail,
    }
    fingerprint = f"postiz-queue-{status}-{int(time.time())}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, NOW(), $6)
            """,
            _ALERTNAME,
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
    restart_fn: Callable[[str], tuple[bool, str]] | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    notify_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Single cycle of the Postiz queue-wedge watch.

    Args:
        pool: asyncpg pool for app_settings + audit_log + alert_events.
        check_fn: ``() -> {"overdue": N, "sample": [...]} | None`` — defaults
            to the live Postiz API read. Tests inject canned states; None
            means "API unreachable" and is treated as wedged.
        restart_fn: ``(container) -> (ok, msg)`` — defaults to the real
            ``docker restart``. Offloaded via ``asyncio.to_thread`` so the
            blocking restart never stalls the brain event loop.
        sleep_fn: ``async (seconds) -> None`` — defaults to ``asyncio.sleep``
            so the between-retry wait yields the loop instead of freezing it.
        notify_fn: operator notifier — defaults to
            :func:`brain.operator_notifier.notify_operator`, used only for
            the "docker is unreachable" surface.
    """
    global _retry_count
    restart_fn = restart_fn or _restart_postiz_container
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
    check_fn = check_fn or (lambda: _overdue_queue_posts(pool, overdue_minutes))

    state = await check_fn()
    overdue = None if state is None else int(state.get("overdue", 0))

    # 1) Clean queue => happy path / auto-resolve.
    if overdue == 0:
        prev = _retry_count
        _retry_count = 0
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
        if not await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(pool, status="firing", severity="warning", detail=detail)
        await _emit_audit_event(
            pool,
            "probe.postiz_queue_escalate",
            detail,
            extra={"overdue": overdue, "retries_used": _retry_count},
        )
        return {
            "ok": False,
            "status": "escalated",
            "overdue": overdue,
            "retries_used": _retry_count,
        }

    # 2a) Retry via docker restart (the documented Temporal-wedge fix).
    _retry_count += 1
    logger.info(
        "[POSTIZ_WATCH] wedged (%s) — restart %d/%d on %s",
        stuck_detail, _retry_count, max_retries, _CONTAINER,
    )
    ok, msg = await asyncio.to_thread(restart_fn, _CONTAINER)
    if not ok:
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
        if "docker CLI" in msg:
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
        _retry_count = 0
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
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_postiz_queue_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("status", ""),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
