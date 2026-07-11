"""The social_post_drafts dedup migration supersedes only ACTIVE duplicates
and builds the partial unique index create_draft's ON CONFLICT infers against.

Regression pins for poindexter#833 (duplicate drafts on finalize re-runs):
- the one-time supersede touches only status IN ('pending','failed') rows —
  'posted' rows are terminal history (prod's pre-fix triple-post stays a true
  record) and 'rejected' rows are already inert;
- the newest row per (pipeline_task_id, platform, subreddit) key survives;
- the unique index is partial over the SAME two active statuses, so
  RetryFailedSocialDraftsJob's failed→pending reset can never collide.
"""
from __future__ import annotations

import importlib
import re

import pytest


def _load_migration():
    # Resolve the timestamped module by suffix so the test is filename-stable.
    import pkgutil

    import services.migrations as m

    name = next(
        n for _, n, _ in pkgutil.iter_modules(m.__path__)
        if n.endswith("dedup_social_post_drafts_active_key")
    )
    return importlib.import_module(f"services.migrations.{name}")


class _RecordingConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)
        return "UPDATE 0"

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


def _normalized_sql(conn: _RecordingConn) -> str:
    """Lowercase, collapse runs of whitespace, and tighten paren spacing so
    assertions pin SQL structure rather than statement line-wrapping."""
    flat = re.sub(r"\s+", " ", " ".join(conn.executed).lower())
    return re.sub(r"\(\s", "(", re.sub(r"\s\)", ")", flat))


@pytest.mark.asyncio
async def test_up_supersedes_only_active_dupes_keeping_newest():
    mod = _load_migration()
    conn = _RecordingConn()
    await mod.up(_Pool(conn))
    sql = _normalized_sql(conn)

    update = sql.split("create unique index")[0]
    assert "update social_post_drafts" in update
    assert "set status = 'rejected'" in update
    # Dup ranking runs over the natural key, newest-first, active rows only.
    assert (
        "partition by pipeline_task_id, platform, "
        "coalesce(platform_config->>'subreddit', '')" in update
    )
    assert "order by created_at desc" in update
    assert "rn > 1" in update
    assert "status in ('pending', 'failed')" in update
    assert "'posted'" not in update  # terminal history is untouchable


@pytest.mark.asyncio
async def test_up_builds_partial_unique_index_over_active_rows():
    mod = _load_migration()
    conn = _RecordingConn()
    await mod.up(_Pool(conn))
    sql = _normalized_sql(conn)

    index = "create unique index" + sql.split("create unique index")[1]
    assert "if not exists ux_social_post_drafts_active_key" in index
    assert "on social_post_drafts" in index
    assert (
        "(pipeline_task_id, platform, "
        "(coalesce(platform_config->>'subreddit', '')))" in index
    )
    assert "where status in ('pending', 'failed')" in index


@pytest.mark.asyncio
async def test_down_drops_only_the_index():
    mod = _load_migration()
    conn = _RecordingConn()
    await mod.down(_Pool(conn))
    sql = _normalized_sql(conn)
    assert "drop index if exists ux_social_post_drafts_active_key" in sql
    # The supersede is one-way — down() must not resurrect duplicates.
    assert "update" not in sql
