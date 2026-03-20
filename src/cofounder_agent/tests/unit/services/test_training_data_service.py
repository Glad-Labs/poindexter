"""
Unit tests for services/training_data_service.py

Tests TrainingDataService: initialization, _row_to_datapoint, save_training_example,
get_all_training_data, filter_training_data, add_tags, remove_tags, get_statistics,
export_as_jsonl, list_datasets, get_dataset.
All database calls are mocked via an asyncpg Pool mock.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.training_data_service import (
    DataTag,
    TrainingDatapoint,
    TrainingDataService,
    TrainingDataStats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_pool() -> tuple:
    """Return a mock asyncpg.Pool with a reusable acquire() context manager."""
    pool = MagicMock()
    conn = AsyncMock()

    # acquire() returns a context manager yielding conn
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    return pool, conn


def make_service() -> tuple:
    """Return (TrainingDataService, conn_mock)."""
    pool, conn = make_mock_pool()
    svc = TrainingDataService(db_pool=pool)
    return svc, conn


def make_sample_row(**overrides) -> MagicMock:
    """Build a mock asyncpg.Record-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": 1,
        "execution_id": "exec-abc-123",
        "user_request": "Write a blog post about AI",
        "intent": "content_generation",
        "business_state": {"topic": "AI"},
        "execution_plan": {"steps": ["research", "draft"]},
        "execution_result": {"output": "Final content"},
        "quality_score": 0.85,
        "success": True,
        "tags": ["production"],
        "created_at": datetime(2025, 3, 12, tzinfo=timezone.utc),
        "post_publication_metrics": None,
        "patterns_discovered": None,
        **overrides,
    }[key]
    row.get = lambda key, default=None: {
        "post_publication_metrics": None,
        "patterns_discovered": None,
        **overrides,
    }.get(key, default)
    return row


# ---------------------------------------------------------------------------
# DataTag Enum
# ---------------------------------------------------------------------------


class TestDataTag:
    def test_enum_values(self):
        assert DataTag.PRODUCTION == "production"
        assert DataTag.LOW_QUALITY == "low_quality"
        assert DataTag.EXCLUDE == "exclude"
        assert DataTag.MANUAL_APPROVED == "manual_approved"


# ---------------------------------------------------------------------------
# TrainingDatapoint dataclass
# ---------------------------------------------------------------------------


class TestTrainingDatapoint:
    def test_creates_with_required_fields(self):
        dp = TrainingDatapoint(
            id="1",
            execution_id="exec-1",
            user_request="Write blog post",
            intent="content",
            business_state={},
            execution_plan={},
            execution_result={},
            quality_score=0.8,
            success=True,
            tags=["production"],
            created_at="2025-01-01T00:00:00+00:00",
        )
        assert dp.id == "1"
        assert dp.quality_score == 0.8
        assert dp.success is True

    def test_optional_fields_default_to_none(self):
        dp = TrainingDatapoint(
            id="1",
            execution_id="exec-1",
            user_request="Test",
            intent="test",
            business_state={},
            execution_plan={},
            execution_result={},
            quality_score=0.5,
            success=False,
            tags=[],
            created_at="2025-01-01T00:00:00+00:00",
        )
        assert dp.post_publication_metrics is None
        assert dp.patterns_discovered is None


# ---------------------------------------------------------------------------
# _row_to_datapoint
# ---------------------------------------------------------------------------


class TestRowToDatapoint:
    def test_converts_row_to_datapoint(self):
        svc, _ = make_service()
        row = make_sample_row()
        dp = svc._row_to_datapoint(row)
        assert isinstance(dp, TrainingDatapoint)
        assert dp.execution_id == "exec-abc-123"
        assert dp.quality_score == 0.85
        assert dp.success is True

    def test_tags_default_to_empty_list_when_none(self):
        svc, _ = make_service()
        row = make_sample_row()
        # Override tags to None
        original_getitem = row.__getitem__
        row.__getitem__ = lambda self, key: None if key == "tags" else original_getitem(key)
        dp = svc._row_to_datapoint(row)
        assert dp.tags == []

    def test_quality_score_cast_to_float(self):
        svc, _ = make_service()
        row = make_sample_row()
        dp = svc._row_to_datapoint(row)
        assert isinstance(dp.quality_score, float)

    def test_created_at_converted_to_iso_string(self):
        svc, _ = make_service()
        row = make_sample_row()
        dp = svc._row_to_datapoint(row)
        assert "2025" in dp.created_at


# ---------------------------------------------------------------------------
# save_training_example
# ---------------------------------------------------------------------------


