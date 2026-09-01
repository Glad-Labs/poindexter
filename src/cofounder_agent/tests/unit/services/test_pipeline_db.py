"""Tests for PipelineDB — the pipeline table write module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.pipeline_db import (
    LEGACY_SITE_TARGETS,
    SITE_TARGET,
    SITE_TARGETS,
    PipelineDB,
)


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def pdb(mock_pool):
    return PipelineDB(mock_pool)


class TestUpsertTask:
    @pytest.mark.asyncio
    async def test_upsert_task_calls_execute(self, pdb, mock_pool):
        await pdb.upsert_task("test-123", {"topic": "AI Testing", "status": "pending"})
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_task_handles_error(self, pdb, mock_pool):
        mock_pool.execute = AsyncMock(side_effect=Exception("DB down"))
        # Should not raise — logs warning instead
        await pdb.upsert_task("test-123", {"topic": "Test"})


class TestUpdateTaskStatus:
    @pytest.mark.asyncio
    async def test_update_status(self, pdb, mock_pool):
        await pdb.update_task_status("test-123", "published")
        mock_pool.execute.assert_awaited_once()
        call_args = mock_pool.execute.call_args
        assert "test-123" in call_args[0]
        assert "published" in call_args[0]

    @pytest.mark.asyncio
    async def test_update_status_handles_error(self, pdb, mock_pool):
        mock_pool.execute = AsyncMock(side_effect=Exception("DB down"))
        await pdb.update_task_status("test-123", "failed")


class TestUpsertVersion:
    @pytest.mark.asyncio
    async def test_upsert_version_calls_execute(self, pdb, mock_pool):
        await pdb.upsert_version("test-123", {
            "title": "Test Post",
            "content": "Some content",
            "quality_score": 85,
        })
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_version_handles_error(self, pdb, mock_pool):
        mock_pool.execute = AsyncMock(side_effect=Exception("DB down"))
        await pdb.upsert_version("test-123", {"title": "Test"})

    @pytest.mark.asyncio
    async def test_upsert_version_carries_qa_approved_snapshot_marker(self, pdb, mock_pool):
        """The qa_approved_snapshot marker (durable QA-approval, 2026-07-03)
        must land in stage_data so the stale-sweep promote bucket and the
        keep-best reject guard can see it."""
        import json

        await pdb.upsert_version("test-123", {
            "title": "T",
            "content": "body",
            "qa_approved_snapshot": {"score": 86.0, "approved_at": "2026-07-03T00:00:00Z"},
        })
        args = mock_pool.execute.call_args.args
        stage_data = json.loads(args[-1])
        assert stage_data["qa_approved_snapshot"]["score"] == 86.0


class TestClearQaApprovedSnapshot:
    @pytest.mark.asyncio
    async def test_clear_removes_marker_key(self, pdb, mock_pool):
        """Operator rejection must clear the marker so keep-best never
        resurrects content the operator explicitly threw away."""
        await pdb.clear_qa_approved_snapshot("test-123")
        mock_pool.execute.assert_awaited_once()
        sql = mock_pool.execute.call_args.args[0]
        assert "stage_data - 'qa_approved_snapshot'" in sql
        assert "pipeline_versions" in sql

    @pytest.mark.asyncio
    async def test_clear_handles_error(self, pdb, mock_pool):
        mock_pool.execute = AsyncMock(side_effect=Exception("DB down"))
        # Best-effort — must not raise.
        await pdb.clear_qa_approved_snapshot("test-123")


# NOTE: TestAddReview removed 2026-05-09 — the pipeline_reviews writes
# unified onto pipeline_gate_history per Glad-Labs/poindexter#366. The
# route handlers and stage callers now write directly to
# pipeline_gate_history; the corresponding tests live alongside the
# call sites (tests/unit/routes/test_approval_routes.py, etc.).


class TestAddDistribution:
    @pytest.mark.asyncio
    async def test_add_distribution(self, pdb, mock_pool):
        await pdb.add_distribution("test-123", SITE_TARGET, post_slug="test-post")
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_distribution_handles_error(self, pdb, mock_pool):
        mock_pool.execute = AsyncMock(side_effect=Exception("DB down"))
        await pdb.add_distribution("test-123", "dev.to")

    @pytest.mark.asyncio
    async def test_medium_defaults_to_the_single_artifact_sentinel(self, pdb, mock_pool):
        """A blog publish delivers one undifferentiated artifact, so callers here
        never name a medium — but the column is part of the row's unique key, so
        the write has to supply the sentinel explicitly."""
        await pdb.add_distribution("test-123", SITE_TARGET)
        sql, *args = mock_pool.execute.await_args.args
        assert args[:3] == ["test-123", SITE_TARGET, "default"]

    @pytest.mark.asyncio
    async def test_own_site_target_is_a_sentinel_not_a_hostname(self, pdb, mock_pool):
        """poindexter#1038 — the own-site row carried the source operator's own
        domain, at three write sites and five read sites.

        A fresh install was never *broken* by it (the literal matched on both
        sides), which is why it survived: the damage is that every fork labels
        its own publishes with someone else's brand, and every "not the site
        itself" filter reads as ``target <> '<one operator's domain>'``.

        The read side is a SQL view, so it cannot consult
        ``app_settings.site_domain`` — a sentinel is the only encoding that
        works on both ends.
        """
        assert "." not in SITE_TARGET, (
            f"{SITE_TARGET!r} looks like a hostname — the own-site target names "
            f"a relationship (published to self), not a domain"
        )
        await pdb.add_distribution("test-123", SITE_TARGET)
        _sql, *args = mock_pool.execute.await_args.args
        assert args[1] == "site"

    def test_readers_still_accept_the_pre_cutover_spelling(self):
        """Writers only ever produce the sentinel now, but a row carrying the old
        literal can still arrive — an older dump restored, or a task published
        inside a rollback window. Those rows must keep resolving their
        post_id / post_slug / published_at rather than reading as unpublished.
        """
        assert SITE_TARGETS[0] == SITE_TARGET
        assert "gladlabs.io" in LEGACY_SITE_TARGETS
        assert set(SITE_TARGETS) == {SITE_TARGET, *LEGACY_SITE_TARGETS}

    @pytest.mark.asyncio
    async def test_upsert_keys_on_medium_so_two_renders_coexist(self, pdb, mock_pool):
        """The conflict target must include medium. Under the old
        (task_id, target) key a task's Short overwrote its long-form YouTube row
        and one handle was lost — see migration 20260901_173133."""
        await pdb.add_distribution("test-123", "youtube", medium="video_short")
        sql, *args = mock_pool.execute.await_args.args
        assert "ON CONFLICT (task_id, target, medium)" in sql
        assert args[2] == "video_short"
