"""Unit tests for ``services/stages/quality_evaluation.py``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage
from services.site_config import SiteConfig
from services.stages.quality_evaluation import QualityEvaluationStage


def _sc() -> SiteConfig:
    """Fresh SiteConfig for Phase H DI (GH#95)."""
    return SiteConfig()


def _fake_quality_result(score: float = 82.5, passing: bool = True, truncated: bool = False):
    dims = SimpleNamespace(
        clarity=1.0, accuracy=1.0, completeness=1.0, relevance=1.0,
        seo_quality=1.0, readability=1.0, engagement=1.0,
    )
    return SimpleNamespace(
        overall_score=score, passing=passing,
        truncation_detected=truncated, dimensions=dims,
    )


class TestProtocol:
    def test_conforms(self):
        assert isinstance(QualityEvaluationStage(), Stage)

    def test_metadata(self):
        s = QualityEvaluationStage()
        assert s.name == "quality_evaluation"
        assert s.halts_on_failure is True


@pytest.mark.asyncio
class TestExecute:
    async def test_populates_all_expected_keys(self):
        fake_svc = SimpleNamespace(
            evaluate=AsyncMock(return_value=_fake_quality_result(score=91, passing=True)),
        )
        ctx: dict[str, Any] = {
            "topic": "AI", "tags": ["AI"], "content": "body text",
            "database_service": MagicMock(), "site_config": _sc(),
        }
        with patch(
            "services.quality_service.UnifiedQualityService",
            return_value=fake_svc,
        ):
            result = await QualityEvaluationStage().execute(ctx, {})
        assert result.ok is True
        u = result.context_updates
        assert u["quality_score"] == 91
        assert u["quality_passing"] is True
        assert u["truncation_detected"] is False
        assert u["stages"]["2b_quality_evaluated_initial"] is True
        assert "quality_details_initial" in u
        assert "quality_result" in u

    async def test_empty_content_returns_not_ok(self):
        ctx: dict[str, Any] = {"topic": "X", "content": "", "database_service": MagicMock(), "site_config": _sc()}
        result = await QualityEvaluationStage().execute(ctx, {})
        assert result.ok is False
        assert "content" in result.detail

    async def test_none_result_raises_valueerror(self):
        fake_svc = SimpleNamespace(evaluate=AsyncMock(return_value=None))
        ctx: dict[str, Any] = {
            "topic": "AI", "content": "body", "database_service": MagicMock(), "site_config": _sc(),
        }
        with patch(
            "services.quality_service.UnifiedQualityService",
            return_value=fake_svc,
        ):
            with pytest.raises(ValueError, match="no result produced"):
                await QualityEvaluationStage().execute(ctx, {})

    async def test_uses_topic_as_keyword_when_tags_empty(self):
        fake_svc = SimpleNamespace(evaluate=AsyncMock(return_value=_fake_quality_result()))
        ctx: dict[str, Any] = {
            "topic": "some-topic", "content": "body",
            "database_service": MagicMock(), "tags": [], "site_config": _sc(),
        }
        with patch(
            "services.quality_service.UnifiedQualityService",
            return_value=fake_svc,
        ):
            await QualityEvaluationStage().execute(ctx, {})
        # quality_service.evaluate was called with keywords=["some-topic"]
        call_kwargs = fake_svc.evaluate.call_args.kwargs
        assert call_kwargs["context"]["keywords"] == ["some-topic"]
