"""brain/corsair_feed_probe.py — freshness watchdog for the corsair_csv
(iCUE) sensor feed. Closes Glad-Labs/glad-labs-stack#868.

The ``tap.corsair_csv`` handler ingests iCUE LINK CSV exports into
``sensor_samples`` (source ``corsair_csv``), and that feed is the always-on
backup source for PSU wall power used by the electricity-cost calc (see
``brain/psu_power.py``). The tap is driven by the worker's hourly
``run_taps`` job, so a HEALTHY feed is at most ~60 min stale. A feed that
*stops* — iCUE crashed, the tap stalled, the sensor_logs path moved — used
to go completely unnoticed (it happened for real 2026-06-03: the tap froze
and was only caught by manual inspection).

This probe checks ``max(sampled_at)`` for ``source='corsair_csv'`` and, when
it exceeds ``app_settings.corsair_feed_stale_threshold_minutes`` (default
120 — comfortably past the hourly ingest cadence), emits a ``finding``
(``audit_log`` ``event_type='finding'``, ``severity='warning'``). The
worker's ``findings_alert_router`` then routes it to the operator (warning ->
Discord per ``feedback_telegram_vs_discord``), and the brain's
``alert_dispatcher`` dedups.

Design notes:
- **Transition-only emit.** A finding is written on the fresh->stale edge
  (state persisted in ``brain_knowledge``), so a multi-day stall pages once
  rather than every 5-min cycle. ``prev=None`` + stale still emits, so a
  feed that is already stale at brain boot is surfaced.
- **No data => not assessed.** Operators without the iCUE setup have zero
  ``corsair_csv`` rows; that is not an alert (no false alarms for them).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD_MIN = 120
_WATCHDOG_ENTITY = "corsair_feed_watchdog"


async def _threshold_minutes(pool) -> int:
    """Stale threshold (minutes) from app_settings, default 120."""
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings "
            "WHERE key = 'corsair_feed_stale_threshold_minutes'"
        )
        return int(val) if val else _DEFAULT_THRESHOLD_MIN
    except Exception:  # noqa: BLE001 — bad/missing row -> safe default
        return _DEFAULT_THRESHOLD_MIN


async def run_corsair_feed_probe(pool) -> dict:
    """Emit a finding when the iCUE corsair_csv feed goes stale.

    Returns a probe summary dict (``ok`` / ``detail`` / metrics) for the
    brain's ``run_cycle`` aggregation. ``ok=False`` when the feed is stale.
    """
    threshold = await _threshold_minutes(pool)

    try:
        row = await pool.fetchrow(
            """
            SELECT max(sampled_at) AS latest,
                   EXTRACT(EPOCH FROM (now() - max(sampled_at))) / 60.0 AS age_min
            FROM sensor_samples
            WHERE source = 'corsair_csv'
            """
        )
    except Exception as exc:  # noqa: BLE001 — probe must never crash the cycle
        logger.warning("[corsair_feed_probe] query failed: %s", exc)
        return {"ok": True, "detail": f"query failed: {exc}", "assessed": False}

    latest = row["latest"] if row else None
    if latest is None:
        # No corsair_csv data ever — the iCUE tap isn't configured for this
        # operator. Not an alert condition.
        return {
            "ok": True,
            "detail": "no corsair_csv samples (iCUE tap not configured)",
            "assessed": False,
        }

    age_min = float(row["age_min"])
    is_stale = age_min > threshold
    new_state = "stale" if is_stale else "fresh"

    prev = await pool.fetchrow(
        "SELECT value FROM brain_knowledge "
        "WHERE entity = $1 AND attribute = 'last_state'",
        _WATCHDOG_ENTITY,
    )
    prev_state = prev["value"] if prev else None

    # Page once per stale episode (the edge), not every cycle.
    if is_stale and prev_state != "stale":
        details = {
            "kind": "sensor_feed_stale",
            "title": (
                f"iCUE corsair_csv feed stale "
                f"({age_min:.0f}m old, threshold {threshold}m)"
            ),
            "body": (
                f"No fresh corsair_csv sample in {age_min:.0f} minutes "
                f"(last at {latest}; threshold {threshold}m). The iCUE LINK "
                f"sensor feed that backs PSU wall-power for electricity cost "
                f"has stopped ingesting. Check that iCUE LINK is running and "
                f"writing to the sensor_logs directory, and that the worker's "
                f"run_taps job is firing the corsair_csv tap."
            ),
            "dedup_key": "corsair_csv_feed_stale",
        }
        try:
            await pool.execute(
                "INSERT INTO audit_log (event_type, source, details, severity) "
                "VALUES ('finding', 'corsair_feed_probe', $1::jsonb, 'warning')",
                json.dumps(details),
            )
            logger.warning(
                "[corsair_feed_probe] feed STALE (%.0fm > %dm) — finding emitted",
                age_min, threshold,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[corsair_feed_probe] finding insert failed: %s", exc)
    elif not is_stale and prev_state == "stale":
        logger.info("[corsair_feed_probe] feed recovered (%.0fm old)", age_min)

    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ($1, 'last_state', $2, 1.0, 'corsair_feed_probe')
            ON CONFLICT (entity, attribute)
              DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            _WATCHDOG_ENTITY, new_state,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[corsair_feed_probe] state write failed: %s", exc)

    return {
        "ok": not is_stale,
        "detail": (
            f"corsair_csv feed {age_min:.0f}m old "
            f"(threshold {threshold}m) — {new_state}"
        ),
        "age_minutes": round(age_min, 1),
        "threshold_minutes": threshold,
        "stale": is_stale,
    }
