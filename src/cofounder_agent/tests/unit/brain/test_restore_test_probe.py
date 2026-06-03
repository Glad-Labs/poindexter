"""Unit tests for brain/restore_test_probe.py (poindexter#441).

All docker/subprocess/filesystem I/O is injected — no real container ever
runs. The pool is a MagicMock with AsyncMock methods; app_settings reads
are seeded via the ``setting_values`` dict passed to ``_make_pool``, and
the daily-gate query is seeded via ``seconds_since_last_run``.
"""
from __future__ import annotations

import os as _os
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package resolves.
from brain import restore_test_probe as rt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _settings(**over: str) -> dict[str, str]:
    base = {
        rt.ENABLED_KEY: "true",
        rt.INTERVAL_HOURS_KEY: "24",
        rt.BACKUP_DIR_KEY: "/host-backups/auto",
        rt.TIER_KEY: "daily",
        rt.POSTGRES_IMAGE_KEY: "pgvector/pgvector:pg16",
        rt.RUN_SMOKE_KEY: "true",
        rt.CRITICAL_TABLES_KEY: "posts,app_settings,audit_log",
        rt.MIN_ROW_COUNT_KEY: "1",
        rt.PG_READY_TIMEOUT_KEY: "60",
        rt.RESTORE_TIMEOUT_KEY: "300",
        rt.SMOKE_TIMEOUT_KEY: "180",
    }
    base.update(over)
    return base


def _make_pool(*, setting_values: dict[str, str] | None = None,
               seconds_since_last_run: float | None = None):
    pool = MagicMock()
    settings = _settings(**(setting_values or {}))

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        if "audit_log" in query:
            return seconds_since_last_run
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


def _events(pool) -> list[str]:
    return [c.args[1] for c in pool.execute.call_args_list
            if "INSERT INTO audit_log" in c.args[0]]


def _seams(**over):
    """Default all-pass injectable seams; override per test."""
    seams = {
        "find_dump_fn": lambda d, t: "/host-backups/auto/daily/poindexter_brain_x.dump",
        "discover_network_fn": lambda: "net",
        "start_fn": lambda name, image, net, pw: (True, "started"),
        "wait_ready_fn": lambda name, timeout: True,
        "copy_fn": lambda name, path: (True, "copied"),
        "restore_fn": lambda name, timeout: (0, ""),
        "count_fn": lambda name, db, table: 5,
        "smoke_fn": lambda thr, db, pw, timeout: (True, "OK"),
        "teardown_fn": MagicMock(),
        "notify_fn": MagicMock(),
    }
    seams.update(over)
    return seams


@pytest.fixture(autouse=True)
def _reset():
    rt._reset_module_state()
    yield
    rt._reset_module_state()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_read_config_coerces_types():
    cfg = await rt._read_config(_make_pool())
    assert cfg["enabled"] is True
    assert cfg["interval_hours"] == 24
    assert cfg["tier"] == "daily"
    assert cfg["critical_tables"] == ["posts", "app_settings", "audit_log"]
    assert cfg["min_row_count"] == 1
    assert cfg["run_smoke"] is True


@pytest.mark.asyncio
async def test_read_config_filters_invalid_table_names():
    pool = _make_pool(setting_values={
        rt.CRITICAL_TABLES_KEY: "posts, drop table; , app_settings"})
    cfg = await rt._read_config(pool)
    assert cfg["critical_tables"] == ["posts", "app_settings"]


# ---------------------------------------------------------------------------
# Dump discovery + daily gate
# ---------------------------------------------------------------------------
def _make_older(path):
    st = path.stat()
    _os.utime(path, (st.st_atime - 3600, st.st_mtime - 3600))


