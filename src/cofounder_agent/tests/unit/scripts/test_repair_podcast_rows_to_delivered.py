"""scripts/repair_podcast_rows_to_delivered.py (Glad-Labs/poindexter#1090).

The decision is pure and pinned here case by case. The SQL runs on the real
schema in tests/integration_db/test_repair_podcast_rows_sql.py.
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load():
    script = _repo_root() / "scripts" / "repair_podcast_rows_to_delivered.py"
    spec = spec_from_file_location("repair_podcast_rows_to_delivered_t", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REPAIR = _load()


def _plan(keeper_size, backups, bucket_size, **kw):
    return REPAIR.plan_post("post-1", "keeper-1", keeper_size, backups, bucket_size, **kw)


class TestPlanPost:
    def test_surviving_row_that_matches_the_bucket_is_stamped(self):
        plan = _plan(100, [{"id": "b1", "size": 100, "duration_ms": 5}], 100)
        assert (plan.action, plan.backup_id) == ("stamp", None)

    def test_backed_up_row_that_matches_the_bucket_is_restored(self):
        plan = _plan(200, [{"id": "b1", "size": 100, "duration_ms": 5000}], 100)
        assert (plan.action, plan.backup_id, plan.needs_duration) == ("restore", "b1", False)

    def test_an_exact_backup_wins_over_a_newer_url_only_one(self):
        backups = [
            {"id": "newer-stub", "size": None, "duration_ms": None},
            {"id": "exact", "size": 100, "duration_ms": 5000},
        ]
        assert _plan(200, backups, 100).backup_id == "exact"

    def test_url_only_backup_is_restored_and_measured(self):
        plan = _plan(200, [{"id": "stub", "size": None, "duration_ms": None}], 100)
        assert (plan.action, plan.backup_id, plan.needs_duration) == ("restore", "stub", True)

    def test_backup_of_another_file_is_not_restored(self):
        """A backed-up row with a URL but a different size isn't the file in the
        bucket either, so the surviving row is made to describe the bucket."""
        plan = _plan(200, [{"id": "b1", "size": 300, "duration_ms": 5}], 100)
        assert (plan.action, plan.needs_duration) == ("describe", True)

    def test_no_backup_and_no_match_describes_the_bucket_object(self):
        plan = _plan(200, [], 100)
        assert (plan.action, plan.bucket_size) == ("describe", 100)

    def test_missing_object_is_left_alone(self):
        assert _plan(200, [{"id": "b1", "size": 100}], None).action == "missing"

    def test_unverified_store_is_left_alone(self):
        assert _plan(100, [], 100, unverified=True).action == "unverified"


class TestSql:
    def test_column_list_matches_the_dedup_migration(self):
        """The reverse swap must copy exactly the columns #884 copied."""
        migration = (
            _repo_root() / "src" / "cofounder_agent" / "poindexter" / "services" / "migrations"
            / "20260717_154103_dedup_podcast_media_assets_and_add_unique_index.py"
        ).read_text(encoding="utf-8")
        cols = re.search(r"INSERT INTO media_assets_dedup_backup \(([^)]*)\)", migration)
        assert cols is not None

        def split(s: str) -> list[str]:
            return [c.strip() for c in s.replace("\n", " ").split(",") if c.strip()]

        assert split(REPAIR.ASSET_COLUMNS) == split(cols.group(1))

    @pytest.mark.parametrize("name", ["STAMP_SQL", "DELETE_LIVE_SQL", "DESCRIBE_SQL", "MOVE_LIVE_TO_BACKUP_SQL"])
    def test_writes_to_the_live_row_require_it_still_has_no_url(self, name):
        assert "COALESCE(url, '') = ''" in getattr(REPAIR, name)

    def test_describe_keeps_the_replaced_values(self):
        sql = REPAIR.DESCRIBE_SQL
        assert "'previous_file_size_bytes', file_size_bytes" in sql
        assert "'previous_duration_ms', duration_ms" in sql


class TestApplyPlan:
    async def test_restore_backs_up_before_it_removes(self):
        conn = AsyncMock()
        conn.execute.side_effect = ["INSERT 0 1", "DELETE 1", "INSERT 0 1", "DELETE 1", "UPDATE 1"]
        plan = REPAIR.Plan("p", "restore", "keeper", 100, "backup", needs_duration=True)

        await REPAIR.apply_plan(conn, plan, url="https://cdn/p.mp3", duration_ms=1234)

        sqls = [c.args[0] for c in conn.execute.await_args_list]
        assert sqls == [
            REPAIR.MOVE_LIVE_TO_BACKUP_SQL, REPAIR.DELETE_LIVE_SQL,
            REPAIR.MOVE_BACKUP_TO_LIVE_SQL, REPAIR.DELETE_BACKUP_SQL,
            REPAIR.FILL_RESTORED_SQL,
        ]
        assert conn.execute.await_args_list[-1].args[1:] == (
            "backup", "https://cdn/p.mp3", 100, 1234, "keeper",
        )

    async def test_a_guard_that_matches_nothing_raises(self):
        """A row that gained a URL since the plan was made must abort its
        transaction rather than be overwritten."""
        conn = AsyncMock()
        conn.execute.return_value = "UPDATE 0"
        plan = REPAIR.Plan("p", "stamp", "keeper", 100)
        with pytest.raises(RuntimeError, match="stamp"):
            await REPAIR.apply_plan(conn, plan, url="u", duration_ms=None)

    @pytest.mark.parametrize("action", ["missing", "unverified"])
    async def test_reported_actions_change_nothing(self, action):
        conn = AsyncMock()
        with pytest.raises(ValueError):
            await REPAIR.apply_plan(conn, REPAIR.Plan("p", action, "keeper"), url="u", duration_ms=None)
        conn.execute.assert_not_awaited()


class TestProbeDuration:
    def test_parses_seconds_to_ms(self, monkeypatch):
        monkeypatch.setattr(
            REPAIR.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="187.08\n", stderr=""),
        )
        assert REPAIR.probe_duration_ms("https://cdn/p.mp3") == 187080

    def test_no_output_is_none(self, monkeypatch):
        monkeypatch.setattr(
            REPAIR.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="404"),
        )
        assert REPAIR.probe_duration_ms("https://cdn/p.mp3") is None

    def test_missing_ffprobe_is_none(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("ffprobe")

        monkeypatch.setattr(REPAIR.subprocess, "run", boom)
        assert REPAIR.probe_duration_ms("https://cdn/p.mp3") is None
