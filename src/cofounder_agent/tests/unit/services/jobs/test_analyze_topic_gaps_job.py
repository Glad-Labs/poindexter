"""Unit tests for ``services/jobs/analyze_topic_gaps.py``.

Pool mocked. Covers each finding class (empty/low/stale), threshold
config pass-through, Gitea opt-in/out, and fetch failure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.analyze_topic_gaps import AnalyzeTopicGapsJob


def _make_pool(
    categories: list[dict] | None = None,
    stale: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
) -> Any:
    """Return a pool whose fetch routes by query content:
    - contains 'HAVING' → stale_rows
    - otherwise         → categories
    """
    conn = AsyncMock()

    async def _fetch(query: str, *args: Any) -> list[dict]:
        if fetch_raises is not None:
            raise fetch_raises
        if "HAVING" in query:
            return stale or []
        return categories or []

    conn.fetch = AsyncMock(side_effect=_fetch)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestContract:
    def test_has_required_attrs(self):
        job = AnalyzeTopicGapsJob()
        assert job.name == "analyze_topic_gaps"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_all_healthy_returns_zero_suggestions(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "AI", "posts": 10},
                {"name": "Hardware", "posts": 7},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "all categories healthy" in result.detail
        assert result.metrics == {
            "empty_categories": 0,
            "low_coverage_categories": 0,
            "stale_categories": 0,
            "topic_gap_rows_written": 0,
        }

    @pytest.mark.asyncio
    async def test_empty_category_flagged(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "Abandoned", "posts": 0},
                {"name": "Active", "posts": 10},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            result = await job.run(pool, {})
        assert result.metrics["empty_categories"] == 1
        assert result.changes_made == 1
        mock_gitea.assert_called_once()
        # Issue title should include the empty count. emit_finding is keyword-only.
        title = mock_gitea.call_args.kwargs["title"]
        assert "1 empty" in title

    @pytest.mark.asyncio
    async def test_low_coverage_flagged(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "Thin", "posts": 2},
                {"name": "Thick", "posts": 50},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {"low_threshold": 5})
        assert result.metrics["low_coverage_categories"] == 1
        assert "Thin (2)" in result.detail or result.changes_made == 1

    @pytest.mark.asyncio
    async def test_stale_category_flagged(self):
        pool, _ = _make_pool(
            categories=[{"name": "OldNews", "posts": 10}],
            stale=[
                {"name": "OldNews", "latest": datetime(2025, 1, 1, tzinfo=timezone.utc)},
            ],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {})
        assert result.metrics["stale_categories"] == 1

    @pytest.mark.asyncio
    async def test_low_threshold_config_honored(self):
        """low_threshold=3 → 2 posts is low, 3 is NOT low (strictly <)."""
        pool, _ = _make_pool(
            categories=[
                {"name": "A", "posts": 2},
                {"name": "B", "posts": 3},
                {"name": "C", "posts": 4},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {"low_threshold": 3})
        # Only A (posts=2) is below threshold 3.
        assert result.metrics["low_coverage_categories"] == 1

    @pytest.mark.asyncio
    async def test_stale_days_passed_to_query(self):
        pool, conn = _make_pool(categories=[], stale=[])
        job = AnalyzeTopicGapsJob()
        await job.run(pool, {"stale_days": 30, "file_gitea_issue": False})
        # Two fetch calls; second = stale query with stale_days as $1.
        second_call = conn.fetch.await_args_list[1]
        assert second_call.args[1] == 30

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool, _ = _make_pool(
            categories=[{"name": "Abandoned", "posts": 0}],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        mock_gitea = MagicMock()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding", new=mock_gitea,
        ):
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = AnalyzeTopicGapsJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail

    @pytest.mark.asyncio
    async def test_mixed_findings_aggregate_in_detail(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "Empty1", "posts": 0},
                {"name": "Empty2", "posts": 0},
                {"name": "Low", "posts": 3},
            ],
            stale=[
                {"name": "Old", "latest": datetime(2025, 1, 1, tzinfo=timezone.utc)},
            ],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {})
        # 2 empty + 1 low + 1 stale = 4 brain_knowledge rows upserted.
        assert result.metrics == {
            "empty_categories": 2,
            "low_coverage_categories": 1,
            "stale_categories": 1,
            "topic_gap_rows_written": 4,
        }
        # 3 suggestion classes, all triggered.
        assert result.changes_made == 3


# ---------------------------------------------------------------------------
# brain_knowledge upsert — poindexter#485 follow-up
# ---------------------------------------------------------------------------


class TestBrainKnowledgeUpsert:
    """The job materialises topic-gap findings as ``brain_knowledge``
    rows that ``services.topic_sources.knowledge.KnowledgeSource``
    consumes. Pins the upsert shape so the source contract stays
    intact when this job evolves.
    """

    @pytest.mark.asyncio
    async def test_upserts_one_row_per_gap_category(self):
        pool, conn = _make_pool(
            categories=[
                {"name": "AI", "posts": 0},      # empty
                {"name": "Gaming", "posts": 2},  # low (<5)
            ],
            stale=[
                {"name": "PC Hardware", "latest": datetime(2026, 4, 1, tzinfo=timezone.utc)},
            ],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {"file_gitea_issue": False})

        # 3 categories → 3 brain_knowledge rows. The job calls
        # ``conn.execute(sql, entity, value)`` once per row.
        execute_calls = conn.execute.await_args_list
        assert len(execute_calls) == 3
        assert result.metrics["topic_gap_rows_written"] == 3

        # Every call should target ``attribute='topic_gap'`` (baked into
        # the SQL) and have an ``entity`` starting with ``category.``
        # (the KnowledgeSource query pattern).
        for call in execute_calls:
            sql, entity, value = call.args
            assert "topic_gap" in sql
            assert entity.startswith("category."), (
                f"entity must be keyed under category.<slug> for the "
                f"KnowledgeSource reader; got {entity!r}"
            )
            assert "ON CONFLICT (entity, attribute) DO UPDATE" in sql
            assert isinstance(value, str) and len(value) > 10

    @pytest.mark.asyncio
    async def test_does_not_upsert_when_no_gaps_found(self):
        pool, conn = _make_pool(
            categories=[
                {"name": "AI", "posts": 10},
                {"name": "Gaming", "posts": 12},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        result = await job.run(pool, {"file_gitea_issue": False})

        # No findings → no upserts → no execute() calls.
        assert conn.execute.await_count == 0
        assert result.metrics["topic_gap_rows_written"] == 0

    @pytest.mark.asyncio
    async def test_upsert_failures_logged_but_do_not_break_job(self):
        """One row's upsert raising shouldn't poison the rest — the
        emit_finding path is the primary alert, and individual row
        failures are best-effort observability writes."""
        pool, conn = _make_pool(
            categories=[
                {"name": "AI", "posts": 0},      # empty
                {"name": "Gaming", "posts": 1},  # low
            ],
            stale=[],
        )

        # First execute() succeeds, second raises.
        call_count = {"n": 0}

        async def _execute(_sql, *_args):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated DB error")
            return "INSERT 0 1"

        conn.execute = AsyncMock(side_effect=_execute)

        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {"file_gitea_issue": False})

        # Job still succeeds; one row written, one logged-and-skipped.
        assert result.ok is True
        assert result.metrics["topic_gap_rows_written"] == 1

    @pytest.mark.asyncio
    async def test_low_entry_count_suffix_stripped_from_entity_slug(self):
        """The low list arrives as ``"<name> (<count>)"``. The entity
        slug must drop the parenthesised count so repeat analyses with
        different counts don't churn the unique-key constraint."""
        pool, conn = _make_pool(
            categories=[
                {"name": "AI", "posts": 3},  # low
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.emit_finding",
            new=MagicMock(),
        ):
            await job.run(pool, {"file_gitea_issue": False})

        # entity should be "category.ai", NOT "category.ai.3" or similar.
        execute_calls = conn.execute.await_args_list
        assert len(execute_calls) == 1
        _, entity, _ = execute_calls[0].args
        assert entity == "category.ai"
