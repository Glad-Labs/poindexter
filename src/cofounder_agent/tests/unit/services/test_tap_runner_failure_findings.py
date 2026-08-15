"""Per-tap failure findings + deferral classification in ``tap_runner.run_all``.

Glad-Labs/poindexter#1015. Before this, an ``external_taps`` failure produced
exactly one signal: the scheduler's generic ``job_failure`` escalation, keyed
``job-fail:run_taps`` for all nine taps and every failure mode. In the 30 days
to 2026-08-15 that key fired 53 times, 43 of them ``internal_rag`` — so a
chronically failing tap owned the dedup fingerprint and anything else breaking
arrived behind it.

Two things are locked here:

1. **Per-tap identity.** Both the dispatcher's ``dedup_key`` and the router's
   cooldown key (``(kind, source)`` since poindexter#1010) must vary per tap,
   or the masking bug returns one level up.
2. **Deferral is not failure.** 16 of those 43 ``internal_rag`` "failures"
   were ``GpuBusyError`` / ``GpuLockTimeoutError`` — the GPU scheduler
   declining while the operator was gaming or the video pipeline held the
   lock. Those must not burn the failure streak, must not page, and must not
   flip the whole walk to failed.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import registry as registry_module
from services.integrations import tap_runner

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        return "UPDATE 1"

    async def fetchval(self, query, *args):
        self._pool.executes.append((query, args))
        return self._pool.next_streak

    async def fetch(self, query, *args):
        return self._pool.next_fetch


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self.next_fetch: list[dict[str, Any]] = []
        self.next_streak: int = 1

    def acquire(self):
        return _FakeConn(self)


class _FakeSiteConfig:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_int(self, key, default=0):
        return int(self._values.get(key, default))

    def get_bool(self, key, default=False):
        return bool(self._values.get(key, default))


class _GpuBusyError(RuntimeError):
    """Name-matched stand-in — the runner classifies on class NAME, not on
    an import, so the real gpu_admission module stays out of this test."""


_GpuBusyError.__name__ = "GpuBusyError"


class _GpuLockTimeoutError(TimeoutError):
    pass


_GpuLockTimeoutError.__name__ = "GpuLockTimeoutError"


async def _healthy_handler(payload, *, site_config, row, pool):
    return {"records": 3}


async def _broken_handler(payload, *, site_config, row, pool):
    raise ConnectionError("[Errno -5] No address associated with hostname")


async def _gpu_busy_handler(payload, *, site_config, row, pool):
    raise _GpuBusyError("GPU admission rejected: game_mode (holder ETA ~10738s)")


async def _gpu_lock_handler(payload, *, site_config, row, pool):
    raise _GpuLockTimeoutError("gpu.lock('ollama') timed out after 900s")


@pytest.fixture(autouse=True)
def _registry_isolation():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY["tap.healthy"] = _healthy_handler
    registry_module._REGISTRY["tap.broken"] = _broken_handler
    registry_module._REGISTRY["tap.gpu_busy"] = _gpu_busy_handler
    registry_module._REGISTRY["tap.gpu_lock"] = _gpu_lock_handler
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


@pytest.fixture
def emitted(monkeypatch):
    """Capture emit_finding calls at the module the runner imports it from."""
    calls: list[dict[str, Any]] = []

    def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("utils.findings.emit_finding", _capture)
    return calls


def _tap_row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "glad-labs_devto",
        "handler_name": "broken",
        "tap_type": "devto",
        "target_table": "topic_pool",
        "record_handler": None,
        "schedule": "every 30 minutes",
        "config": {},
        "state": {},
        "enabled": True,
        "metadata": {},
    }
    base.update(overrides)
    return base


class TestPerTapFindingIdentity:
    async def test_dedup_key_and_source_are_per_tap(self, emitted):
        """The whole point of #1015: two taps failing in one walk produce two
        distinguishable findings, not one collapsed fingerprint."""
        pool = _FakePool()
        pool.next_streak = 5  # sustained, so both route
        pool.next_fetch = [
            _tap_row(name="glad-labs_devto", handler_name="broken"),
            _tap_row(
                name="glad-labs_hackernews", handler_name="broken",
                id="00000000-0000-0000-0000-000000000002",
            ),
        ]

        await tap_runner.run_all(pool)

        assert len(emitted) == 2
        assert {f["dedup_key"] for f in emitted} == {
            "tap-fail:glad-labs_devto",
            "tap-fail:glad-labs_hackernews",
        }
        # Cooldowns key on (kind, source) since #1010 — so source must vary
        # too, or one tap's cooldown mutes the other's outage.
        assert {f["source"] for f in emitted} == {
            "tap.glad-labs_devto",
            "tap.glad-labs_hackernews",
        }
        assert {f["kind"] for f in emitted} == {"tap_failure"}

    async def test_error_text_rides_the_finding(self, emitted):
        pool = _FakePool()
        pool.next_streak = 2
        pool.next_fetch = [_tap_row()]

        await tap_runner.run_all(pool)

        assert "No address associated with hostname" in emitted[0]["body"]
        assert emitted[0]["extra"]["tap"] == "glad-labs_devto"
        assert emitted[0]["extra"]["consecutive_failures"] == 2


class TestStreakSeverityEscalation:
    async def test_first_failure_is_info_and_never_routes(self, emitted):
        """A lone blip records but does not page. ``info`` is below the
        router's fetch floor (warn/critical), so this is structural, not a
        policy the operator can accidentally lower into paging."""
        pool = _FakePool()
        pool.next_streak = 1
        pool.next_fetch = [_tap_row()]

        await tap_runner.run_all(pool)

        assert emitted[0]["severity"] == "info"

    async def test_second_consecutive_failure_routes(self, emitted):
        pool = _FakePool()
        pool.next_streak = 2
        pool.next_fetch = [_tap_row()]

        await tap_runner.run_all(pool)

        assert emitted[0]["severity"] == "warn"
        assert "2×" in emitted[0]["title"]

    async def test_threshold_is_db_tunable(self, emitted):
        """``tap_failure_alert_after_consecutive=1`` pages on contact."""
        pool = _FakePool()
        pool.next_streak = 1
        pool.next_fetch = [_tap_row()]

        await tap_runner.run_all(
            pool,
            site_config=_FakeSiteConfig(tap_failure_alert_after_consecutive=1),
        )

        assert emitted[0]["severity"] == "warn"

    async def test_master_switch_suppresses_emission(self, emitted):
        pool = _FakePool()
        pool.next_streak = 9
        pool.next_fetch = [_tap_row()]

        summary = await tap_runner.run_all(
            pool, site_config=_FakeSiteConfig(tap_failure_finding_enabled=False),
        )

        assert emitted == []
        # Still recorded as a failure — the switch gates the ALERT, not the
        # bookkeeping (feedback_no_silent_defaults).
        assert summary.total_failed == 1


class TestDeferralIsNotFailure:
    @pytest.mark.parametrize("handler", ["gpu_busy", "gpu_lock"])
    async def test_deferral_does_not_fail_or_page(self, emitted, handler):
        pool = _FakePool()
        pool.next_fetch = [_tap_row(handler_name=handler)]

        summary = await tap_runner.run_all(pool)

        assert summary.total_failed == 0
        assert summary.total_deferred == 1
        assert summary.taps[0].ok is True
        assert summary.taps[0].deferred is True
        assert emitted == []

    async def test_deferral_records_deferred_status_not_failed(self):
        """The row must say 'deferred' — the Grafana "Failing taps" stat
        counts 'failed' only, so a gaming session must not read as an
        outage on the Integrations board."""
        pool = _FakePool()
        pool.next_fetch = [_tap_row(handler_name="gpu_busy")]

        await tap_runner.run_all(pool)

        writes = [q for q, _ in pool.executes]
        assert any("last_run_status = 'deferred'" in q for q in writes)
        assert not any("last_run_status = 'failed'" in q for q in writes)

    async def test_deferral_leaves_the_failure_streak_alone(self):
        """A deferral must neither start nor advance a streak, or a long
        gaming session would escalate an unrelated tap to paging."""
        pool = _FakePool()
        pool.next_fetch = [_tap_row(handler_name="gpu_busy")]

        await tap_runner.run_all(pool)

        deferral_writes = [
            q for q, _ in pool.executes if "last_run_status = 'deferred'" in q
        ]
        assert len(deferral_writes) == 1
        assert "consecutive_failures" not in deferral_writes[0]

    async def test_deferral_does_not_flip_a_healthy_walk(self, emitted):
        """The 2026-08-15 shape: one tap declined, the rest fine. The walk
        must report success — folding a deferral into ok=False is what made
        `job_failure` claim an outage mid-collection."""
        pool = _FakePool()
        pool.next_fetch = [
            _tap_row(name="rag", handler_name="gpu_busy"),
            _tap_row(
                name="hn", handler_name="healthy",
                id="00000000-0000-0000-0000-000000000002",
            ),
        ]

        summary = await tap_runner.run_all(pool)

        assert summary.total_failed == 0
        assert summary.total_deferred == 1
        assert summary.total_records == 3
        assert emitted == []

    async def test_deferral_types_are_db_tunable(self, emitted):
        """Narrowing the CSV reclassifies a type back to a real failure."""
        pool = _FakePool()
        pool.next_streak = 3
        pool.next_fetch = [_tap_row(handler_name="gpu_lock")]

        summary = await tap_runner.run_all(
            pool,
            site_config=_FakeSiteConfig(
                tap_deferral_exception_types="GpuBusyError",
            ),
        )

        assert summary.total_deferred == 0
        assert summary.total_failed == 1
        assert len(emitted) == 1

    async def test_handler_timeout_subclass_is_not_misread_as_the_guard(self):
        """``GpuLockTimeoutError`` subclasses ``TimeoutError``, and since 3.11
        ``asyncio.TimeoutError`` IS ``TimeoutError`` — so the handler-timeout
        ``except`` clause (added by #3223) swallowed it and reported "handler
        exceeded 600s" for a lock wait that never reached 600s. That is both a
        false diagnosis and a bypass of deferral classification."""
        pool = _FakePool()
        pool.next_fetch = [_tap_row(handler_name="gpu_lock")]

        summary = await tap_runner.run_all(pool, handler_timeout_s=600)

        assert summary.total_deferred == 1
        assert "tap_handler_timeout_seconds" not in (summary.taps[0].error or "")
        assert "GpuLockTimeoutError" in (summary.taps[0].error or "")

    async def test_real_guard_timeout_still_reports_its_reason(self):
        """The other side of that split: our own wait_for firing must keep
        its explicit reason, since a bare TimeoutError stringifies to ''."""
        pool = _FakePool()
        pool.next_fetch = [_tap_row(handler_name="hang")]

        async def _hang(payload, *, site_config, row, pool):
            import asyncio as _a
            await _a.Event().wait()

        registry_module._REGISTRY["tap.hang"] = _hang
        summary = await tap_runner.run_all(pool, handler_timeout_s=0.01)

        assert summary.total_failed == 1
        assert "tap_handler_timeout_seconds" in (summary.taps[0].error or "")

    async def test_unrelated_exception_still_fails(self, emitted):
        """Deferral classification must not swallow ordinary breakage."""
        pool = _FakePool()
        pool.next_streak = 4
        pool.next_fetch = [_tap_row(handler_name="broken")]

        summary = await tap_runner.run_all(pool)

        assert summary.total_failed == 1
        assert summary.total_deferred == 0
        assert len(emitted) == 1


class TestSuccessResetsStreak:
    async def test_success_write_zeroes_the_counter(self):
        pool = _FakePool()
        pool.next_fetch = [_tap_row(handler_name="healthy")]

        await tap_runner.run_all(pool)

        success_writes = [
            q for q, _ in pool.executes if "last_run_status = 'success'" in q
        ]
        assert len(success_writes) == 1
        assert "consecutive_failures = 0" in success_writes[0]


class TestEmissionNeverBreaksTheWalk:
    async def test_finding_failure_is_swallowed(self, monkeypatch):
        """Observability must never take down ingest."""
        def _boom(**kwargs):
            raise RuntimeError("audit log down")

        monkeypatch.setattr("utils.findings.emit_finding", _boom)
        pool = _FakePool()
        pool.next_streak = 2
        pool.next_fetch = [
            _tap_row(handler_name="broken"),
            _tap_row(
                name="hn", handler_name="healthy",
                id="00000000-0000-0000-0000-000000000002",
            ),
        ]

        summary = await tap_runner.run_all(pool)

        assert summary.total_failed == 1
        assert summary.total_records == 3
