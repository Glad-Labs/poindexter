"""Stamp app_settings.last_read_at for the settings a process has read.

The flush half of read-telemetry (Glad-Labs/poindexter#756). A process records
every key it asks for in two in-memory buffers: its ``SiteConfig`` instance's
read set (``SiteConfig.get`` / ``require``) and the process-wide
``services.settings_read_sink`` (``SettingsService.get``,
``DatabaseService.get_setting_value`` and the raw-SQL helpers).
:func:`flush_read_telemetry` drains both and batch-stamps
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
- **Each ``poindexter`` CLI command.** ``cli._bootstrap.close_cli_pool``
  flushes before it closes the command's pool, and ``container_for_cli``
  flushes its container's ``SiteConfig``. A command's other SiteConfigs come
  from ``cli_site_config``, which records into the process-wide sink.
- **Each auto-embed pass.** ``services.taps.runner.run_all`` flushes the
  ``SiteConfig`` it builds.
- **Every script and voice agent** that loads a ``SiteConfig`` flushes it
  before its pool closes.

``scripts/ci/settings_read_flush_lint.py`` holds this list to account: every
place that builds a DB-loaded ``SiteConfig`` has to flush it, or carry a
reason in that lint's allowlist for why it doesn't. The one process that
doesn't flush is the MCP server, a long-lived adapter with no teardown. The
brain daemon reads ``app_settings`` over its own asyncpg connections and never
records anything.

A read counts only when code asks for a key it names. The settings-admin
surfaces fetch a key because a person or an agent named it, and they don't
record: ``GET/POST/PUT /api/settings/{key}`` (``AdminDatabase.get_setting``),
the MCP ``get_setting`` tool and the console chat's ``get_setting`` tool
(``SiteConfig.peek``). Until 2026-09-28 they did, so ``poindexter settings get
<key>`` stamped the key it looked at, and the zero-reader probe, which lists
only never-stamped keys, never reported it again.

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

# The two controls above, for a flush from a process that has no SiteConfig.
_CONTROLS_SQL = "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])"


@dataclass(frozen=True)
class ReadTelemetryFlush:
    """What one flush did. ``ok`` and ``detail`` map onto a ``JobResult``."""

    ok: bool
    detail: str
    keys_read: int = 0
    keys_stamped: int = 0


_NOTHING_READ = ReadTelemetryFlush(ok=True, detail="no keys read since the last flush")


def _discarded(keys_read: int) -> ReadTelemetryFlush:
    return ReadTelemetryFlush(
        ok=True,
        detail=f"telemetry disabled — discarded {keys_read} key(s)",
        keys_read=keys_read,
    )


def _affected_rows(status: Any) -> int:
    """Parse asyncpg's ``execute`` command tag (``"UPDATE 5"``) into a count.

    Degrades to 0 on anything unexpected. The count is for reporting only,
    never a control-flow decision."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0


async def _controls_from_db(conn: Any) -> Any:
    """The two telemetry controls, read from ``app_settings`` on ``conn``.

    Returned as a ``SiteConfig`` so a value parses exactly as it does in a
    process that has one, env-var fallback included."""
    from poindexter.services.site_config import SiteConfig

    rows = await conn.fetch(_CONTROLS_SQL, [_ENABLED_KEY, _RESTAMP_SECONDS_KEY])
    return SiteConfig(initial_config={r["key"]: r["value"] for r in rows if r["value"]})


async def flush_read_telemetry(pool: Any, site_config: Any = None) -> ReadTelemetryFlush:
    """Drain this process's settings-read buffers and stamp ``last_read_at``.

    ``site_config`` is the process's ``SiteConfig``: its read set is drained,
    and it supplies the ``settings_read_telemetry_*`` controls. Pass ``None``
    from a process that holds no SiteConfig of its own, as the CLI's
    ``close_cli_pool`` does: only the process-wide sink is drained, and the
    controls are read from ``app_settings`` on the connection that runs the
    UPDATE. Never raises: a failed query, or anything else, is logged and
    reported in the result, so telemetry cannot break the caller (a scheduler
    cycle, a content-flow run, a CLI command, an auto-embed pass). Callers
    can put it in a ``finally`` without it ever masking their own exception.
    """
    try:
        return await _flush(pool, site_config)
    except Exception as e:  # noqa: BLE001 — telemetry must never crash its caller
        logger.warning(
            "[settings_read_telemetry] flush failed: %s", describe_exception(e)
        )
        return ReadTelemetryFlush(ok=False, detail=f"flush failed: {describe_exception(e)}")


async def _flush(pool: Any, site_config: Any) -> ReadTelemetryFlush:
    if pool is None:
        # Don't drain. The keys stay buffered for a later flush once a pool
        # is available again.
        return ReadTelemetryFlush(ok=False, detail="no pool available")

    # Drain unconditionally, even when disabled, so neither buffer grows past
    # one flush's distinct reads. Union the SiteConfig instance's set with the
    # process-wide sink: SettingsService is built ad hoc with only a pool
    # (multi_model_qa, content_router, …), so its reads can't land on the
    # SiteConfig instance. Sorted so the UPDATE's key array is deterministic.
    own_reads = site_config.drain_read_keys() if site_config is not None else []
    keys = sorted(set(own_reads) | set(settings_read_sink.drain_read_keys()))

    restamp_seconds: int | None = None
    if site_config is not None:
        # These two reads happen after the drain, so they are stamped by the
        # next flush. A process that flushes once, as it ends, has no next
        # flush, which costs nothing: the worker reads both keys every minute.
        if not site_config.get_bool(_ENABLED_KEY, True):
            return _discarded(len(keys))
        if not keys:
            return _NOTHING_READ
        restamp_seconds = site_config.get_int(_RESTAMP_SECONDS_KEY, _DEFAULT_RESTAMP_SECONDS)
    elif not keys:
        # No DB round trip for a process that read nothing.
        return _NOTHING_READ

    try:
        async with pool.acquire() as conn:
            if restamp_seconds is None:
                controls = await _controls_from_db(conn)
                if not controls.get_bool(_ENABLED_KEY, True):
                    return _discarded(len(keys))
                restamp_seconds = controls.get_int(
                    _RESTAMP_SECONDS_KEY, _DEFAULT_RESTAMP_SECONDS
                )
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
