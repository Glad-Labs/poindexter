"""Failure counts and self-heal cooldowns survive a brain restart
(``poindexter/brain/probe_failure_state.py``).

``health_probes`` kept both in module-level dicts, empty in every new process.
In the 30 days to 2026-09-28 the brain restarted 91 times and 50 failure
streaks crossed a restart and started again from one, so "three failures in a
row" meant three since the last restart.

A restart here is a fresh ``ProbeFailureState`` and ``ProbeSchedule`` reading
the same table, which is what a new brain process has.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.brain import docker_utils as du
from poindexter.brain import health_probes as hp
from poindexter.brain import probe_failure_state as pfs
from poindexter.brain import probe_schedule
from poindexter.brain.probe_failure_state import ProbeFailureState
from poindexter.brain.probe_schedule import ProbeSchedule

COOLDOWN = hp.REMEDIATION_COOLDOWN


class _Pool:
    """``brain_knowledge`` for the failure state's and the schedule's statements, run for real.

    Every other query answers nothing. ``down`` makes every call raise, as a
    database that is unreachable does.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], tuple[str, str]] = {}
        self.writes: list[tuple[str, str, str]] = []
        self.down = False

    def seed(self, name: str, attribute: str, value: str) -> None:
        self.rows[(f"probe.{name}", attribute)] = (value, pfs.SOURCE)

    def value(self, name: str, attribute: str) -> str | None:
        row = self.rows.get((f"probe.{name}", attribute))
        return row[0] if row else None

    def count_writes(self) -> list[tuple[str, str]]:
        return [(entity, value) for entity, attr, value in self.writes if attr == pfs.FAILURES]

    def _check(self) -> None:
        if self.down:
            raise OSError("connection refused")

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self._check()
        if sql == pfs._LOAD_SQL:
            source, attributes = args
            return [
                {"entity": entity, "attribute": attr, "value": value}
                for (entity, attr), (value, src) in self.rows.items()
                if src == source and attr in attributes
            ]
        if sql == probe_schedule._LOAD_SQL:
            attribute, source = args
            return [
                {"entity": entity, "value": value}
                for (entity, attr), (value, src) in self.rows.items()
                if attr == attribute and src == source
            ]
        return []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._check()
        return None

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self._check()
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self._check()
        if sql in (pfs._UPSERT_SQL, probe_schedule._UPSERT_SQL):
            entity, attribute, value, source = args
            self.rows[(entity, attribute)] = (value, source)
            self.writes.append((entity, attribute, value))
        return "OK"


def _ago(**delta: float) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).isoformat()


def _restarted(container: str) -> du.ContainerRestart:
    """What ``docker_utils.restart_container`` returns for a restart that worked."""
    return du.ContainerRestart(container, du.RESTART_OK, f"restarted {container}", 90)


@pytest.fixture(autouse=True)
def restart(monkeypatch) -> Callable[[], None]:
    """Each test starts in a new brain process; calling this starts another."""

    def _restart() -> None:
        monkeypatch.setattr(pfs, "state", ProbeFailureState())
        monkeypatch.setattr(probe_schedule, "schedule", ProbeSchedule())

    _restart()
    return _restart


@pytest.fixture(autouse=True)
def _no_live_alertmanager(monkeypatch):
    """Unreachable, as on a developer host: covered probes are not suppressed."""
    monkeypatch.setattr(hp, "_alertmanager_healthy", AsyncMock(return_value=False))


async def _cycle(
    pool: _Pool, name: str, *, ok: bool = True, due: bool = True, notify=None, info=None,
) -> None:
    """One brain cycle in which ``name`` is the only probe. When it isn't
    ``due`` it doesn't run, and only its stored count can start a self-heal."""

    async def probe(_pool):
        return {"ok": ok, "detail": "fine" if ok else "down"}

    with (
        patch.dict(hp.PROBES, {name: probe}, clear=True),
        patch.object(hp, "_is_due", return_value=due),
    ):
        await hp.run_health_probes(pool, notify_fn=notify, info_fn=info)


