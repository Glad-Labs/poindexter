"""The dev_diary cron-rebase migration is guarded: it only rewrites the stale default."""
from __future__ import annotations

import importlib

import pytest


def _load_migration():
    # Resolve the timestamped module by suffix so the test is filename-stable.
    import pkgutil
    import services.migrations as m

    name = next(
        n for _, n, _ in pkgutil.iter_modules(m.__path__)
        if n.endswith("rebase_dev_diary_cron_local")
    )
    return importlib.import_module(f"services.migrations.{name}")


class _RecordingConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


@pytest.mark.asyncio
async def test_up_issues_guarded_update():
    mod = _load_migration()
    conn = _RecordingConn()
    await mod.up(_Pool(conn))
    sql = " ".join(conn.executed).lower()
    assert "plugin.job.run_dev_diary_post" in sql
    assert "0 9 * * *" in sql
    # Guard present: only rewrite when the row still holds the old default.
    assert "0 13 * * *" in sql
    assert "config,schedule" in sql.replace(" ", "")
