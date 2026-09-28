"""FlushSettingsReadTelemetryJob — stamp app_settings.last_read_at for read keys.

Second half of the read-telemetry mechanism (Glad-Labs/poindexter#756 item 2).
``SiteConfig.get`` records every key it is asked for into an in-memory set; this
job drains that set once a minute and batch-stamps ``app_settings.last_read_at``
so a key that is never read keeps a NULL stamp (an orphan candidate the
``ProbeZeroReaderSettingsJob`` later surfaces).

This job flushes the worker process only. The drain and the throttled UPDATE
live in ``services/settings_read_telemetry.py`` because every other process
that reads settings calls them too, as it finishes: each Prefect content-flow
run (from ``content_generation_flow``'s ``finally``), each ``poindexter`` CLI
command (``close_cli_pool``) and each auto-embed pass (``taps.runner.run_all``).
Their read buffers die with them otherwise.
``scripts/ci/settings_read_flush_lint.py`` keeps that list complete.

Why a separate job rather than folding into ``reload_site_config``: the
scheduler seeds the lifespan-bound ``SiteConfig`` into EVERY job's config at
``config["_site_config"]`` (``plugins/scheduler.py``), so this job drains the
SAME instance the request path reads — without entangling "refresh the value
cache" (reload) with "persist read telemetry" (this).

Write-amplification control: a naive "stamp every read key every minute" would
re-UPDATE hot keys 60×/hour. The UPDATE only touches rows whose ``last_read_at``
is NULL or older than ``settings_read_telemetry_min_restamp_seconds`` (default
1h), so a hot key is written at most ~once/hour and the per-minute statement is
a cheap, mostly-no-op HOT update on a ~2k-row table.
"""

from __future__ import annotations

from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.settings_read_telemetry import flush_read_telemetry


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
        # Drains the lifespan SiteConfig plus the process-wide SettingsService
        # read sink. With no pool it drains nothing, so the keys survive to the
        # next cycle.
        flushed = await flush_read_telemetry(pool, site_config)
        return JobResult(
            ok=flushed.ok, detail=flushed.detail, changes_made=flushed.keys_stamped
        )
