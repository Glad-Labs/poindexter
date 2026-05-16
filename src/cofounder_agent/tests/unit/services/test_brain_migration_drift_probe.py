"""Unit tests for brain/migration_drift_probe.py (GH#228).

Covers the three required scenarios from the issue:

1. drift=0 → success without firing notify_operator
2. drift>0 + auto_recover=false → notify once, no restart
3. drift>0 + auto_recover=true → restart, re-check, escalate only if drift persists

Plus assorted edge cases (worker unreachable, restart fails, repeat-cycle
notification dedupe). All external I/O (asyncpg, urllib, subprocess) is
mocked — no real ``docker restart`` runs and no test sleeps for real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import migration_drift_probe as mdp


def _make_pool():
    """Build a minimal asyncpg pool mock.

    Defaults make the relkind probe (#329 follow-up) report no
    mismatches so pre-existing tests don't have to know about it.
    Tests that exercise the relkind probe override ``pool.fetch``
    explicitly.
    """
    pool = MagicMock()
    # Default: every expected relation has its expected relkind.
    pool.fetch = AsyncMock(return_value=[
        {"relname": name, "relkind": expected.encode("ascii")}
        for name, expected in mdp._EXPECTED_RELKINDS.items()
    ])
    pool.fetchrow = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


def _health_with_drift(pending: int, applied: int = 5, latest: str = "0121.py") -> dict:
    return {
        "status": "healthy",
        "components": {
            "migrations": {
                "applied": applied,
                "pending": pending,
                "latest_applied": latest,
                "drift": pending > 0,
            }
        },
    }


@pytest.fixture(autouse=True)
def _reset_module_state():
    mdp._last_notify_drift_count = None
    mdp._last_relkind_notify_key = None
    yield
    mdp._last_notify_drift_count = None
    mdp._last_relkind_notify_key = None


# ---------------------------------------------------------------------------
# Scenario 1 — drift=0 → success, no notify
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoDrift:
    @pytest.mark.asyncio
    async def test_zero_drift_returns_ok_no_notify(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")  # auto-recover off

        notifies: list[dict] = []
        restart_calls: list[None] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_restart():
            restart_calls.append(None)
            return True, "should not be called"

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=fake_restart,
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(0),
        )

        assert summary["ok"] is True
        assert summary["status"] == "no_drift"
        assert summary["pending"] == 0
        assert notifies == []
        assert restart_calls == []

        # Audit row should record the no-drift cycle.
        assert pool.execute.call_count >= 1
        audit_call = pool.execute.call_args_list[-1]
        assert "audit_log" in audit_call.args[0]
        assert audit_call.args[1] == "probe.migration_drift_ok"

    @pytest.mark.asyncio
    async def test_drift_clear_resets_notify_dedupe(self):
        # If a previous cycle notified about pending=2, then a later
        # cycle finds drift cleared, the module-level dedupe counter
        # must reset so a NEW drift event re-notifies.
        mdp._last_notify_drift_count = 2

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")

        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(0),
        )

        assert mdp._last_notify_drift_count is None


# ---------------------------------------------------------------------------
# Scenario 2 — drift>0 + auto_recover=false → notify once, no restart
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDriftAutoRecoverDisabled:
    @pytest.mark.asyncio
    async def test_drift_with_auto_recover_off_notifies_once(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")  # auto-recover off

        notifies: list[dict] = []
        restart_calls: list[None] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_restart():
            restart_calls.append(None)
            return True, "should not be called"

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=fake_restart,
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(2),
        )

        assert summary["ok"] is False
        assert summary["status"] == "drift_detected_no_recover"
        assert summary["pending"] == 2
        assert summary["auto_recover_enabled"] is False
        assert restart_calls == []
        assert len(notifies) == 1
        assert "Migration drift detected" in notifies[0]["title"]
        assert notifies[0]["severity"] == "warning"
        assert "DISABLED" in notifies[0]["detail"]

    @pytest.mark.asyncio
    async def test_repeat_cycle_with_unchanged_drift_does_not_renotify(self):
        # First cycle notifies; second cycle with same pending count
        # must NOT re-notify (avoids stuck-loop Telegram blasts).
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")

        notifies: list[dict] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(2),
        )
        assert len(notifies) == 1

        # Second cycle, drift unchanged → no new notification.
        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(2),
        )
        assert len(notifies) == 1

    @pytest.mark.asyncio
    async def test_drift_count_change_re_notifies(self):
        # First cycle pending=2 → notify. Next cycle pending=3 → notify
        # again because the situation changed.
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")

        notifies: list[dict] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(2),
        )
        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(3),
        )

        assert len(notifies) == 2

    @pytest.mark.asyncio
    async def test_drift_always_writes_detected_audit_event(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")

        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(2),
        )

        events_written = [
            call.args[1] for call in pool.execute.call_args_list
            if "audit_log" in call.args[0]
        ]
        assert "probe.migration_drift_detected" in events_written


# ---------------------------------------------------------------------------
# Scenario 3 — drift>0 + auto_recover=true → restart + re-check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDriftAutoRecoverEnabled:
    @pytest.mark.asyncio
    async def test_recover_clears_drift_no_escalation(self):
        # Drift detected, auto-recover ON, restart succeeds, post-restart
        # health shows pending=0 → recovered status, no notify.
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="true")  # auto-recover on

        notifies: list[dict] = []
        restart_calls: list[None] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_restart():
            restart_calls.append(None)
            return True, "Restarted"

        # First fetch: drift=2. After restart, wait_fn returns the
        # post-restart health with pending=0.
        post_health = _health_with_drift(0, applied=7, latest="0123.py")
        fetch_results = iter([_health_with_drift(2)])

        def fake_health():
            try:
                return next(fetch_results)
            except StopIteration:
                return post_health

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=fake_restart,
            wait_fn=lambda: (True, post_health),
            health_fetcher=fake_health,
        )

        assert summary["ok"] is True
        assert summary["status"] == "recovered"
        assert summary["pending"] == 0
        assert summary["previous_pending"] == 2
        assert len(restart_calls) == 1
        # No escalation needed — recovery succeeded silently.
        assert notifies == []

        events_written = [
            call.args[1] for call in pool.execute.call_args_list
            if "audit_log" in call.args[0]
        ]
        assert "probe.migration_drift_detected" in events_written
        assert "probe.migration_drift_recovered" in events_written

    @pytest.mark.asyncio
    async def test_recover_drift_persists_escalates(self):
        # Drift detected, auto-recover ON, restart succeeds, but
        # post-restart drift is still > 0 (e.g. broken migration the
        # runner refused to apply) → critical notify + audit.
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="true")

        notifies: list[dict] = []
        restart_calls: list[None] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_restart():
            restart_calls.append(None)
            return True, "Restarted"

        post_health = _health_with_drift(2)  # still drifting

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=fake_restart,
            wait_fn=lambda: (True, post_health),
            health_fetcher=lambda: _health_with_drift(2),
        )

        assert summary["ok"] is False
        assert summary["status"] == "recover_drift_persists"
        assert len(restart_calls) == 1
        assert len(notifies) == 1
        assert notifies[0]["severity"] == "critical"
        assert "PERSISTS" in notifies[0]["title"]

    @pytest.mark.asyncio
    async def test_recover_restart_fails_escalates(self):
        # docker restart itself fails → critical notify, no health re-check.
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="true")

        notifies: list[dict] = []
        wait_calls: list[None] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_wait():
            wait_calls.append(None)
            return True, {}

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=lambda: (False, "docker socket permission denied"),
            wait_fn=fake_wait,
            health_fetcher=lambda: _health_with_drift(1),
        )

        assert summary["ok"] is False
        assert summary["status"] == "recover_restart_failed"
        assert len(notifies) == 1
        assert notifies[0]["severity"] == "critical"
        assert "docker socket permission denied" in notifies[0]["detail"]
        # wait_fn must NOT be called when restart failed.
        assert wait_calls == []

    @pytest.mark.asyncio
    async def test_recover_worker_unhealthy_after_restart_escalates(self):
        # Restart succeeded but worker never reports healthy within
        # RESTART_WAIT_SECONDS → critical notify.
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="true")

        notifies: list[dict] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=lambda: (True, "Restarted"),
            wait_fn=lambda: (False, {"_error": "Connection refused"}),
            health_fetcher=lambda: _health_with_drift(1),
        )

        assert summary["ok"] is False
        assert summary["status"] == "recover_unhealthy"
        assert len(notifies) == 1
        assert notifies[0]["severity"] == "critical"
        assert "did not come back healthy" in notifies[0]["title"]


# ---------------------------------------------------------------------------
# Edge cases — worker unreachable, malformed health response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_worker_unreachable_returns_unknown_status(self):
        # health endpoint errors out → status=unknown, no escalation
        # (worker_error_rate probe owns the down-worker alert path).
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")

        notifies: list[dict] = []
        restart_calls: list[None] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_restart():
            restart_calls.append(None)
            return True, ""

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=fake_restart,
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: {"_error": "ConnectionRefusedError"},
        )

        assert summary["ok"] is True  # not OUR failure to surface
        assert summary["status"] == "unknown"
        assert notifies == []
        assert restart_calls == []

    @pytest.mark.asyncio
    async def test_health_missing_migrations_block_returns_unknown(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: {"status": "healthy", "components": {}},
        )

        assert summary["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_audit_write_failure_does_not_crash_probe(self):
        # If audit_log table is missing, probe should still return a
        # sane summary instead of raising.
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")
        pool.execute = AsyncMock(side_effect=Exception("audit_log does not exist"))

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(0),
        )

        assert summary["status"] == "no_drift"

    @pytest.mark.asyncio
    async def test_auto_recover_setting_missing_defaults_off(self):
        # Row absent → behave as if auto_recover=false.
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=None)

        notifies: list[dict] = []
        restart_calls: list[None] = []

        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=lambda **kwargs: notifies.append(kwargs),
            restart_fn=lambda: (restart_calls.append(None) or (True, "")),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(1),
        )

        assert restart_calls == []
        assert len(notifies) == 1


# ---------------------------------------------------------------------------
# _drift_from_health helper — direct unit coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDriftFromHealth:
    def test_extracts_pending_applied_latest(self):
        out = mdp._drift_from_health(_health_with_drift(3, applied=10, latest="0125.py"))
        assert out["ok"] is True
        assert out["pending"] == 3
        assert out["applied"] == 10
        assert out["latest_applied"] == "0125.py"

    def test_handles_zero_pending(self):
        out = mdp._drift_from_health(_health_with_drift(0))
        assert out["ok"] is True
        assert out["pending"] == 0

    def test_returns_not_ok_on_fetch_error(self):
        out = mdp._drift_from_health({"_error": "timeout"})
        assert out["ok"] is False
        assert "timeout" in out["error"]

    def test_returns_not_ok_when_migrations_block_has_error(self):
        health = {
            "status": "degraded",
            "components": {"migrations": {"status": "unknown", "error": "DB down"}},
        }
        out = mdp._drift_from_health(health)
        assert out["ok"] is False
        assert "DB down" in out["error"]

    def test_returns_not_ok_when_pending_field_missing(self):
        health = {"status": "healthy", "components": {"migrations": {"applied": 5}}}
        out = mdp._drift_from_health(health)
        assert out["ok"] is False
        assert "pending" in out["error"]
        # 2026-05-16: distinct from the "component absent" case below
        assert "present but" in out["error"]

    def test_returns_not_ok_when_migrations_component_absent(self):
        """Captured 2026-05-16: the worker's /api/health was returning
        ``components.migrations = None``. Pre-fix the probe reported
        "missing 'pending' field" which read like a contract bug; in
        reality the migrations component wasn't wired into the
        endpoint at all. Different cause → different message so the
        operator stops chasing pending-field ghosts.
        """
        # Case 1: components dict has no migrations key
        out = mdp._drift_from_health({"status": "healthy", "components": {}})
        assert out["ok"] is False
        assert "absent" in out["error"]

        # Case 2: components.migrations is explicitly None
        out = mdp._drift_from_health(
            {"status": "healthy", "components": {"migrations": None}}
        )
        assert out["ok"] is False
        assert "absent" in out["error"]

    def test_returns_not_ok_when_migrations_is_wrong_type(self):
        """A non-dict value at ``components.migrations`` is treated as
        an absent component for our purposes (the probe can't extract
        ``pending`` from a string/list). Better than crashing."""
        out = mdp._drift_from_health(
            {"status": "healthy", "components": {"migrations": "n/a"}}
        )
        assert out["ok"] is False
        # Non-dict falls through to "missing 'pending' field" — still
        # reports correctly, just via the post-coerce path
        assert "pending" in out["error"]


# ---------------------------------------------------------------------------
# _read_auto_recover_enabled — boolean parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadAutoRecoverEnabled:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("value,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("garbage", False),
    ])
    async def test_parses_common_boolean_values(self, value, expected):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=value)
        assert await mdp._read_auto_recover_enabled(pool) is expected

    @pytest.mark.asyncio
    async def test_missing_row_returns_false(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=None)
        assert await mdp._read_auto_recover_enabled(pool) is False

    @pytest.mark.asyncio
    async def test_db_failure_returns_false(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(side_effect=Exception("db down"))
        assert await mdp._read_auto_recover_enabled(pool) is False


# ---------------------------------------------------------------------------
# Relkind contract probe (Glad-Labs/poindexter#329 follow-up).
# Verifies that the post-migration shape of relations in
# ``_EXPECTED_RELKINDS`` is the early-warning signal the docstring
# promises.
# ---------------------------------------------------------------------------


def _row(relname: str, relkind):
    """Build a fake asyncpg row-like for ``_check_relkind_mismatches``."""
    return {"relname": relname, "relkind": relkind}


@pytest.mark.unit
class TestCheckRelkindMismatches:
    @pytest.mark.asyncio
    async def test_no_mismatches_when_view_shape_matches(self):
        """The canonical post-#329 prod state — content_tasks is 'v'."""
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[_row("content_tasks", b"v")])
        assert await mdp._check_relkind_mismatches(pool) == []

    @pytest.mark.asyncio
    async def test_str_relkind_normalises_same_as_bytes(self):
        """asyncpg version drift — both str and bytes returns must
        compare as equal to the expected ``'v'``."""
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[_row("content_tasks", "v")])
        assert await mdp._check_relkind_mismatches(pool) == []

    @pytest.mark.asyncio
    async def test_table_shape_is_flagged(self):
        """The bug #329 was filed for — content_tasks as a TABLE in dev
        when prod has it as a VIEW. This is the contract violation the
        probe must surface."""
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[_row("content_tasks", b"r")])
        mismatches = await mdp._check_relkind_mismatches(pool)
        assert mismatches == [{
            "relname": "content_tasks",
            "expected": "v",
            "actual": "r",
        }]

    @pytest.mark.asyncio
    async def test_missing_relation_is_flagged(self):
        """If pg_class returns no row at all for content_tasks, the
        probe reports actual='' (empty) rather than silently passing."""
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        mismatches = await mdp._check_relkind_mismatches(pool)
        assert mismatches == [{
            "relname": "content_tasks",
            "expected": "v",
            "actual": "",
        }]

    @pytest.mark.asyncio
    async def test_query_failure_returns_empty_not_raise(self):
        """If pg_class is unreachable, degrade gracefully — the rest of
        the drift probe must keep running."""
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=Exception("connection lost"))
        # Should not raise.
        assert await mdp._check_relkind_mismatches(pool) == []

    @pytest.mark.asyncio
    async def test_empty_expected_table_returns_empty(self, monkeypatch):
        """Sanity guard — if the expected mapping is empty (future
        cleanup), the probe should exit cheap without any DB query."""
        pool = _make_pool()
        pool.fetch = AsyncMock()
        monkeypatch.setattr(mdp, "_EXPECTED_RELKINDS", {})
        assert await mdp._check_relkind_mismatches(pool) == []
        # Critically — no query was issued.
        pool.fetch.assert_not_called()


