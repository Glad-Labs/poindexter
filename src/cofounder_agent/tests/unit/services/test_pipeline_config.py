"""
Unit tests for services/pipeline_config.py

Tests DB-driven pipeline stage management, experiment routing, and run logging.
"""

from unittest.mock import AsyncMock

import pytest

from services.pipeline_config import PipelineConfig, PipelineStage


def _make_pool(stages=None, experiment=None):
    pool = AsyncMock()

    if stages is None:
        stages = [
            {"key": "draft", "name": "Drafting", "description": "", "stage_order": 20,
             "enabled": True, "config_json": {"model": "ollama/qwen3.5:35b"}, "ab_test_group": None},
            {"key": "qa", "name": "QA Review", "description": "", "stage_order": 40,
             "enabled": True, "config_json": {"model": "glm-4.7", "threshold": 70}, "ab_test_group": None},
        ]
    pool.fetch = AsyncMock(return_value=stages)
    pool.fetchrow = AsyncMock(return_value=experiment)
    pool.execute = AsyncMock()
    return pool


class TestGetActiveStages:
    async def test_returns_enabled_stages(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        stages = await config.get_active_stages()
        assert len(stages) == 2
        assert stages[0].key == "draft"
        assert stages[1].key == "qa"

    async def test_stages_are_cached(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        await config.get_active_stages()
        await config.get_active_stages()
        # fetch should only be called once (cached)
        pool.fetch.assert_awaited_once()

    async def test_invalidate_cache(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        await config.get_active_stages()
        config.invalidate_cache()
        await config.get_active_stages()
        assert pool.fetch.await_count == 2


class TestGetStageConfig:
    async def test_returns_config_dict(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        stage_config = await config.get_stage_config("draft")
        assert stage_config["model"] == "ollama/qwen3.5:35b"

    async def test_missing_stage_returns_empty(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        stage_config = await config.get_stage_config("nonexistent")
        assert stage_config == {}


class TestIsStageEnabled:
    async def test_enabled_stage(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        assert await config.is_stage_enabled("draft") is True

    async def test_missing_stage_is_false(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        assert await config.is_stage_enabled("nonexistent") is False


class TestExperimentRouting:
    async def test_no_experiment_returns_base_config(self):
        pool = _make_pool(experiment=None)
        config = PipelineConfig(pool)
        stage_config = await config.get_stage_config("qa")
        assert "_experiment_id" not in stage_config

    async def test_experiment_overrides_config(self):
        experiment = {
            "id": 1, "name": "test", "stage_key": "qa",
            "variant_a": {"threshold": 70}, "variant_b": {"threshold": 60},
            "traffic_split_pct": 100,  # Always variant B
            "is_active": True,
        }
        pool = _make_pool(experiment=experiment)
        config = PipelineConfig(pool)
        stage_config = await config.get_stage_config("qa")
        # With 100% traffic to B, should get variant B
        assert stage_config.get("threshold") == 60
        assert stage_config.get("_experiment_id") == 1


class TestLogStageRun:
    async def test_logs_to_database(self):
        pool = _make_pool()
        config = PipelineConfig(pool)
        await config.log_stage_run(
            task_id="test-123", stage_key="draft",
            result="passed", score=75.0, duration_ms=1200,
        )
        pool.execute.assert_awaited_once()

    async def test_log_handles_error(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=Exception("db error"))
        config = PipelineConfig(pool)
        # Should not raise
        await config.log_stage_run(task_id="x", stage_key="y", result="failed")