def test_find_latest_dump_picks_newest(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    old = daily / "poindexter_brain_20260601T000000Z.dump"
    new = daily / "poindexter_brain_20260602T000000Z.dump"
    old.write_text("old")
    new.write_text("new")
    _make_older(old)
    assert rt._find_latest_dump(str(tmp_path), "daily") == str(new)


def test_find_latest_dump_skips_tmp_and_missing(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "poindexter_brain_20260602T000000Z.dump.tmp").write_text("partial")
    assert rt._find_latest_dump(str(tmp_path), "daily") is None
    assert rt._find_latest_dump(str(tmp_path), "hourly") is None  # dir absent


def test_find_latest_dump_prefix_fallback(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    other = daily / "some-other-backup.dump"
    other.write_text("x")
    assert rt._find_latest_dump(str(tmp_path), "daily") == str(other)


@pytest.mark.asyncio
async def test_gate_returns_seconds_since_last_run():
    pool = _make_pool(seconds_since_last_run=3600.0)
    assert await rt._seconds_since_last_run(pool) == 3600.0


@pytest.mark.asyncio
async def test_gate_none_when_no_prior_run():
    assert await rt._seconds_since_last_run(_make_pool()) is None


# ---------------------------------------------------------------------------
# Docker seams (pure-ish parsers)
# ---------------------------------------------------------------------------
def test_discover_network_parses_first(monkeypatch):
    monkeypatch.setattr(rt, "_run_cmd",
                        lambda cmd, timeout: (0, "glad-labs-website_default\n", ""))
    assert rt._discover_network() == "glad-labs-website_default"


def test_discover_network_none_on_error(monkeypatch):
    monkeypatch.setattr(rt, "_run_cmd",
                        lambda cmd, timeout: (1, "", "no such container"))
    assert rt._discover_network() is None


def test_table_count_parses_int(monkeypatch):
    monkeypatch.setattr(rt, "_run_cmd", lambda cmd, timeout: (0, " 78 \n", ""))
    assert rt._table_count("c", "db", "posts") == 78


def test_table_count_rejects_bad_identifier(monkeypatch):
    called = []
    monkeypatch.setattr(
        rt, "_run_cmd",
        lambda cmd, timeout: called.append(cmd) or (0, "1", ""))
    assert rt._table_count("c", "db", "posts; DROP TABLE x") is None
    assert called == []  # never reached subprocess


def test_table_count_none_on_nonzero(monkeypatch):
    monkeypatch.setattr(rt, "_run_cmd",
                        lambda cmd, timeout: (1, "", "relation does not exist"))
    assert rt._table_count("c", "db", "missing") is None


# ---------------------------------------------------------------------------
# Verdict policy
# ---------------------------------------------------------------------------
def _ok_counts():
    return {"posts": 78, "app_settings": 800, "audit_log": 5}


def test_verdict_pass_clean():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="", row_counts=_ok_counts(),
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is True and sev == "info"


def test_verdict_pass_despite_benign_restore_warning():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=1, restore_stderr="warning: no privileges could be revoked",
        row_counts=_ok_counts(), schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is True and sev == "info"


def test_verdict_fail_empty_critical_table():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="",
        row_counts={"posts": 0, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is False and sev == "error"


def test_verdict_fail_missing_table_count():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="",
        row_counts={"posts": None, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is False and sev == "error"


def test_verdict_fail_smoke():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="", row_counts=_ok_counts(),
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=False, smoke_detail="FAIL: 2 missing")
    assert ok is False and sev == "error"


def test_verdict_fail_empty_schema_migrations():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="", row_counts=_ok_counts(),
        schema_migrations_count=0, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is False and sev == "error"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_disabled_short_circuits():
    pool = _make_pool(setting_values={rt.ENABLED_KEY: "false"})
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["status"] == "disabled"
    seams["teardown_fn"].assert_not_called()


@pytest.mark.asyncio
async def test_gate_skips_recent_run():
    pool = _make_pool(seconds_since_last_run=3600.0)  # 1h < 24h
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["status"] == "skipped"
    seams["teardown_fn"].assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_passes_and_tears_down():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is True and out["status"] == "passed"
    # teardown ran (stale-cleanup + finally); only ever with THROWAWAY.
    assert seams["teardown_fn"].called
    assert all(c.args == (rt.THROWAWAY_CONTAINER,)
               for c in seams["teardown_fn"].call_args_list)
    assert "probe.restore_test_completed" in _events(pool)


@pytest.mark.asyncio
async def test_corrupt_dump_pages_error():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(count_fn=lambda name, db, table: 0)  # empty tables
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "error"
    assert seams["teardown_fn"].called
    assert "probe.restore_test_failed" in _events(pool)


@pytest.mark.asyncio
async def test_no_dump_is_infra_warning():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(find_dump_fn=lambda d, t: None)
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False and out["status"] == "no_dump"
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "warning"
    seams["teardown_fn"].assert_not_called()  # never started a container


@pytest.mark.asyncio
async def test_container_start_failure_warns_and_cleans_up():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(start_fn=lambda name, image, net, pw: (False, "no image"))
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False and out["status"] == "infra_error"
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "warning"
    assert seams["teardown_fn"].called  # stale-cleanup still ran


@pytest.mark.asyncio
async def test_smoke_failure_pages_error():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(smoke_fn=lambda thr, db, pw, timeout: (False, "FAIL: 1 missing"))
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "error"


@pytest.mark.asyncio
async def test_recovery_notify_after_prior_failure():
    pool = _make_pool(seconds_since_last_run=None)
    rt._last_passed = False  # simulate previous run failed
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is True
    assert seams["notify_fn"].called
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "info"


@pytest.mark.asyncio
async def test_teardown_runs_even_on_seam_exception():
    pool = _make_pool(seconds_since_last_run=None)

    def boom(name, timeout):
        raise RuntimeError("restore blew up")

    seams = _seams(restore_fn=boom)
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False and out["status"] == "infra_error"
    assert seams["teardown_fn"].called


@pytest.mark.asyncio
async def test_smoke_skipped_when_network_undiscoverable():
    pool = _make_pool(seconds_since_last_run=None)
    smoke = MagicMock()
    seams = _seams(discover_network_fn=lambda: None, smoke_fn=smoke)
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is True  # restore + row counts still pass
    smoke.assert_not_called()  # smoke skipped without a network


# ---------------------------------------------------------------------------
# Regression: gate column + smoke restored-backup flag (poindexter#441)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_gate_query_uses_audit_log_timestamp_column():
    # The gate threw "column created_at does not exist" against the real
    # audit_log (whose time column is "timestamp"), returned None, and the
    # probe ran every brain cycle. The pool mock masked it — so assert on the
    # query text itself, which is what a revert would actually change.
    pool = _make_pool(seconds_since_last_run=42.0)
    await rt._seconds_since_last_run(pool)
    query = pool.fetchval.call_args.args[0]
    assert "created_at" not in query
    assert '"timestamp"' in query


def test_run_smoke_passes_restored_backup_flag(monkeypatch):
    # Without --restored-backup the smoke fails on a prod backup's historical
    # schema_migrations rows. The probe must always pass it.
    captured: dict[str, list[str]] = {}

    def fake_run_cmd(cmd, timeout):
        captured["cmd"] = cmd
        return (0, "[smoke] OK", "")

    monkeypatch.setattr(rt, "_run_cmd", fake_run_cmd)
    ok, _ = rt._run_smoke("throwaway", "poindexter", "pw", 60)
    assert ok is True
    assert "--restored-backup" in captured["cmd"]
    # appended after the script path so argparse on the worker side sees it
    assert captured["cmd"][-1] == "--restored-backup"
