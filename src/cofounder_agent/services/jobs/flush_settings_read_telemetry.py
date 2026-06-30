"""FlushSettingsReadTelemetryJob — stamp app_settings.last_read_at for read keys.

Second half of the read-telemetry mechanism (Glad-Labs/poindexter#756 item 2).
``SiteConfig.get`` records every key it is asked for into an in-memory set; this
job drains that set once a minute and batch-stamps ``app_settings.last_read_at``
so a key that is never read keeps a NULL stamp (an orphan candidate the
``ProbeZeroReaderSettingsJob`` later surfaces).

Why a separate job rather than folding into ``reload_site_config``: the
scheduler seeds the lifespan-bound ``SiteConfig`` into EVERY job's config at
``config["_site_config"]`` (``plugins/scheduler.py``), so this job drains the
SAME instance the request path reads — without entangling "refresh the value
cache" (reload) with "persist read telemetry" (this).

Write-amplification control: a naive "stamp every read key every minute" would
re-UPDATE hot keys 60×/hour. The UPDATE only touches rows whose ``last_read_at``
is NULL or older than ``settings_read_telemetry_min_restamp_seconds`` (default
1h), so a hot key is written at most ~once/hour and the per-minute statement is
a cheap, mostly-no-op HOT update on a ~1k-row table.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "settings_read_telemetry_enabled"
_RESTAMP_SECONDS_KEY = "settings_read_telemetry_min_restamp_seconds"
_DEFAULT_RESTAMP_SECONDS = 3600


def _affected_rows(status: Any) -> int:
    """Parse asyncpg's ``execute`` command tag (``"UPDATE 5"``) into a count.

    Degrades to 0 on anything unexpected — the count is for the JobResult /
    metrics only, never a control-flow decision."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


class FlushSettingsReadTelemetryJob:
    """Drain SiteConfig's read set and stamp app_settings.last_read_at."""

    name = "flush_settings_read_telemetry"
    description = (
        "Stamp app_settings.last_read_at for keys read since the last cycle "
        "(read-telemetry, poindexter#756)"
    )
    schedule = "every 1 minute"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False,
                detail="no site_config in config (job dispatcher seeding broken?)",
                changes_made=0,
            )
        if pool is None:
            # Don't drain — keep the read set intact so the keys survive to the
            # next cycle once a pool is available again.
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        # Drain unconditionally (even when disabled) so the in-memory set stays
        # bounded — it never grows past one cycle's distinct reads.
        keys = site_config.drain_read_keys()

        if not site_config.get_bool(_ENABLED_KEY, True):
            return JobResult(
                ok=True,
                detail=f"telemetry disabled — discarded {len(keys)} key(s)",
                changes_made=0,
            )

        if not keys:
            return JobResult(
                ok=True, detail="no keys read since last cycle", changes_made=0
            )

        restamp_seconds = site_config.get_int(
            _RESTAMP_SECONDS_KEY, _DEFAULT_RESTAMP_SECONDS
        )

        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    """
                    UPDATE app_settings
                    SET last_read_at = NOW()
                    WHERE key = ANY($1::text[])
                      AND (
                        last_read_at IS NULL
                        OR last_read_at < NOW() - ($2 * INTERVAL '1 second')
                      )
                    """,
                    keys,
                    restamp_seconds,
                )
        except Exception as e:  # noqa: BLE001 — telemetry must never crash a cycle
            logger.warning("[flush_settings_read_telemetry] UPDATE failed: %s", e)
            return JobResult(ok=False, detail=f"update failed: {e}", changes_made=0)

        updated = _affected_rows(status)
        logger.debug(
            "[flush_settings_read_telemetry] stamped %d/%d key(s)", updated, len(keys)
        )
        return JobResult(
            ok=True,
            detail=f"stamped {updated}/{len(keys)} key(s) read this cycle",
            changes_made=updated,
        )
