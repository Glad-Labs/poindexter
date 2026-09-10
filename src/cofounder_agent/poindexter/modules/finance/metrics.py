"""Prometheus metrics for the Mercury finance poll (Glad-Labs/poindexter#565).

The hourly :class:`modules.finance.jobs.poll_mercury.PollMercuryJob` had
**zero** observability before this module: a stalled poll surfaced on no
dashboard and paged no one, and ``/api/finance/healthcheck`` returns 200
in every case (status in the body) so uptime monitors can't catch
``auth_failed`` / ``upstream_error`` either.

This file closes the metric half of #565 by mirroring the
``services/metrics_exporter.py`` pattern exactly: module-level
prometheus_client singletons plus a scrape-time refresh that reads the
``finance_poll_runs`` audit table. The substrate exporter discovers this
refresh through the generic ``Module.refresh_module_metrics`` hook (no
finance-specific import in public substrate code — see
``FinanceModule.refresh_module_metrics`` + ``metrics_exporter`` module
loop), so the whole surface stays inside the stripped ``modules/finance/``
boundary and never leaks to the public mirror.

## Metrics exposed

- ``poindexter_finance_last_poll_success_timestamp_seconds`` — gauge,
  Unix epoch of the most recent ``finance_poll_runs`` row with
  ``status='ok'``. This is the load-bearing staleness signal: the
  ``FinanceMercuryPollStale`` alert fires on
  ``time() - <gauge> > finance_poll_stale_seconds``. Deliberately
  CLEARED (absent from exposition) when Mercury is disabled, when there
  has never been a successful poll, or on DB error — same posture as the
  brain dead-man's-switch heartbeat, so ``absent()`` distinguishes
  "never succeeded / DB down" from a merely-stale timestamp.
- ``poindexter_finance_poll_runs_total`` — gauge, cumulative count of
  ``finance_poll_runs`` rows labeled by ``status`` (``ok`` / ``auth_failed``
  / ``api_error`` / ``exception`` / ``running``). A Gauge (not a Counter)
  because the value is re-read from the persisted table on each scrape, so
  it survives worker restarts — a raw Counter would reset to 0 every
  deploy and break ``increase()`` on short windows (same reasoning the
  exporter applies to ``poindexter_pipeline_auto_cancelled_total``).
- ``poindexter_finance_last_poll_age_seconds`` — gauge, seconds since the
  last successful poll (``time() - success_epoch``). Convenience series so
  the operator dashboard can chart staleness directly without a PromQL
  ``time()`` subtraction; the alert uses the timestamp gauge so it keeps
  working even if a scrape is skipped.

All values come from the same ``finance_poll_runs`` table the
``PollMercuryJob`` writes and the Grafana finance panels already read, so
the metric path stays consistent with the DB.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from prometheus_client import Gauge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric definitions (module-level singletons — Prometheus convention).
# Defined at import; the substrate exporter pulls this module in via the
# Module.refresh_module_metrics hook the first time /metrics is scraped, so
# the series register on the default REGISTRY without a finance import in
# public substrate code.
# ---------------------------------------------------------------------------

# The staleness signal. Cleared (absent) on disabled / never-succeeded /
# DB-error so the FinanceMercuryPollStale alert can use absent() to tell
# "no successful poll ever" apart from "last success is stale". Single
# constant label keeps the .clear() / absent() contract clean (a plain
# unlabeled Gauge would always emit a last/zero value and defeat absent()).
FINANCE_LAST_POLL_SUCCESS_TIMESTAMP = Gauge(
    "poindexter_finance_last_poll_success_timestamp_seconds",
    "Unix epoch of the most recent finance_poll_runs row with status='ok'. "
    "Absent when Mercury is disabled, no poll has ever succeeded, or the DB "
    "is unreachable — so absent() distinguishes that from a stale timestamp.",
    ["source"],
)

# Cumulative run counts by terminal status. Gauge (re-read from the
# persisted table each scrape) so it survives worker restarts.
FINANCE_POLL_RUNS_TOTAL = Gauge(
    "poindexter_finance_poll_runs_total",
    "Cumulative finance_poll_runs rows, labeled by status "
    "(ok / auth_failed / api_error / exception / running).",
    ["status"],
)

# Convenience: age in seconds since the last successful poll. The alert
# uses the timestamp gauge above (robust to a skipped scrape); this is for
# the operator dashboard so staleness is one panel, not a PromQL subtraction.
# Carries the same constant ``source`` label as the timestamp gauge so it can
# be CLEARED to absence — prometheus_client's ``.clear()`` only works on a
# gauge that declares labelnames (an unlabeled Gauge has no per-label child
# map to drop), and we want this series absent (not 0) under the same
# disabled / never-succeeded / DB-error conditions.
FINANCE_LAST_POLL_AGE_SECONDS = Gauge(
    "poindexter_finance_last_poll_age_seconds",
    "Seconds since the most recent successful Mercury poll "
    "(time() - last success epoch). Absent under the same conditions as "
    "poindexter_finance_last_poll_success_timestamp_seconds.",
    ["source"],
)


_TRUTHY = {"true", "1", "yes", "on"}


async def _mercury_enabled(conn: Any) -> bool:
    """Match ``PollMercuryJob._is_enabled`` — the poll is a no-op when
    ``mercury_enabled`` is falsey, so its metrics should be absent too
    (no stale-poll alert on a deliberately-disabled integration)."""
    row = await conn.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'mercury_enabled'"
    )
    value = row["value"] if row else None
    return (value or "").strip().lower() in _TRUTHY


def _clear_all() -> None:
    """Drop every finance series from the exposition.

    Called when Mercury is disabled or the DB is unreachable. Clearing
    (rather than emitting 0 / a stale value) lets the staleness alert's
    ``absent()`` clause fire and keeps a disabled integration off the
    dashboards entirely.
    """
    FINANCE_LAST_POLL_SUCCESS_TIMESTAMP.clear()
    FINANCE_LAST_POLL_AGE_SECONDS.clear()
    FINANCE_POLL_RUNS_TOTAL.clear()


async def refresh_finance_metrics(pool: Any, *, now: float | None = None) -> None:
    """Update the finance poll Gauges from ``finance_poll_runs``.

    Invoked at scrape time by the substrate exporter's module-metrics
    loop (``services/metrics_exporter.refresh_metrics`` →
    ``FinanceModule.refresh_module_metrics`` → here). Mirrors the
    exporter's defensive posture: every step is wrapped so one slow /
    failing query never makes ``/metrics`` error (Prometheus would then
    alert on the endpoint being down, which is not the intent).

    ``now`` is injectable for deterministic unit tests; defaults to
    wall-clock ``time.time()``.
    """
    now = time.time() if now is None else now

    # Gate on mercury_enabled — a disabled integration must not page on a
    # "stale poll". Clearing the series keeps it off the dashboard too.
    try:
        async with pool.acquire() as conn:
            if not await _mercury_enabled(conn):
                _clear_all()
                return

            # Last successful poll timestamp.
            success_epoch = await conn.fetchval(
                "SELECT EXTRACT(EPOCH FROM MAX(started_at)) "
                "FROM finance_poll_runs WHERE status = 'ok'"
            )

            # Cumulative counts by status — drives the per-status counter
            # and lets the operator chart failure vs. success run volume.
            status_rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM finance_poll_runs "
                "GROUP BY status"
            )
    except Exception as e:  # noqa: BLE001 — never fail /metrics
        logger.debug("refresh_finance_metrics: query failed: %s", e)
        # DB error → drop the series so the staleness alert's absent()
        # clause can fire rather than freezing on a stale last value.
        _clear_all()
        return

    # Per-status run counts. Reset the labelset first so a status that
    # stops appearing (e.g. no more 'running' rows) doesn't leave a stale
    # series at its last value.
    FINANCE_POLL_RUNS_TOTAL.clear()
    for r in status_rows:
        FINANCE_POLL_RUNS_TOTAL.labels(status=r["status"] or "unknown").set(
            int(r["n"] or 0)
        )

    if success_epoch is None:
        # Mercury is enabled but no poll has ever succeeded — let absent()
        # fire rather than emitting 0 (which would read as "succeeded at
        # the unix epoch", a 56-year-old success → permanent stale alert).
        FINANCE_LAST_POLL_SUCCESS_TIMESTAMP.clear()
        FINANCE_LAST_POLL_AGE_SECONDS.clear()
        return

    epoch = float(success_epoch)
    FINANCE_LAST_POLL_SUCCESS_TIMESTAMP.labels(source="finance_poll_runs").set(epoch)
    # Guard against clock skew making age negative.
    FINANCE_LAST_POLL_AGE_SECONDS.labels(source="finance_poll_runs").set(
        max(0.0, now - epoch)
    )


__all__ = [
    "FINANCE_LAST_POLL_SUCCESS_TIMESTAMP",
    "FINANCE_POLL_RUNS_TOTAL",
    "FINANCE_LAST_POLL_AGE_SECONDS",
    "refresh_finance_metrics",
]
