"""Unit tests for ``services/jobs/run_dev_diary_post.py``.

Covers the loop semantics + the niche-creation + idempotency-marker
behavior. The DevDiarySource is mocked at the class-method level so
the job test never reaches subprocess or DB-side concerns that the
source's own tests already cover.

DB interactions go through the ``db_pool`` fixture (real Postgres,
all migrations applied). Per Matt's directive (PR #155): no row-faker
MagicMocks for DB behavior.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.run_dev_diary_post import (
    RunDevDiaryPostJob,
    _LAST_RUN_KEY,
    _NICHE_SLUG,
    _create_dev_diary_task,
    _format_draft_landed_message,
    _get_last_run_date,
    _set_last_run_date,
)
from services.topic_sources.dev_diary_source import DevDiaryContext


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetadata:
    def test_name(self):
        assert RunDevDiaryPostJob.name == "run_dev_diary_post"

    def test_schedule_is_cron_9am_et(self):
        # 0 13 * * * UTC = 9am EDT (US Eastern during DST)
        assert RunDevDiaryPostJob.schedule == "0 13 * * *"

    def test_idempotent_flag_set(self):
        assert RunDevDiaryPostJob.idempotent is True


# ---------------------------------------------------------------------------
# _format_draft_landed_message
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatDraftLandedMessage:
    def test_includes_date_task_id_gates_and_counts(self):
        ctx = DevDiaryContext(
            date="2026-05-02",
            merged_prs=[{"number": 1, "title": "x"}, {"number": 2, "title": "y"}],
            notable_commits=[{"sha": "a", "subject": "feat: x"}],
            brain_decisions=[{"id": 1, "decision": "z", "confidence": 0.9}],
            audit_resolved=[],
            recent_posts=[{"id": "p1", "title": "yesterday's post"}],
            cost_summary={"total_usd": 0.0123, "total_inferences": 42, "by_model": []},
        )
        msg = _format_draft_landed_message("task-uuid", ctx, "draft,final")
        assert "2026-05-02" in msg
        assert "task-uuid" in msg
        assert "draft,final" in msg
        assert "2 merged PRs" in msg
        assert "1 notable commits" in msg
        assert "1 high-confidence brain decisions" in msg
        assert "0.0123" in msg or "0.012" in msg
        assert "42 inferences" in msg


# ---------------------------------------------------------------------------
# Helpers (real DB roundtrip)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestLastRunDateMarker:
    async def test_unset_returns_none(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY,
            )
        result = await _get_last_run_date(db_pool)
        assert result is None

    async def test_set_then_get_roundtrip(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY,
            )
        await _set_last_run_date(db_pool, "2026-05-02")
        result = await _get_last_run_date(db_pool)
        assert result == "2026-05-02"

    async def test_set_overwrites_existing(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY,
            )
        await _set_last_run_date(db_pool, "2026-05-01")
        await _set_last_run_date(db_pool, "2026-05-02")
        result = await _get_last_run_date(db_pool)
        assert result == "2026-05-02"


# ---------------------------------------------------------------------------
# RunDevDiaryPostJob.run — the orchestration logic
# ---------------------------------------------------------------------------


def _quiet_ctx(date: str = "2026-05-02") -> DevDiaryContext:
    return DevDiaryContext(
        date=date,
        merged_prs=[], notable_commits=[], brain_decisions=[],
        audit_resolved=[], recent_posts=[],
        cost_summary={"total_usd": 0.0, "total_inferences": 0, "by_model": []},
    )


def _busy_ctx(date: str = "2026-05-02") -> DevDiaryContext:
    return DevDiaryContext(
        date=date,
        merged_prs=[
            {
                "number": 156,
                "title": "feat(gates): per-medium approval gate engine",
                "url": "https://github.com/Glad-Labs/poindexter/pull/156",
                "merged_at": "2026-05-01T20:00:00Z",
                "author": "matty",
            },
        ],
        notable_commits=[
            {
                "sha": "20b89a71",
                "subject": "feat(gates): per-medium approval gate engine (closes #24)",
                "prefix": "feat",
                "author": "Matt",
                "date": "2026-05-01T20:00:00Z",
            },
        ],
        brain_decisions=[
            {
                "id": 1,
                "decision": "Switch writer from gemma3:27b to glm-4.7-5090",
                "reasoning": "Approval rate up 12pp on glm",
                "confidence": 0.88,
                "created_at": "2026-05-01T18:00:00Z",
            },
        ],
        audit_resolved=[],
        recent_posts=[],
        cost_summary={"total_usd": 0.0034, "total_inferences": 12, "by_model": []},
    )


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestRun:
    async def test_no_pool_returns_error(self):
        job = RunDevDiaryPostJob()
        result = await job.run(pool=None, config={})
        assert result.ok is False
        assert "no DB pool" in result.detail
        assert result.changes_made == 0

    async def test_already_ran_today_short_circuits(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY)
            await conn.execute("DELETE FROM content_tasks WHERE category = $1", _NICHE_SLUG)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await _set_last_run_date(db_pool, today)

        job = RunDevDiaryPostJob()
        gather_called = AsyncMock()
        with patch(
            "services.topic_sources.dev_diary_source.DevDiarySource.gather_context",
            gather_called,
        ):
            result = await job.run(db_pool, {})

        assert result.ok is True
        assert result.changes_made == 0
        assert "already ran today" in result.detail
        # gather_context must not even be called when the marker is fresh
        gather_called.assert_not_awaited()

    async def test_quiet_day_skips_and_marks_marker(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY)
            await conn.execute("DELETE FROM content_tasks WHERE category = $1", _NICHE_SLUG)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        notify_calls: list[str] = []

        async def fake_notify(message, critical=False):
            notify_calls.append(message)

        async def fake_gather(self, pool, **kwargs):
            return _quiet_ctx(today)

        with (
            patch("services.topic_sources.dev_diary_source.DevDiarySource.gather_context",
                  fake_gather),
            patch("services.integrations.operator_notify.notify_operator", fake_notify),
        ):
            result = await RunDevDiaryPostJob().run(db_pool, {"notify_on_draft": True})

        assert result.ok is True
        assert result.changes_made == 0
        assert "quiet day" in result.detail
        # No content task should have been created
        async with db_pool.acquire() as conn:
            row_count = await conn.fetchval(
                "SELECT COUNT(*) FROM content_tasks WHERE category = $1",
                _NICHE_SLUG,
            )
        assert row_count == 0
        # Marker should be set so we don't re-fire on the same UTC day
        assert (await _get_last_run_date(db_pool)) == today
        # Operator should have been told about the quiet day
        assert any("quiet day" in m for m in notify_calls)

    async def test_busy_day_creates_task_and_notifies(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY)
            await conn.execute("DELETE FROM content_tasks WHERE category = $1", _NICHE_SLUG)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        notify_calls: list[str] = []

        async def fake_notify(message, critical=False):
            notify_calls.append(message)

        async def fake_gather(self, pool, **kwargs):
            return _busy_ctx(today)

        with (
            patch("services.topic_sources.dev_diary_source.DevDiarySource.gather_context",
                  fake_gather),
            patch("services.integrations.operator_notify.notify_operator", fake_notify),
        ):
            result = await RunDevDiaryPostJob().run(
                db_pool, {"gates": "draft,final", "notify_on_draft": True},
            )

        assert result.ok is True
        assert result.changes_made == 1
        assert "queued dev_diary task" in result.detail
        assert "gates=draft,final" in result.detail
        # The content_tasks row should be present + tagged correctly
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic, category, task_metadata FROM content_tasks "
                "WHERE category = $1 ORDER BY created_at DESC LIMIT 1",
                _NICHE_SLUG,
            )
        assert row is not None
        assert row["category"] == _NICHE_SLUG
        assert today in row["topic"]
        # task_metadata is JSONB — asyncpg returns it as a string in raw mode
        import json as _json
        meta = row["task_metadata"] if isinstance(row["task_metadata"], dict) \
            else _json.loads(row["task_metadata"])
        assert meta["niche"] == _NICHE_SLUG
        assert meta["gates"] == "draft,final"
        assert meta["request_type"] == "dev_diary"
        assert "context_bundle" in meta
        # Idempotency marker advanced
        assert (await _get_last_run_date(db_pool)) == today
        # Operator was notified about the draft (not the quiet-day branch)
        assert any("Dev diary draft queued" in m for m in notify_calls)

    async def test_notify_failure_does_not_break_job(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY)
            await conn.execute("DELETE FROM content_tasks WHERE category = $1", _NICHE_SLUG)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        async def fake_gather(self, pool, **kwargs):
            return _busy_ctx(today)

        async def boom(message, critical=False):
            raise RuntimeError("telegram down")

        with (
            patch("services.topic_sources.dev_diary_source.DevDiarySource.gather_context",
                  fake_gather),
            patch("services.integrations.operator_notify.notify_operator", boom),
        ):
            result = await RunDevDiaryPostJob().run(db_pool, {})

        # Notify failure must NOT take down the job — the post is queued.
        assert result.ok is True
        assert result.changes_made == 1

    async def test_gather_context_failure_returns_error(self, db_pool):
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", _LAST_RUN_KEY)

        async def boom(self, pool, **kwargs):
            raise RuntimeError("gh CLI exploded")

        with patch(
            "services.topic_sources.dev_diary_source.DevDiarySource.gather_context",
            boom,
        ):
            result = await RunDevDiaryPostJob().run(db_pool, {})

        assert result.ok is False
        assert "gather_context failed" in result.detail


# ---------------------------------------------------------------------------
# _create_dev_diary_task — #341 regression guard (mock pool, no live DB)
# ---------------------------------------------------------------------------


def _make_mock_pool(execute_side_effect=None):
    """Lightweight pool — ``async with pool.acquire()`` →
    ``async with conn.transaction()`` → ``await conn.execute(...)``.
    Mirrors the helpers in test_tasks_db / test_topic_discovery /
    test_topic_batch_service so all #188/#341 INSERT-target guard tests
    share a uniform shape.
    """
    conn = MagicMock()
    if execute_side_effect:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock()
    # _create_dev_diary_task now reads niches.writer_rag_mode via fetchval
    # before inserting, so the mock conn needs an awaitable for it.
    # Returning None keeps writer_rag_mode NULL in the INSERT, which is
    # the same behaviour the test originally exercised.
    conn.fetchval = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _tx_inner():
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **kw: _tx_inner())

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _stub_ctx() -> DevDiaryContext:
    """Minimal DevDiaryContext for the SQL-shape assertions — content
    of the bundle doesn't matter, only that headline() + to_dict() work.
    """
    return DevDiaryContext(
        date="2026-05-02",
        merged_prs=[],
        notable_commits=[],
        brain_decisions=[],
        audit_resolved=[],
        recent_posts=[],
        cost_summary={"total_usd": 0.0, "total_inferences": 0, "by_model": []},
    )


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestCreateDevDiaryTaskSQL:
    """#341 regression guard — ``_create_dev_diary_task`` must INSERT
    into ``pipeline_tasks`` + ``pipeline_versions`` (the underlying
    tables), never into the ``content_tasks`` view (which raises
    ``ObjectNotInPrerequisiteStateError`` in production).
    """

    async def test_writes_to_pipeline_tables_not_view(self):
        seen: list[str] = []

        async def _capture(sql, *args, **kwargs):
            seen.append(sql)
            return "INSERT 0 1"

        pool, _conn = _make_mock_pool(execute_side_effect=_capture)
        await _create_dev_diary_task(pool, _stub_ctx(), gates="draft,final")

        joined = "\n".join(seen)
        assert "pipeline_tasks" in joined
        assert "pipeline_versions" in joined
        assert "INSERT INTO content_tasks" not in joined

    async def test_emits_two_inserts(self):
        pool, conn = _make_mock_pool()
        await _create_dev_diary_task(pool, _stub_ctx(), gates="draft,final")
        # One INSERT into pipeline_tasks, one into pipeline_versions.
        assert conn.execute.await_count == 2

    async def test_returns_task_id(self):
        pool, _conn = _make_mock_pool()
        task_id = await _create_dev_diary_task(
            pool, _stub_ctx(), gates="draft,final",
        )
        assert isinstance(task_id, str)
        # Generated UUIDs follow the standard hyphenated 8-4-4-4-12 form
        assert task_id.count("-") == 4