@pytest.mark.unit
class TestProbeFailureState:
    async def test_a_new_process_continues_the_count_where_the_last_one_stopped(self):
        pool = _Pool()
        before = ProbeFailureState()
        await before.load(pool)
        await before.record_failure(pool, "disk_space")
        await before.record_failure(pool, "disk_space")

        after = ProbeFailureState()
        await after.load(pool)

        assert after.failures == {"disk_space": 2}
        assert await after.record_failure(pool, "disk_space") == 3

    async def test_a_pass_ends_the_streak_in_the_table_too(self):
        pool = _Pool()
        before = ProbeFailureState()
        await before.load(pool)
        for _ in range(4):
            await before.record_failure(pool, "disk_space")
        await before.record_success(pool, "disk_space")

        after = ProbeFailureState()
        await after.load(pool)

        assert pool.value("disk_space", pfs.FAILURES) == "0"
        assert after.failures == {"disk_space": 0}

    async def test_a_passing_probe_writes_nothing_once_its_row_says_zero(self):
        pool = _Pool()
        pool.seed("db_ping", pfs.FAILURES, "0")
        state = ProbeFailureState()
        await state.load(pool)

        for _ in range(3):
            await state.record_success(pool, "db_ping")

        assert pool.writes == []

    async def test_a_probe_with_no_row_writes_its_first_result_and_then_only_changes(self):
        pool = _Pool()
        state = ProbeFailureState()
        await state.load(pool)

        for ok in (True, True, False, False, True, True):
            if ok:
                await state.record_success(pool, "db_ping")
            else:
                await state.record_failure(pool, "db_ping")

        assert pool.count_writes() == [
            ("probe.db_ping", "0"),
            ("probe.db_ping", "1"),
            ("probe.db_ping", "2"),
            ("probe.db_ping", "0"),
        ]

    async def test_the_table_is_read_once_per_process_not_per_check(self):
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock()
        state = ProbeFailureState()

        await state.load(pool)
        await state.load(pool)
        await state.record_failure(pool, "db_ping")
        state.remediation_due("db_ping", COOLDOWN)

        pool.fetch.assert_awaited_once()

    async def test_a_failed_read_is_retried_and_until_then_counts_start_from_zero(self, caplog):
        pool = _Pool()
        pool.seed("disk_space", pfs.FAILURES, "2")
        state = ProbeFailureState()

        pool.down = True
        with caplog.at_level(logging.WARNING):
            await state.load(pool)
        assert "could not read failure counts" in caplog.text
        assert await state.record_failure(pool, "disk_space") == 1

        pool.down = False
        await state.load(pool)

        # The count this process recorded is newer than the row, and the late
        # read writes it back rather than taking the row's.
        assert state.failures == {"disk_space": 1}
        assert pool.value("disk_space", pfs.FAILURES) == "1"

    async def test_a_failed_write_is_retried_with_the_probe_s_next_result(self, caplog):
        pool = _Pool()
        pool.seed("worker_error_rate", pfs.FAILURES, "4")
        state = ProbeFailureState()
        await state.load(pool)

        pool.down = True
        with caplog.at_level(logging.WARNING):
            await state.record_success(pool, "worker_error_rate")
        assert "could not persist worker_error_rate's failure count 0" in caplog.text
        assert pool.value("worker_error_rate", pfs.FAILURES) == "4"

        pool.down = False
        await state.record_success(pool, "worker_error_rate")

        assert pool.value("worker_error_rate", pfs.FAILURES) == "0"

    async def test_unreadable_rows_are_skipped_and_the_rest_restored(self, caplog):
        pool = _Pool()
        pool.seed("disk_space", pfs.FAILURES, "three")
        pool.seed("db_ping", pfs.FAILURES, "-1")
        pool.seed("public_site", pfs.LAST_REMEDIATION, "a while ago")
        pool.rows[("not_a_probe", pfs.FAILURES)] = ("2", pfs.SOURCE)
        pool.seed("worker_error_rate", pfs.FAILURES, "2")
        pool.seed("grafana_datasources", pfs.LAST_REMEDIATION, _ago(minutes=5))
        state = ProbeFailureState()

        with caplog.at_level(logging.WARNING):
            await state.load(pool)

        assert state.failures == {"worker_error_rate": 2}
        assert list(state.last_remediation) == ["grafana_datasources"]
        assert caplog.text.count("ignoring unreadable row") == 4
        assert pool.writes == []

        # The probe's next result replaces the unreadable row.
        await state.record_failure(pool, "disk_space")
        assert pool.value("disk_space", pfs.FAILURES) == "1"

    async def test_a_self_heal_time_in_the_future_delays_a_heal_one_cooldown_not_forever(self):
        pool = _Pool()
        pool.seed("worker_error_rate", pfs.LAST_REMEDIATION, "2099-01-01T00:00:00+00:00")
        state = ProbeFailureState()

        await state.load(pool)

        assert state.last_remediation["worker_error_rate"] <= time.time()
        assert state.remediation_due("worker_error_rate", COOLDOWN) is False

    async def test_a_self_heal_is_written_as_a_utc_timestamp_under_the_probe_s_entity(self):
        pool = _Pool()
        before = datetime.now(UTC)

        await ProbeFailureState().mark_remediation(pool, "public_site")

        value, source = pool.rows[("probe.public_site", pfs.LAST_REMEDIATION)]
        assert source == pfs.SOURCE
        written = datetime.fromisoformat(value)
        assert written.utcoffset() == timedelta(0)
        assert before <= written <= datetime.now(UTC)

    async def test_a_failed_self_heal_write_keeps_the_cooldown_in_this_process(self, caplog):
        pool = _Pool()
        pool.down = True
        state = ProbeFailureState()

        with caplog.at_level(logging.WARNING):
            await state.mark_remediation(pool, "public_site")

        assert state.remediation_due("public_site", COOLDOWN) is False
        assert "could not persist public_site's self-heal time" in caplog.text