@pytest.mark.unit
class TestNoDriftWithRelkindMismatch:
    """The probe runs the relkind check INSIDE the no-drift happy path,
    so verify the integration: pending=0 + relkind mismatch => still ok=True
    overall but mismatches surfaced + notify_operator fired."""

    @pytest.mark.asyncio
    async def test_no_drift_but_relkind_mismatch_notifies(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")
        # pg_class probe returns a TABLE-shaped content_tasks (the bug).
        pool.fetch = AsyncMock(return_value=[_row("content_tasks", b"r")])

        notifies: list[dict] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        summary = await mdp.run_migration_drift_probe(
            pool,
            notify_fn=fake_notify,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(0),
        )

        # Drift itself is fine — pending=0.
        assert summary["status"] == "no_drift"
        assert summary["ok"] is True
        # But the relkind mismatch surfaced in the result + a notify fired.
        assert summary["relkind_mismatches"] == [{
            "relname": "content_tasks",
            "expected": "v",
            "actual": "r",
        }]
        assert len(notifies) == 1
        assert "relkind" in notifies[0]["title"].lower() or "schema" in notifies[0]["title"].lower()

    @pytest.mark.asyncio
    async def test_repeat_cycle_with_same_mismatch_does_not_renotify(self):
        """Persistent mismatch shouldn't blast Telegram every cycle —
        same dedupe contract as drift notifications."""
        # Pre-load the dedupe key as if a prior cycle already notified.
        mdp._last_relkind_notify_key = ("content_tasks", "v", "r")

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")
        pool.fetch = AsyncMock(return_value=[_row("content_tasks", b"r")])

        notifies: list[dict] = []

        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(0),
        )

        # No new notification — same mismatch as last cycle.
        assert notifies == []

    @pytest.mark.asyncio
    async def test_mismatch_clearing_resets_dedupe(self):
        """When the mismatch resolves (operator fixes the schema),
        the dedupe key resets so a NEW mismatch triggers a fresh notify."""
        mdp._last_relkind_notify_key = ("content_tasks", "v", "r")

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="false")
        # Now it matches — relkind == 'v'.
        pool.fetch = AsyncMock(return_value=[_row("content_tasks", b"v")])

        await mdp.run_migration_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            restart_fn=lambda: (True, ""),
            wait_fn=lambda: (True, {}),
            health_fetcher=lambda: _health_with_drift(0),
        )

        assert mdp._last_relkind_notify_key is None
