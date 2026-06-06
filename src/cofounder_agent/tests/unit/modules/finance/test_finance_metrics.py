"""Unit tests for ``modules.finance.metrics`` (Glad-Labs/poindexter#565).

Pins the scrape-time refresh of the Mercury-poll Prometheus gauges against
``finance_poll_runs``:

- a fresh successful poll → timestamp gauge set, age small, per-status counts
- a stale successful poll → timestamp still set (the ALERT does the
  staleness math, not the exporter), age large
- Mercury disabled → all finance series CLEARED (absent), so a disabled
  integration never pages
- no successful poll yet → success/age series CLEARED so the alert's
  ``absent()`` arm fires instead of reading a 1970 timestamp
- DB error → series cleared, no exception escapes (so /metrics never 500s)

No real DB — a fake asyncpg pool synthesizes ``fetchval`` / ``fetch`` /
``fetchrow`` for the three queries the refresh runs.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from modules.finance import metrics as fin_metrics
from modules.finance.metrics import (
    FINANCE_LAST_POLL_AGE_SECONDS,
    FINANCE_LAST_POLL_SUCCESS_TIMESTAMP,
    FINANCE_POLL_RUNS_TOTAL,
    refresh_finance_metrics,
)

# A fixed "now" so age math is deterministic. 2026-06-03T00:00:00Z-ish.
_NOW = 1_780_000_000.0


class _FakeConn:
    def __init__(
        self,
        *,
        enabled: str | None,
        success_epoch: float | None,
        status_counts: dict[str, int] | None,
        raise_on: str | None = None,
    ):
        self._enabled = enabled
        self._success_epoch = success_epoch
        self._status_counts = status_counts or {}
        self._raise_on = raise_on

    async def fetchrow(self, query: str, *args):
        if self._raise_on and self._raise_on in query:
            raise RuntimeError("simulated DB error")
        if "mercury_enabled" in query:
            return None if self._enabled is None else {"value": self._enabled}
        return None

    async def fetchval(self, query: str, *args):
        if self._raise_on and self._raise_on in query:
            raise RuntimeError("simulated DB error")
        if "MAX(started_at)" in query:
            return self._success_epoch
        return None

    async def fetch(self, query: str, *args):
        if self._raise_on and self._raise_on in query:
            raise RuntimeError("simulated DB error")
        if "GROUP BY status" in query:
            return [{"status": k, "n": v} for k, v in self._status_counts.items()]
        return []


class _FakePool:
    def __init__(self, **conn_kwargs: Any):
        self._conn_kwargs = conn_kwargs

    def acquire(self):
        @asynccontextmanager
        async def _ctx():
            yield _FakeConn(**self._conn_kwargs)

        return _ctx()


def _gauge_value(gauge, **labels) -> float | None:
    """Read a prometheus_client gauge's current value, or None if the
    (labelled) series is absent from the registry."""
    if labels:
        # Labelled child: only present after .labels(...).set(...).
        child = gauge._metrics.get(tuple(labels[k] for k in gauge._labelnames))
        if child is None:
            return None
        return child._value.get()
    return gauge._value.get()


def _setup_clean():
    """Clear all finance series so each test starts from a known-absent
    state (the gauges are module-level singletons on the default REGISTRY)."""
    FINANCE_LAST_POLL_SUCCESS_TIMESTAMP.clear()
    FINANCE_LAST_POLL_AGE_SECONDS.clear()
    FINANCE_POLL_RUNS_TOTAL.clear()


@pytest.mark.unit
async def test_fresh_poll_sets_timestamp_and_counts():
    _setup_clean()
    success = _NOW - 1800.0  # 30 min ago — fresh
    pool = _FakePool(
        enabled="true",
        success_epoch=success,
        status_counts={"ok": 410, "auth_failed": 2, "running": 1},
    )

    await refresh_finance_metrics(pool, now=_NOW)

    ts = _gauge_value(FINANCE_LAST_POLL_SUCCESS_TIMESTAMP, source="finance_poll_runs")
    assert ts == pytest.approx(success)
    assert _gauge_value(FINANCE_LAST_POLL_AGE_SECONDS, source="finance_poll_runs") == pytest.approx(1800.0)
    assert _gauge_value(FINANCE_POLL_RUNS_TOTAL, status="ok") == 410
    assert _gauge_value(FINANCE_POLL_RUNS_TOTAL, status="auth_failed") == 2
    assert _gauge_value(FINANCE_POLL_RUNS_TOTAL, status="running") == 1


@pytest.mark.unit
async def test_stale_poll_still_emits_timestamp_with_large_age():
    """The exporter does NOT decide staleness — it emits the timestamp and
    the age; the Prometheus alert does ``time() - ts > threshold``. So a
    stale poll still publishes the (old) timestamp + a large age."""
    _setup_clean()
    success = _NOW - 6 * 3600.0  # 6h ago — well past the 3h default window
    pool = _FakePool(
        enabled="true",
        success_epoch=success,
        status_counts={"ok": 5},
    )

    await refresh_finance_metrics(pool, now=_NOW)

    ts = _gauge_value(FINANCE_LAST_POLL_SUCCESS_TIMESTAMP, source="finance_poll_runs")
    assert ts == pytest.approx(success)
    assert _gauge_value(FINANCE_LAST_POLL_AGE_SECONDS, source="finance_poll_runs") == pytest.approx(6 * 3600.0)


@pytest.mark.unit
async def test_mercury_disabled_clears_all_series():
    """A disabled integration must emit NOTHING so it never pages."""
    _setup_clean()
    # Pre-populate so we can prove the refresh actively clears them.
    FINANCE_LAST_POLL_SUCCESS_TIMESTAMP.labels(source="finance_poll_runs").set(123.0)
    FINANCE_LAST_POLL_AGE_SECONDS.labels(source="finance_poll_runs").set(1.0)
    FINANCE_POLL_RUNS_TOTAL.labels(status="ok").set(9)

    pool = _FakePool(enabled="false", success_epoch=_NOW, status_counts={"ok": 9})
    await refresh_finance_metrics(pool, now=_NOW)

    assert _gauge_value(
        FINANCE_LAST_POLL_SUCCESS_TIMESTAMP, source="finance_poll_runs"
    ) is None
    assert _gauge_value(FINANCE_LAST_POLL_AGE_SECONDS, source="finance_poll_runs") is None
    assert _gauge_value(FINANCE_POLL_RUNS_TOTAL, status="ok") is None


@pytest.mark.unit
async def test_no_successful_poll_clears_success_series_keeps_counts():
    """Enabled but never succeeded → success/age absent (so absent() fires)
    while the per-status run counts still publish (e.g. all auth_failed)."""
    _setup_clean()
    pool = _FakePool(
        enabled="true",
        success_epoch=None,
        status_counts={"auth_failed": 3},
    )

    await refresh_finance_metrics(pool, now=_NOW)

    assert _gauge_value(
        FINANCE_LAST_POLL_SUCCESS_TIMESTAMP, source="finance_poll_runs"
    ) is None
    assert _gauge_value(FINANCE_LAST_POLL_AGE_SECONDS, source="finance_poll_runs") is None
    assert _gauge_value(FINANCE_POLL_RUNS_TOTAL, status="auth_failed") == 3


@pytest.mark.unit
async def test_db_error_clears_series_and_never_raises():
    """A DB failure must not bubble out of refresh (would 500 /metrics) and
    must clear the series so the staleness alert's absent() arm can fire."""
    _setup_clean()
    FINANCE_LAST_POLL_SUCCESS_TIMESTAMP.labels(source="finance_poll_runs").set(55.0)

    pool = _FakePool(
        enabled="true",
        success_epoch=_NOW,
        status_counts={"ok": 1},
        raise_on="MAX(started_at)",
    )

    # Must not raise.
    await refresh_finance_metrics(pool, now=_NOW)

    assert _gauge_value(
        FINANCE_LAST_POLL_SUCCESS_TIMESTAMP, source="finance_poll_runs"
    ) is None


@pytest.mark.unit
async def test_default_now_is_walltime(monkeypatch):
    """When ``now`` is omitted the refresh uses ``time.time()`` — sanity-check
    the age comes out small for a just-now success."""
    _setup_clean()
    monkeypatch.setattr(fin_metrics.time, "time", lambda: _NOW)
    pool = _FakePool(
        enabled="true",
        success_epoch=_NOW - 10.0,
        status_counts={"ok": 1},
    )

    await refresh_finance_metrics(pool)  # no explicit now

    assert _gauge_value(FINANCE_LAST_POLL_AGE_SECONDS, source="finance_poll_runs") == pytest.approx(10.0)