@pytest.mark.unit
class TestRestartKeepsTheStreak:
    """``run_health_probes`` across a restart, as the brain runs it."""

    async def test_two_failures_before_a_restart_and_one_after_page_on_the_third(self, restart):
        pool = _Pool()
        pages: list[str] = []

        await _cycle(pool, "disk_space", ok=False, notify=pages.append)
        await _cycle(pool, "disk_space", ok=False, notify=pages.append)
        assert pages == []

        restart()
        await _cycle(pool, "disk_space", ok=False, notify=pages.append)

        assert pages == ["🔴 Probe 'disk_space' failed 3x: down"]

    async def test_a_streak_announced_before_a_restart_is_not_announced_again(self, restart):
        pool = _Pool()
        pages: list[str] = []
        for _ in range(3):
            await _cycle(pool, "disk_space", ok=False, notify=pages.append)
        assert len(pages) == 1

        restart()
        for _ in range(3):
            await _cycle(pool, "disk_space", ok=False, notify=pages.append)

        assert len(pages) == 1
        assert pool.value("disk_space", pfs.FAILURES) == "6"

    async def test_a_recovery_after_a_restart_is_announced(self, restart):
        pool = _Pool()
        pages: list[str] = []
        notices: list[str] = []
        for _ in range(3):
            await _cycle(pool, "disk_space", ok=False, notify=pages.append, info=notices.append)

        restart()
        await _cycle(pool, "disk_space", ok=True, notify=pages.append, info=notices.append)

        assert notices == ["✅ Probe 'disk_space' recovered: fine"]
        assert pool.value("disk_space", pfs.FAILURES) == "0"

    async def test_the_self_heal_cooldown_survives_a_restart(self, restart):
        pool = _Pool()
        restart_container = AsyncMock(return_value=_restarted("poindexter-worker"))

        with patch.object(hp, "_restart_container", restart_container):
            for _ in range(3):
                await _cycle(pool, "worker_error_rate", ok=False, notify=AsyncMock())
            assert restart_container.call_count == 1

            restart()
            await _cycle(pool, "worker_error_rate", ok=False, notify=AsyncMock())
            assert restart_container.call_count == 1

            # Once the cooldown has run out, the streak read back heals again,
            # even in a cycle where the probe isn't due to run.
            pool.seed("worker_error_rate", pfs.LAST_REMEDIATION, _ago(seconds=COOLDOWN + 60))
            restart()
            await _cycle(pool, "worker_error_rate", due=False, notify=AsyncMock())

        assert restart_container.call_count == 2

    async def test_a_self_heal_the_cycle_watchdog_cancels_still_starts_the_cooldown(
        self, restart,
    ):
        """``brain_daemon`` runs each cycle under ``asyncio.wait_for``. A
        container restart that outlives the cycle is cancelled mid-flight, and
        a cooldown recorded only after the action would never be recorded."""
        pool = _Pool()
        release = threading.Event()

        async def slow_restart(container: str, *, pool: Any = None) -> du.ContainerRestart:
            # The real helper runs docker in a worker thread, which a
            # cancelled await leaves running; so does this.
            await asyncio.to_thread(release.wait, 10)
            return _restarted(container)

        try:
            with (
                patch.object(hp, "_restart_container", side_effect=slow_restart),
                pytest.raises(TimeoutError),
            ):
                await asyncio.wait_for(
                    hp._try_remediation("worker_error_rate", {"detail": "down"}, pool=pool),
                    timeout=0.2,
                )
        finally:
            release.set()

        restart()
        await pfs.state.load(pool)
        assert pfs.state.remediation_due("worker_error_rate", COOLDOWN) is False

    async def test_a_recovered_probe_whose_reset_write_failed_is_not_healed_after_a_restart(
        self, restart,
    ):
        """A count left stale would come back after a restart and heal a
        service that had already recovered."""
        pool = _Pool()
        restart_container = AsyncMock(return_value=_restarted("poindexter-worker"))

        with patch.object(hp, "_restart_container", restart_container):
            for _ in range(3):
                await _cycle(pool, "worker_error_rate", ok=False, notify=AsyncMock())
            pool.down = True
            await _cycle(pool, "worker_error_rate", ok=True, notify=AsyncMock())
            pool.down = False
            await _cycle(pool, "worker_error_rate", ok=True, notify=AsyncMock())

            # After the restart the probe isn't due yet, so only the count read
            # back from the table decides whether it is healed.
            pool.seed("worker_error_rate", pfs.LAST_REMEDIATION, _ago(seconds=COOLDOWN + 60))
            restart()
            await _cycle(pool, "worker_error_rate", due=False, notify=AsyncMock())

        assert pool.value("worker_error_rate", pfs.FAILURES) == "0"
        assert restart_container.call_count == 1

    async def test_a_count_left_by_a_removed_probe_heals_nothing(self):
        pool = _Pool()
        pool.seed("grafana_datasources", pfs.FAILURES, "5")
        restart_container = AsyncMock(return_value=_restarted("poindexter-grafana"))

        with patch.object(hp, "_restart_container", restart_container):
            await _cycle(pool, "db_ping", ok=True)

        restart_container.assert_not_called()

    async def test_a_database_that_is_down_never_stops_a_page_or_a_heal(self, restart):
        pool = _Pool()
        pool.down = True
        pages: list[str] = []
        restart_container = AsyncMock(return_value=_restarted("poindexter-worker"))

        with patch.object(hp, "_restart_container", restart_container):
            for _ in range(3):
                await _cycle(pool, "worker_error_rate", ok=False, notify=pages.append)

        assert "🔴 Probe 'worker_error_rate' failed 3x: down" in pages
        restart_container.assert_called_once_with("poindexter-worker", pool=pool)


@pytest.mark.unit
def test_every_self_heal_belongs_to_a_probe():
    """Self-heals run only for probes in ``PROBES``, so that a count read back
    for a probe that no longer exists can't restart anything. A remediation
    keyed to any other name would never run."""
    assert set(hp.REMEDIATIONS) <= set(hp.PROBES)


@pytest.mark.unit
def test_the_rows_cannot_overwrite_another_writer_s_row():
    """``probe.<name>`` rows are keyed on (entity, attribute), and an upsert
    overwrites whatever row holds the key. ``health_probes`` writes
    ``health_status`` (with ``source='health_probe'``, which ``doctor`` reads)
    and ``probe_schedule`` writes ``last_run_at``."""
    attributes = ["health_status", probe_schedule.ATTRIBUTE, pfs.FAILURES, pfs.LAST_REMEDIATION]
    assert len(set(attributes)) == len(attributes)
    assert pfs.SOURCE not in {"health_probe", probe_schedule.SOURCE}
    assert pfs.ENTITY_PREFIX == "probe."
