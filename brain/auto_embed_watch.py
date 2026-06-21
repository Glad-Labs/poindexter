"""Auto-embed liveness watch (self-heal-before-paging for the embedder sidecar).

The ``auto-embed`` container is a thin runner over the Tap registry that runs
hourly (``sh -c 'while true; do python auto-embed.py; sleep 3600; done'``) and
populates the pgvector ``embeddings`` table. It had no liveness signal — a
wedged run (a hung Tap / a stuck Ollama call) or a dead container went
unnoticed until someone read ``auto-embed.log``. New content silently stops
being embedded, so RAG retrieval + semantic memory quietly go stale.

This probe is the sibling of ``brain/offsite_backup_watch.py``: its freshness
source is an ``audit_log`` heartbeat (``auto_embed_succeeded``) that
``scripts/auto-embed.py`` stamps at the end of every completed run — a
creds-free DB read, no Ollama/embedding access needed.

Per cycle:
1. Age of the newest ``auto_embed_succeeded`` event. Fresh
   (<= ``auto_embed_max_age_hours``) => happy path; auto-resolve any firing
   ``auto_embed_stale`` alert.
2. Stale => ``docker restart poindexter-auto-embed`` (kills a hung run and
   restarts the hourly loop), wait ``auto_embed_watch_retry_delay_seconds``,
   re-read. Fresh => recovered.
3. After ``auto_embed_watch_max_retries`` cumulative fail-then-retry cycles =>
   escalate: emit a firing ``auto_embed_stale`` alert_events row (``warning``
   — stale embeddings degrade search/memory but don't block the pipeline or
   risk data loss, so this routes to Discord, not Telegram) and stop kicking.
   The probe is the only alert source for a dead embedder, so it emits its own.

Design parity with offsite_backup_watch: standalone (stdlib + asyncpg only),
DB-configurable through ``app_settings``, injectable ``age_fn`` / ``restart_fn``
/ ``sleep_fn`` / ``notify_fn`` seams for unit tests, and a Probe-Protocol
wrapper for the registry.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.auto_embed_watch")

ENABLED_KEY = "auto_embed_watch_enabled"
MAX_AGE_HOURS_KEY = "auto_embed_max_age_hours"
MAX_RETRIES_KEY = "auto_embed_watch_max_retries"
RETRY_DELAY_KEY = "auto_embed_watch_retry_delay_seconds"

DEFAULT_ENABLED = True
# auto-embed runs hourly; 6h ≈ 6 missed cycles before paging — forgiving of a
# couple of slow/empty runs, but catches a genuinely dead/wedged embedder.
DEFAULT_MAX_AGE_HOURS = 6
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 120

_CONTAINER = "poindexter-auto-embed"
_ALERTNAME = "auto_embed_stale"
_HEARTBEAT_EVENT = "auto_embed_succeeded"
_DOCKER_RESTART_TIMEOUT_SECONDS = 30
PROBE_INTERVAL_SECONDS = 300

# Module-level retry counter — persists across cycles so escalation fires
# cumulatively, exactly like offsite_backup_watch's _retry_count.
_retry_count = 0


def _reset_retry_state() -> None:
    """Test helper — wipe the cross-cycle retry counter."""
    global _retry_count
    _retry_count = 0


# --- app_settings reads (same pattern as offsite_backup_watch.py) -----------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[AUTO_EMBED_WATCH] read %s failed: %s — default %r", key, exc, default
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
        "max_age_hours": _coerce_int(
            await _read_setting(pool, MAX_AGE_HOURS_KEY, DEFAULT_MAX_AGE_HOURS),
            DEFAULT_MAX_AGE_HOURS,
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


async def _seconds_since_heartbeat(pool: Any) -> float | None:
    """Age (seconds) of the newest auto_embed_succeeded event, or None.

    Creds-free: a plain audit_log read — the brain never runs Ollama or touches
    the embeddings table. scripts/auto-embed.py stamps the heartbeat at the end
    of each completed run.
    """
    try:
        val = await pool.fetchval(
            'SELECT EXTRACT(EPOCH FROM (now() - MAX("timestamp")))'
            " FROM audit_log WHERE event_type = $1",
            _HEARTBEAT_EVENT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AUTO_EMBED_WATCH] heartbeat read failed: %s", exc)
        return None
    return None if val is None else float(val)


def _restart_auto_embed_container(container: str) -> tuple[bool, str]:
    """``docker restart <container>`` (shape mirrors offsite_backup_watch)."""
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
        logger.warning("[AUTO_EMBED_WATCH] alert lookup failed: %s", exc)
        return False
    return bool(row) and (row["status"] or "").lower() == "firing"


async def _emit_alert(pool: Any, *, status: str, severity: str, detail: str) -> bool:
    """Insert an ``auto_embed_stale`` alert_events row (firing or resolved).

    The embedder is not Prometheus-scraped, so this watch is the only alert
    source for a dead/wedged runner — it emits the firing row itself on escalate.
    """
    labels = {
        "source": "brain.auto_embed_watch",
        "category": "embedding",
    }
    annotations = {
        "summary": (
            "Auto-embed stale — embedder not producing heartbeats"
            if status == "firing"
            else "Auto-embed recovered after auto-retry"
        ),
        "description": detail,
    }
    fingerprint = f"auto-embed-{status}-{int(time.time())}"
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
        logger.warning("[AUTO_EMBED_WATCH] alert insert (%s) failed: %s", status, exc)
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
            "brain.auto_embed_watch",
            json.dumps(payload),
            "warning" if "stale" in event or "escalate" in event else "info",
        )
    except Exception as exc:  # noqa: BLE001
        # silent-ok: the audit_log timeline write is best-effort (the table may
        # be absent on a very fresh install); the probe's real work — restart +
        # the firing alert_events row — already happened via their own paths.
        logger.debug("[AUTO_EMBED_WATCH] audit write failed: %s", exc)


async def run_auto_embed_watch_probe(
    pool: Any,
    *,
    age_fn: Callable[[], Awaitable[float | None]] | None = None,
    restart_fn: Callable[[str], tuple[bool, str]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    notify_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Single cycle of the auto-embed liveness watch.

    Args:
        pool: asyncpg pool for app_settings + audit_log + alert_events.
        age_fn: ``() -> age_seconds | None`` — defaults to the audit_log
            heartbeat read. Tests inject canned ages.
        restart_fn: ``(container) -> (ok, msg)`` — defaults to the real
            ``docker restart``.
        sleep_fn: ``(seconds) -> None`` — defaults to ``time.sleep``.
        notify_fn: operator notifier — defaults to
            :func:`brain.operator_notifier.notify_operator`, used only for the
            "docker is unreachable" surface.
    """
    global _retry_count
    age_fn = age_fn or (lambda: _seconds_since_heartbeat(pool))
    restart_fn = restart_fn or _restart_auto_embed_container
    sleep_fn = sleep_fn or time.sleep
    _notify: Callable[..., Any] = notify_fn or notify_operator

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "detail": f"{ENABLED_KEY}=false"}

    max_age_seconds = float(config["max_age_hours"]) * 3600.0
    max_retries = int(config["max_retries"])
    retry_delay = float(config["retry_delay_seconds"])

    age = await age_fn()

    # 1) Fresh => happy path / auto-resolve.
    if age is not None and age <= max_age_seconds:
        prev = _retry_count
        _retry_count = 0
        status = "fresh"
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(
                pool,
                status="resolved",
                severity="info",
                detail=(
                    f"Auto-embed fresh again (age={age:.0f}s <= "
                    f"{max_age_seconds:.0f}s) after {prev} watch retry attempt(s)."
                ),
            )
            await _emit_audit_event(
                pool,
                "probe.auto_embed_resolved",
                f"fresh again (age={age:.0f}s)",
                extra={"age_seconds": age},
            )
            status = "auto_resolved"
        return {"ok": True, "status": status, "age_seconds": age, "retries_used": prev}

    # 2) Stale / missing — escalate if we've burned the retry budget.
    if _retry_count >= max_retries:
        detail = (
            f"Auto-embed stale after {_retry_count} retry attempt(s) "
            f"(age={age!r}s, threshold={max_age_seconds:.0f}s). Escalating."
        )
        logger.warning("[AUTO_EMBED_WATCH] %s", detail)
        if not await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(pool, status="firing", severity="warning", detail=detail)
        await _emit_audit_event(
            pool,
            "probe.auto_embed_escalate",
            detail,
            extra={"age_seconds": age, "retries_used": _retry_count},
        )
        return {
            "ok": False,
            "status": "escalated",
            "age_seconds": age,
            "retries_used": _retry_count,
        }

    # 2a) Retry via docker restart.
    _retry_count += 1
    logger.info(
        "[AUTO_EMBED_WATCH] stale (age=%s) — restart %d/%d on %s",
        f"{age:.0f}s" if age is not None else "missing",
        _retry_count,
        max_retries,
        _CONTAINER,
    )
    ok, msg = restart_fn(_CONTAINER)
    if not ok:
        detail = (
            f"Auto-embed stale and docker restart failed: {msg} "
            f"(retry {_retry_count}/{max_retries})."
        )
        logger.warning("[AUTO_EMBED_WATCH] %s", detail)
        await _emit_audit_event(
            pool,
            "probe.auto_embed_restart_failed",
            detail,
            extra={"retries_used": _retry_count, "restart_error": msg},
        )
        if "docker CLI" in msg:
            try:
                _notify(
                    title="Auto-embed watch cannot restart container",
                    detail=detail,
                    source="brain.auto_embed_watch",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[AUTO_EMBED_WATCH] notify failed: %s", exc)
        return {"ok": False, "status": "restart_failed", "retries_used": _retry_count}

    # 2b) Wait + re-read.
    sleep_fn(retry_delay)
    post_age = await age_fn()
    if post_age is not None and post_age <= max_age_seconds:
        _retry_count = 0
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(
                pool,
                status="resolved",
                severity="info",
                detail=f"Auto-embed recovered after restart (age={post_age:.0f}s).",
            )
        await _emit_audit_event(
            pool,
            "probe.auto_embed_recovered",
            f"fresh after restart (age={post_age:.0f}s)",
            extra={"age_seconds": post_age},
        )
        return {
            "ok": True,
            "status": "recovered",
            "age_seconds": post_age,
            "retries_used": _retry_count,
        }

    detail = (
        f"Auto-embed still stale after restart (age={post_age!r}s). "
        f"Used {_retry_count}/{max_retries}."
    )
    logger.warning("[AUTO_EMBED_WATCH] %s", detail)
    await _emit_audit_event(
        pool,
        "probe.auto_embed_retry_failed",
        detail,
        extra={"age_seconds": post_age, "retries_used": _retry_count},
    )
    return {
        "ok": False,
        "status": "retry_failed",
        "age_seconds": post_age,
        "retries_used": _retry_count,
    }


class AutoEmbedWatchProbe:
    """Probe-Protocol wrapper (mirrors OffsiteBackupWatchProbe)."""

    name: str = "auto_embed_watch"
    description: str = (
        "Watches the auto-embed sidecar's audit_log heartbeat; `docker "
        "restart`s the wedged embedder before paging, and emits "
        "auto_embed_stale on escalate."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_auto_embed_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("status", ""),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
