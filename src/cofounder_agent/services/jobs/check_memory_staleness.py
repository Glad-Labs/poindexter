"""CheckMemoryStalenessJob — alert when a pgvector writer goes silent.

Replaces ``IdleWorker._check_memory_staleness``. Runs every 30 minutes
by default. For each writer in ``MemoryClient.stats()['by_writer']``
compares age (now - newest) against a per-writer threshold
(``app_settings.memory_stale_threshold_seconds_<writer>``, global
fallback ``memory_stale_threshold_seconds``, default 6h).

When a writer is past threshold AND hasn't been alerted in the last
``memory_stale_alert_cooldown_seconds`` (default 6h), we:

1. Fire a ``memory_sync_stale`` audit_log event (visible on /pipeline
   + Grafana dashboards).
2. Send a Discord ops-channel notification via
   ``services.integrations.operator_notify.notify_operator``
   (which routes the ``discord_ops`` row through the outbound
   dispatcher framework).

Dedup state lives in ``app_settings.memory_stale_last_alerts`` as a
JSON blob: ``{writer_name: last_alert_iso_timestamp}``.

Config (``plugin.job.check_memory_staleness``):
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 1800)
- ``config.cooldown_seconds`` (default 21600) — per-writer alert cooldown
- ``config.default_threshold_seconds`` (default 21600) — global stale threshold
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


async def _get_setting(pool: Any, key: str, default: str) -> str:
    """Read an app_setting with fallback."""
    try:
        row = await pool.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
        if row and row["value"]:
            return str(row["value"])
    except Exception:
        pass
    return default


class CheckMemoryStalenessJob:
    name = "check_memory_staleness"
    description = "Alert when a pgvector memory writer stops syncing"
    schedule = "every 30 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        try:
            from poindexter.memory import MemoryClient
        except ImportError:
            return JobResult(ok=False, detail="poindexter.memory not available", changes_made=0)

        try:
            async with MemoryClient() as mem:
                stats = await mem.stats()
        except Exception as e:
            return JobResult(ok=False, detail=f"stats fetch failed: {e}", changes_made=0)

        if not stats or "by_writer" not in stats:
            return JobResult(ok=True, detail="no stats data", changes_made=0)

        now = datetime.now(timezone.utc)
        cooldown = int(config.get("cooldown_seconds", 21600))
        default_threshold = int(config.get("default_threshold_seconds", 21600))

        # Load per-writer alert-cooldown map from app_settings.
        raw_last = await _get_setting(pool, "memory_stale_last_alerts", "{}")
        try:
            last_alerts: dict[str, str] = json.loads(raw_last) if raw_last else {}
            if not isinstance(last_alerts, dict):
                last_alerts = {}
        except (json.JSONDecodeError, TypeError):
            last_alerts = {}

        # Global threshold can also be overridden from app_settings for
        # back-compat with the pre-migration settings.
        try:
            default_threshold = int(
                await _get_setting(
                    pool, "memory_stale_threshold_seconds", str(default_threshold),
                )
            )
        except (ValueError, TypeError):
            pass

        stale_writers: list[dict] = []
        alerts_fired: list[str] = []
        new_last_alerts = dict(last_alerts)

        for writer, data in stats["by_writer"].items():
            newest = data.get("newest")
            if newest is None:
                continue
            if hasattr(newest, "tzinfo") and newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            age_seconds = int((now - newest).total_seconds())

            # Per-writer threshold overrides the global one.
            per_key = f"memory_stale_threshold_seconds_{writer}"
            try:
                threshold = int(
                    await _get_setting(pool, per_key, str(default_threshold)),
                )
            except (ValueError, TypeError):
                threshold = default_threshold

            if age_seconds <= threshold:
                continue

            stale_writers.append({
                "writer": writer,
                "age_seconds": age_seconds,
                "threshold": threshold,
                "count": int(data.get("count") or 0),
                "newest": newest.isoformat(),
            })

            # Cooldown check.
            last_iso = new_last_alerts.get(writer)
            should_alert = True
            if last_iso:
                try:
                    last_dt = datetime.fromisoformat(last_iso)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if (now - last_dt).total_seconds() < cooldown:
                        should_alert = False
                except ValueError:
                    pass

            if not should_alert:
                continue

            # Audit event.
            try:
                from services.audit_log import audit_log_bg
                audit_log_bg(
                    "memory_sync_stale", "check_memory_staleness_job",
                    {
                        "writer": writer,
                        "age_seconds": age_seconds,
                        "threshold_seconds": threshold,
                        "count": int(data.get("count") or 0),
                        "newest": newest.isoformat(),
                    },
                    severity="warning",
                )
            except Exception as e:
                logger.debug("[MEMORY_STALE] audit event failed: %s", e)

            # Discord ops-channel notification.
            try:
                from services.integrations.operator_notify import notify_operator
                age_hours = age_seconds / 3600
                msg = (
                    f"[MEMORY STALE] writer `{writer}` hasn't been embedded in "
                    f"{age_hours:.1f}h (threshold {threshold // 3600}h). "
                    f"{data.get('count', 0)} rows in pgvector, newest={newest.isoformat()}. "
                    f"Check /memory dashboard."
                )
                await notify_operator(msg, critical=False)
                alerts_fired.append(writer)
            except Exception as e:
                logger.debug("[MEMORY_STALE] discord notify failed: %s", e)

            new_last_alerts[writer] = now.isoformat()

        # Persist updated cooldown state if anything changed.
        if new_last_alerts != last_alerts:
            try:
                await pool.execute(
                    "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                    "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()",
                    "memory_stale_last_alerts",
                    json.dumps(new_last_alerts),
                )
            except Exception as e:
                logger.debug("[MEMORY_STALE] state persist failed: %s", e)

        return JobResult(
            ok=True,
            detail=f"{len(stale_writers)} stale writer(s), {len(alerts_fired)} alert(s) fired",
            changes_made=len(alerts_fired),
            metrics={"stale": stale_writers},
        )