class TestSaveTrainingExample:
    @pytest.mark.asyncio
    async def test_returns_id_and_execution_id(self):
        svc, conn = make_service()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: {
            "id": 42,
            "execution_id": "exec-abc",
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        }[key]
        conn.fetchrow = AsyncMock(return_value=mock_row)

        result = await svc.save_training_example(
            execution_id="exec-abc",
            user_request="Write a blog post about AI",
            intent="content_generation",
            business_state={"topic": "AI"},
            execution_plan={"steps": ["research"]},
            execution_result={"output": "done"},
            quality_score=0.85,
            success=True,
            tags=["production"],
        )

        assert result["id"] == 42
        assert result["execution_id"] == "exec-abc"
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_insert_called_with_correct_args(self):
        svc, conn = make_service()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: {
            "id": 1,
            "execution_id": "exec-123",
            "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        }[key]
        conn.fetchrow = AsyncMock(return_value=mock_row)

        await svc.save_training_example(
            execution_id="exec-123",
            user_request="Test request",
            intent="test",
            business_state={},
            execution_plan={},
            execution_result={},
            quality_score=0.7,
            success=True,
        )

        conn.fetchrow.assert_awaited_once()
        # First positional arg after SQL is execution_id
        call_args = conn.fetchrow.call_args
        assert "exec-123" in call_args[0]


# ---------------------------------------------------------------------------
# get_all_training_data
# ---------------------------------------------------------------------------


class TestGetAllTrainingData:
    @pytest.mark.asyncio
    async def test_returns_list_of_datapoints(self):
        svc, conn = make_service()
        row = make_sample_row()
        conn.fetch = AsyncMock(return_value=[row])

        result = await svc.get_all_training_data(limit=10)

        assert len(result) == 1
        assert isinstance(result[0], TrainingDatapoint)

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_list(self):
        svc, conn = make_service()
        conn.fetch = AsyncMock(return_value=[])

        result = await svc.get_all_training_data()
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_passed_to_query(self):
        svc, conn = make_service()
        conn.fetch = AsyncMock(return_value=[])

        await svc.get_all_training_data(limit=50)

        call_args = conn.fetch.call_args
        assert 50 in call_args[0]


# ---------------------------------------------------------------------------
# add_tags / remove_tags
# ---------------------------------------------------------------------------


class TestAddRemoveTags:
    @pytest.mark.asyncio
    async def test_add_tags_returns_count(self):
        svc, conn = make_service()
        # conn.execute must return a string like "UPDATE 3" (asyncpg status string)
        conn.execute = AsyncMock(return_value="UPDATE 3")

        result = await svc.add_tags(["exec-1", "exec-2", "exec-3"], ["production"])
        assert isinstance(result, int)
        assert result == 3

    @pytest.mark.asyncio
    async def test_remove_tags_returns_count(self):
        svc, conn = make_service()
        # conn.execute returns asyncpg status string "UPDATE N"
        conn.execute = AsyncMock(return_value="UPDATE 2")

        result = await svc.remove_tags(["exec-1", "exec-2"], ["low_quality"])
        assert isinstance(result, int)
        assert result == 2


# ---------------------------------------------------------------------------
# get_statistics
# ---------------------------------------------------------------------------


class TestGetStatistics:
    @pytest.mark.asyncio
    async def test_returns_training_data_stats(self):
        from datetime import datetime, timezone
        svc, conn = make_service()
        # total COUNT(*) fetchval
        conn.fetchval = AsyncMock(return_value=10)

        # get_statistics calls get_all_training_data internally — mock it to return
        # a small list of TrainingDatapoint objects to avoid double-mocking conn.fetch
        from services.training_data_service import TrainingDatapoint
        sample_dp = TrainingDatapoint(
            id="1",
            execution_id="exec-1",
            user_request="Write blog post",
            intent="content_generation",
            business_state={},
            execution_plan={},
            execution_result={},
            quality_score=0.85,
            success=True,
            tags=["production"],
            created_at="2025-03-12T00:00:00+00:00",
        )
        svc.get_all_training_data = AsyncMock(return_value=[sample_dp])

        result = await svc.get_statistics()
        assert isinstance(result, TrainingDataStats)
        assert result.total_examples == 10
        assert result.filtered_count == 1


# ---------------------------------------------------------------------------
# list_datasets / get_dataset
# ---------------------------------------------------------------------------


class TestListGetDatasets:
    @pytest.mark.asyncio
    async def test_list_datasets_returns_list(self):
        svc, conn = make_service()
        conn.fetch = AsyncMock(return_value=[])
        result = await svc.list_datasets()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_dataset_returns_dict_when_found(self):
        svc, conn = make_service()
        mock_row = {"id": 1, "name": "production", "version": 1}
        conn.fetchrow = AsyncMock(return_value=mock_row)
        result = await svc.get_dataset(1)
        assert result is not None
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_dataset_returns_none_when_not_found(self):
        svc, conn = make_service()
        conn.fetchrow = AsyncMock(return_value=None)
        result = await svc.get_dataset(999)
        assert result is None
