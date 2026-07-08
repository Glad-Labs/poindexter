"""brain/clock_skew_probe.py — DB wall-clock skew detector (2026-07-08).

Investigation 2026-07-08: ``poindexter-postgres-local`` returned ``now()``
~4h in the future during a window, self-resolving without a restart. Root
cause was a transient host-level **WSL2 ``CLOCK_REALTIME`` excursion**
(Docker Desktop runs on the WSL2 backend); Postgres reads live wall-clock
via ``gettimeofday()``, so a VM-wide jump lands directly in every
DB-written timestamp. The damage is silent — Grafana time-windows miss
future-stamped rows, and any Python-cutoff-vs-DB-timestamp comparison
breaks — and it corrupted a real-time correlation the day it happened.

**Why the obvious probe is worthless.** A probe running in a container in
the *same* WSL2 VM as Postgres shares the VM ``CLOCK_REALTIME``, so
comparing DB ``now()`` to the probe's own ``datetime.utcnow()`` is blind:
both move together during a jump (Δ≈0). The reference must come from
*outside* the VM wall-clock. This probe uses an **external-absolute**
reference — the HTTP ``Date:`` header from a configurable URL (RFC-9110
UTC, ~1s accuracy, ample against a 120s threshold) — compared to
``clock_timestamp()``. Crucially this does *not* false-page on ordinary
host sleep/resume (post-resume the clock is correct → matches the
reference → silent); it only fires when the DB clock is genuinely wrong.

Behavior every 5-min brain cycle (mirrors data_freshness_probe):

1. Disabled via ``clock_skew_probe_enabled=false`` → no-op.
2. Read ``clock_timestamp()`` epoch (the clock that corrupts data) and the
   external reference epoch. Either unreadable → status ``unknown``, NO
   finding (never page when we can't confirm).
3. ``skew = pg_epoch - reference_epoch``; append a ``clock_skew_samples``
   row for the Grafana time-series and self-prune old samples.
4. ``abs(skew) > clock_skew_threshold_seconds`` → skewed. Edge-triggered
   (state in ``brain_knowledge``, entity ``clock_skew_watchdog``): emit a
   ``db_clock_skew`` finding (severity ``clock_skew_severity``, default
   ``critical`` → Telegram via findings_alert_router) once per episode,
   re-paging only after ``clock_skew_renotify_minutes``. Recovery clears
   the edge.

Standalone — stdlib + ``httpx`` (already a brain dep; degrades to
``unknown`` if absent). The asyncpg pool is injected by the daemon;
``ref_fetcher`` / ``now_fn`` are injected in tests so skew is deterministic
without network.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import time as _time
from typing import Any

logger = logging.getLogger("brain.clock_skew_probe")

try:  # httpx is already a brain dependency (compose_drift_probe uses it).
    import httpx
except ImportError:  # pragma: no cover — degrade to "unknown"
    httpx = None  # type: ignore[assignment]

# --- Setting keys + in-code fallbacks (drift-guarded by test) --------------
ENABLED_SETTING_KEY = "clock_skew_probe_enabled"
REFERENCE_URL_SETTING_KEY = "clock_skew_reference_url"
THRESHOLD_SETTING_KEY = "clock_skew_threshold_seconds"
SEVERITY_SETTING_KEY = "clock_skew_severity"
RENOTIFY_SETTING_KEY = "clock_skew_renotify_minutes"
RETENTION_SETTING_KEY = "clock_skew_sample_retention_days"

DEFAULT_REFERENCE_URL = "https://www.cloudflare.com"
DEFAULT_THRESHOLD_SECONDS = 120
DEFAULT_SEVERITY = "critical"
DEFAULT_RENOTIFY_MINUTES = 60
DEFAULT_RETENTION_DAYS = 30

FINDING_KIND = "db_clock_skew"  # dot-free so findings.db_clock_skew.delivery attaches
STATE_ENTITY = "clock_skew_watchdog"

_REFERENCE_TIMEOUT_SECONDS = 10.0

RefFetcher = Callable[[str], Awaitable[float | None]]


# --- DB helpers (best-effort; the probe must never crash the cycle) --------


async def _read_setting(pool: Any, key: str, default: str = "") -> str:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] setting read %s failed: %s", key, exc)
        return default
    # '' is the app_settings "unset" sentinel — treat it as the default.
    return str(val) if val not in (None, "") else default


async def _read_int_setting(pool: Any, key: str, default: int) -> int:
    raw = (await _read_setting(pool, key, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[clock_skew] %s not an int (%r); using %d", key, raw, default)
        return default


async def _read_state(pool: Any, attribute: str) -> str | None:
    try:
        return await pool.fetchval(
            "SELECT value FROM brain_knowledge WHERE entity = $1 AND attribute = $2",
            STATE_ENTITY, attribute,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] state read %s failed: %s", attribute, exc)
        return None


async def _write_state(pool: Any, attribute: str, value: str) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, $2, $3, 1.0, 'clock_skew_probe')
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            STATE_ENTITY, attribute, value,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] state write %s failed: %s", attribute, exc)


async def _record_sample(
    pool: Any, skew_seconds: float | None, url: str, status: str,
) -> None:
    try:
        await pool.execute(
            "INSERT INTO clock_skew_samples (skew_seconds, reference_url, status) "
            "VALUES ($1, $2, $3)",
            skew_seconds, url, status,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] sample insert failed: %s", exc)


async def _prune_samples(pool: Any, retention_days: int) -> None:
    try:
        await pool.execute(
            "DELETE FROM clock_skew_samples "
            "WHERE sampled_at < now() - make_interval(days => $1)",
            retention_days,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] sample prune failed: %s", exc)


async def _emit_finding(
    pool: Any,
    *,
    severity: str,
    skew: float,
    threshold: int,
    url: str,
    pg_utc: str,
    ref_utc: str,
    pg_vs_container: float,
) -> None:
    details = {
        "kind": FINDING_KIND,
        "title": (
            f"DB clock skew: postgres is {skew:+.0f}s off real time "
            f"(threshold {threshold}s)"
        ),
        "body": (
            f"`clock_timestamp()` on poindexter-postgres-local reads "
            f"{pg_utc} while the external reference ({url}) reads {ref_utc} "
            f"— a skew of {skew:+.1f}s. Every DB-written timestamp is off by "
            f"this amount; Grafana time-windows can silently miss "
            f"future-stamped rows and Python-cutoff-vs-DB comparisons break. "
            f"Most likely a WSL2 clock excursion on host sleep/resume. "
            f"Remediation: see docs/operations/db-clock-skew.md "
            f"(`wsl --update` → force resync → `wsl --shutdown` last resort). "
            f"Probe-vs-container delta: {pg_vs_container:+.1f}s."
        ),
        "dedup_key": FINDING_KIND,
        "extra": {
            "skew_seconds": round(skew, 3),
            "threshold_seconds": threshold,
            "reference_url": url,
            "pg_utc": pg_utc,
            "ref_utc": ref_utc,
            "pg_vs_container_seconds": round(pg_vs_container, 3),
        },
    }
    try:
        import json

        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ('finding', 'clock_skew_probe', $1::jsonb, $2)",
            json.dumps(details), severity,
        )
        logger.warning(
            "[clock_skew] SKEW %.1fs > %ds vs %s — finding emitted (%s)",
            skew, threshold, url, severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] finding insert failed: %s", exc)


# --- External reference clock ----------------------------------------------


async def _fetch_reference_epoch(url: str) -> float | None:
    """External UTC epoch from an HTTP ``Date:`` header. None on any failure."""
    if httpx is None:
        logger.warning("[clock_skew] httpx unavailable — cannot read reference")
        return None
    try:
        async with httpx.AsyncClient(
            timeout=_REFERENCE_TIMEOUT_SECONDS, follow_redirects=True,
        ) as client:
            resp = await client.head(url)
        date_hdr = resp.headers.get("Date")
        if not date_hdr:
            logger.warning("[clock_skew] no Date header from %s", url)
            return None
        dt = parsedate_to_datetime(date_hdr)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] reference fetch from %s failed: %s", url, exc)
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


# --- Probe entry point ------------------------------------------------------


async def run_clock_skew_probe(
    pool: Any,
    *,
    ref_fetcher: RefFetcher | None = None,
    now_fn: Callable[[], float] = _time,
) -> dict[str, Any]:
    """One pass of the DB clock-skew probe. Never raises.

    Returns ``{ok, status, detail, skew_seconds, reference_url, ...}`` for
    the brain's ``run_cycle`` aggregation. ``status`` is one of ``disabled``
    / ``unknown`` / ``ok`` / ``skewed``; ``ok`` is False only on ``skewed``.
    """
    enabled = (await _read_setting(pool, ENABLED_SETTING_KEY, "true")).strip().lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "status": "disabled", "detail": "disabled",
                "skew_seconds": None, "reference_url": None}

    url = await _read_setting(pool, REFERENCE_URL_SETTING_KEY, DEFAULT_REFERENCE_URL)
    threshold = await _read_int_setting(pool, THRESHOLD_SETTING_KEY, DEFAULT_THRESHOLD_SECONDS)
    severity = await _read_setting(pool, SEVERITY_SETTING_KEY, DEFAULT_SEVERITY)
    renotify_minutes = await _read_int_setting(pool, RENOTIFY_SETTING_KEY, DEFAULT_RENOTIFY_MINUTES)
    retention_days = await _read_int_setting(pool, RETENTION_SETTING_KEY, DEFAULT_RETENTION_DAYS)

    # PG clock (the clock that corrupts data). Unreadable → can't assess.
    try:
        pg_raw = await pool.fetchval("SELECT extract(epoch from clock_timestamp())")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] pg clock read failed: %s", exc)
        pg_raw = None
    if pg_raw is None:
        return {"ok": True, "status": "unknown", "detail": "postgres clock unreadable",
                "skew_seconds": None, "reference_url": url}
    pg_epoch = float(pg_raw)

    # External reference. Unreachable → record the gap, but never page.
    fetcher = ref_fetcher or _fetch_reference_epoch
    try:
        ref_epoch = await fetcher(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clock_skew] reference fetcher raised: %s", exc)
        ref_epoch = None
    if ref_epoch is None:
        await _record_sample(pool, None, url, "unknown")
        await _prune_samples(pool, retention_days)
        return {"ok": True, "status": "unknown",
                "detail": f"reference clock unreadable ({url})",
                "skew_seconds": None, "reference_url": url}

    container_epoch = float(now_fn())
    skew = pg_epoch - float(ref_epoch)
    pg_vs_container = pg_epoch - container_epoch
    is_skewed = abs(skew) > threshold
    status = "skewed" if is_skewed else "ok"

    await _record_sample(pool, skew, url, status)
    await _prune_samples(pool, retention_days)

    prev_state = await _read_state(pool, "last_state")
    if is_skewed:
        last_notified_raw = await _read_state(pool, "last_notified_at")
        try:
            last_notified = float(last_notified_raw) if last_notified_raw else 0.0
        except (TypeError, ValueError):
            last_notified = 0.0
        should_notify = (
            prev_state != "skewed"
            or (container_epoch - last_notified) >= renotify_minutes * 60
        )
        if should_notify:
            await _emit_finding(
                pool, severity=severity, skew=skew, threshold=threshold, url=url,
                pg_utc=_iso(pg_epoch), ref_utc=_iso(ref_epoch),
                pg_vs_container=pg_vs_container,
            )
            await _write_state(pool, "last_notified_at", repr(container_epoch))
        if prev_state != "skewed":
            await _write_state(pool, "last_state", "skewed")
    else:
        if prev_state == "skewed":
            logger.info("[clock_skew] recovered — skew now %.1fs (< %ds)", skew, threshold)
        if prev_state != "ok":
            await _write_state(pool, "last_state", "ok")

    detail = (
        f"skew {skew:+.1f}s vs {url} "
        + ("SKEWED" if is_skewed else f"ok (< {threshold}s)")
    )
    return {
        "ok": not is_skewed,
        "status": status,
        "detail": detail,
        "skew_seconds": round(skew, 3),
        "reference_url": url,
        "pg_vs_container_seconds": round(pg_vs_container, 3),
    }
