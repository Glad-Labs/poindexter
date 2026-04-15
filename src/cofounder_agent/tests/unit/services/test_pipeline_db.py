"""Tests for PipelineDB — the pipeline table write module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.pipeline_db import PipelineDB


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


class TestAddReview:
    @pytest.mark.asyncio
    async def test_add_review(self, pdb, mock_pool):
        await pdb.add_review("test-123", "approved", reviewer="operator", feedback="LGTM")
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_review_handles_error(self, pdb, mock_pool):
        mock_pool.execute = AsyncMock(side_effect=Exception("DB down"))
        await pdb.add_review("test-123", "rejected")


class TestAddDistribution:
    @pytest.mark.asyncio
    async def test_add_distribution(self, pdb, mock_pool):
        await pdb.add_distribution("test-123", "gladlabs.io", post_slug="test-post")
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_distribution_handles_error(self, pdb, mock_pool):
        mock_pool.execute = AsyncMock(side_effect=Exception("DB down"))
        await pdb.add_distribution("test-123", "dev.to")
