"""Stamp app_settings.last_read_at for the settings a process has read.

The flush half of read-telemetry (Glad-Labs/poindexter#756). A process records
every key it asks for in two in-memory buffers: its ``SiteConfig`` instance's
read set (``SiteConfig.get``) and the process-wide
``services.settings_read_sink`` (``SettingsService.get`` and the raw-SQL
helpers). :func:`flush_read_telemetry` drains both and batch-stamps
``app_settings.last_read_at``. The buffers live in process memory, so each
process that reads settings has to flush its own:

- **The worker.** ``FlushSettingsReadTelemetryJob`` flushes the lifespan
  ``SiteConfig`` once a minute.
- **Each Prefect content-flow run.** ``prefect worker start --type process``
  runs every flow run in a fresh subprocess, which builds its own
  ``SiteConfig`` and exits when the run ends. ``content_generation_flow``
  flushes that ``SiteConfig`` from its ``finally``, before the run's pool
  closes. Before 2026-09-28 nothing did, and every read the pipeline made
  died with its subprocess. ``content_flow_stale_inprogress_minutes`` is read
  by all ~700 flow runs a day and had never been stamped. The zero-reader
  probe was naming live QA weights (``qa_final_score_threshold``,
  ``qa_critic_weight``) as keys nothing reads.

A process that never calls this, such as the ``poindexter`` CLI, a one-off
script or the brain daemon, leaves its reads unstamped.

Write amplification: the UPDATE only touches rows whose ``last_read_at`` is
NULL or older than ``settings_read_telemetry_min_restamp_seconds`` (default
1h). A hot key is written about once an hour however many processes read it,
and most flushes are a cheap, mostly no-op UPDATE on a ~2k-row table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from poindexter.services import settings_read_sink
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "settings_read_telemetry_enabled"
_RESTAMP_SECONDS_KEY = "settings_read_telemetry_min_restamp_seconds"
_DEFAULT_RESTAMP_SECONDS = 3600

_STAMP_SQL = """
    UPDATE app_settings
    SET last_read_at = NOW()
    WHERE key = ANY($1::text[])
      AND (
        last_read_at IS NULL
        OR last_read_at < NOW() - ($2 * INTERVAL '1 second')
      )
"""


@dataclass(frozen=True)
class ReadTelemetryFlush:
    """What one flush did. ``ok`` and ``detail`` map onto a ``JobResult``."""

    ok: bool
    detail: str
    keys_read: int = 0
    keys_stamped: int = 0


def _affected_rows(status: Any) -> int:
    """Parse asyncpg's ``execute`` command tag (``"UPDATE 5"``) into a count.

    Degrades to 0 on anything unexpected. The count is for reporting only,
    never a control-flow decision."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


async def flush_read_telemetry(pool: Any, site_config: Any) -> ReadTelemetryFlush:
    """Drain this process's settings-read buffers and stamp ``last_read_at``.

    ``site_config`` is the process's ``SiteConfig``: its read set is drained,
    and it supplies the ``settings_read_telemetry_*`` controls. A failed
    UPDATE is logged and reported in the result, never raised, so telemetry
    cannot break the caller (a scheduler cycle, or a content-flow run).
    """
    if pool is None:
        # Don't drain. The keys stay buffered for a later flush once a pool
        # is available again.
        return ReadTelemetryFlush(ok=False, detail="no pool available")

    # Drain unconditionally, even when disabled, so neither buffer grows past
    # one flush's distinct reads. Union the SiteConfig instance's set with the
    # process-wide sink: SettingsService is built ad hoc with only a pool
    # (multi_model_qa, content_router, …), so its reads can't land on the
    # SiteConfig instance. Sorted so the UPDATE's key array is deterministic.
    keys = sorted(set(site_config.drain_read_keys()) | set(settings_read_sink.drain_read_keys()))

    # These two reads happen after the drain, so they are stamped by the next
    # flush. A flow subprocess has no next flush, which costs nothing: the
    # worker reads both keys every minute.
    if not site_config.get_bool(_ENABLED_KEY, True):
        return ReadTelemetryFlush(
            ok=True,
            detail=f"telemetry disabled — discarded {len(keys)} key(s)",
            keys_read=len(keys),
        )

    if not keys:
        return ReadTelemetryFlush(ok=True, detail="no keys read since the last flush")

    restamp_seconds = site_config.get_int(_RESTAMP_SECONDS_KEY, _DEFAULT_RESTAMP_SECONDS)

    try:
        async with pool.acquire() as conn:
            status = await conn.execute(_STAMP_SQL, keys, restamp_seconds)
    except Exception as e:  # noqa: BLE001 — telemetry must never crash its caller
        logger.warning(
            "[settings_read_telemetry] last_read_at UPDATE failed: %s",
            describe_exception(e),
        )
        return ReadTelemetryFlush(
            ok=False,
            detail=f"update failed: {describe_exception(e)}",
            keys_read=len(keys),
        )

    stamped = _affected_rows(status)
    logger.debug("[settings_read_telemetry] stamped %d/%d key(s)", stamped, len(keys))
    return ReadTelemetryFlush(
        ok=True,
        detail=f"stamped {stamped}/{len(keys)} key(s) read since the last flush",
        keys_read=len(keys),
        keys_stamped=stamped,
    )


__all__ = ["ReadTelemetryFlush", "flush_read_telemetry"]
