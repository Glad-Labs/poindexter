"""Smoke tests for the rest of the Phase E stages.

Each new Stage gets a thorough test suite OR a full-port one;
replace_inline_images + source_featured_image are thin wrappers over
legacy functions, so their tests focus on the adapter contract (stage
conforms to Protocol, context-to-legacy-args shape, legacy-result-to-
context-updates mapping) rather than re-testing the legacy bodies.

For the ported stages (generate_seo_metadata, generate_media_scripts,
capture_training_data, finalize_task), we cover the full happy path +
a couple of edge cases per stage.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage
from services.stages.capture_training_data import CaptureTrainingDataStage
from services.stages.finalize_task import FinalizeTaskStage
from services.stages.generate_media_scripts import (
    GenerateMediaScriptsStage,
    _build_scene_prompt,
    _parse_scene_output,
)
from services.stages.generate_seo_metadata import (
    GenerateSeoMetadataStage,
    _normalize_keywords,
)
from services.stages.replace_inline_images import ReplaceInlineImagesStage
from services.stages.source_featured_image import SourceFeaturedImageStage


# ---------------------------------------------------------------------------
# Protocol conformance — all six new stages in one sweep.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage_cls,expected_name,expected_halts", [
    (ReplaceInlineImagesStage, "replace_inline_images", False),
    (SourceFeaturedImageStage, "source_featured_image", False),
    (GenerateSeoMetadataStage, "generate_seo_metadata", True),
    (GenerateMediaScriptsStage, "generate_media_scripts", False),
    (CaptureTrainingDataStage, "capture_training_data", False),
    (FinalizeTaskStage, "finalize_task", True),
])
def test_stage_protocol_conformance(stage_cls, expected_name, expected_halts):
    s = stage_cls()
    assert isinstance(s, Stage)
    assert s.name == expected_name
    assert s.halts_on_failure is expected_halts


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestNormalizeKeywords:
    def test_dedupe_trim_cap(self):
        raw = ["  a ", "b", "", None, "c", "a"] + [f"k{i}" for i in range(15)]
        out = _normalize_keywords(raw)
        assert len(out) == 10
        # Leading whitespace stripped
        assert out[0] == "a"

    def test_string_input(self):
        assert _normalize_keywords("single") == ["single"]
        assert _normalize_keywords("  ") == []

    def test_unknown_type(self):
        assert _normalize_keywords({"dict": "not accepted"}) == []


class TestBuildScenePrompt:
    def test_includes_title_content_and_site_name(self):
        prompt = _build_scene_prompt("My Title", "Content body.", "SiteName")
        assert "My Title" in prompt
        assert "Content body." in prompt
        assert "Full article at SiteName." in prompt
        assert "SHORT:" in prompt

    def test_content_truncated_at_3000_chars(self):
        big = "x" * 5000
        prompt = _build_scene_prompt("T", big, "S")
        # Truncated body appears; the slice size is exactly 3000
        assert "x" * 3000 in prompt
        assert "x" * 3001 not in prompt


class TestParseSceneOutput:
    def test_splits_on_short_marker(self):
        out = (
            "1. Scene one with enough detail to pass the length check\n"
            "2. Scene two with enough detail too\n"
            "\n"
            "SHORT:\n"
            "Here is the short summary narration"
        )
        scenes, short = _parse_scene_output(out, normalize_for_speech=lambda x: x.upper())
        assert len(scenes) == 2
        assert "Scene one" in scenes[0]
        assert short == "HERE IS THE SHORT SUMMARY NARRATION"

    def test_short_missing_returns_empty(self):
        scenes, short = _parse_scene_output(
            "1. Scene one with enough detail to pass the length check",
            normalize_for_speech=lambda x: x,
        )
        assert short == ""
        assert len(scenes) == 1

    def test_strips_numbering_and_quotes(self):
        scenes, _ = _parse_scene_output(
            '1. "Scene with quotes around the whole line here"',
            normalize_for_speech=lambda x: x,
        )
        assert scenes[0] == "Scene with quotes around the whole line here"

    def test_drops_short_lines(self):
        scenes, _ = _parse_scene_output(
            "1. short\n2. This one is long enough to survive the filter check",
            normalize_for_speech=lambda x: x,
        )
        assert len(scenes) == 1
        assert "long enough" in scenes[0]


# ---------------------------------------------------------------------------
# GenerateSeoMetadataStage.execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGenerateSeoMetadata:
    async def test_populates_all_keys(self):
        seo_gen = SimpleNamespace(metadata_gen=SimpleNamespace(
            generate_seo_assets=MagicMock(return_value={
                "seo_title": "My Great Title",
                "meta_description": "A compelling description.",
                "meta_keywords": ["ai", "tools", "productivity", "ai"],
            }),
        ))
        ctx: dict[str, Any] = {"topic": "AI", "content": "body", "tags": []}
        with patch(
            "services.seo_content_generator.get_seo_content_generator",
            return_value=seo_gen,
        ), patch(
            "services.ai_content_generator.get_content_generator",
            MagicMock(),
        ):
            result = await GenerateSeoMetadataStage().execute(ctx, {})
        assert result.ok is True
        u = result.context_updates
        assert u["seo_title"] == "My Great Title"
        assert u["seo_description"] == "A compelling description."
        # De-duplicated + trimmed list
        assert "ai" in u["seo_keywords_list"]
        assert "tools" in u["seo_keywords_list"]
        # Cap at 10
        assert len(u["seo_keywords_list"]) <= 10

    async def test_missing_content_returns_not_ok(self):
        result = await GenerateSeoMetadataStage().execute({"topic": "X"}, {})
        assert result.ok is False

    async def test_none_assets_raises(self):
        seo_gen = SimpleNamespace(metadata_gen=SimpleNamespace(
            generate_seo_assets=MagicMock(return_value=None),
        ))
        with patch(
            "services.seo_content_generator.get_seo_content_generator",
            return_value=seo_gen,
        ), patch(
            "services.ai_content_generator.get_content_generator",
            MagicMock(),
        ):
            with pytest.raises(ValueError, match="invalid result"):
                await GenerateSeoMetadataStage().execute(
                    {"topic": "X", "content": "body"}, {},
                )


# ---------------------------------------------------------------------------
# CaptureTrainingDataStage.execute
# ---------------------------------------------------------------------------


def _fake_quality_result(score: float = 82, passing: bool = True):
    dims = SimpleNamespace(
        clarity=1.0, accuracy=1.0, completeness=1.0, relevance=1.0,
        seo_quality=1.0, readability=1.0, engagement=1.0,
    )
    return SimpleNamespace(
        overall_score=score, passing=passing, dimensions=dims,
        feedback="ok", suggestions=[],
        evaluation_method="pattern",
    )


class _FakeDbForTraining:
    def __init__(self):
        self.qa_calls: list[dict] = []
        self.td_calls: list[dict] = []
        self.qa_raise = False
        self.td_raise = False

    async def create_quality_evaluation(self, payload):
        if self.qa_raise:
            raise RuntimeError("qa boom")
        self.qa_calls.append(payload)

    async def create_orchestrator_training_data(self, payload):
        if self.td_raise:
            raise RuntimeError("td boom")
        self.td_calls.append(payload)


@pytest.mark.asyncio
class TestCaptureTrainingData:
    async def test_writes_both_rows(self):
        db = _FakeDbForTraining()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "style": "s", "tone": "n",
            "target_length": 1200, "tags": ["a"], "content": "some body.",
            "quality_result": _fake_quality_result(score=82),
            "featured_image": object(),
            "database_service": db,
        }
        result = await CaptureTrainingDataStage().execute(ctx, {})
        assert result.ok is True
        assert len(db.qa_calls) == 1
        assert len(db.td_calls) == 1
        # Normalized quality score ∈ [0,1]
        assert 0 <= db.td_calls[0]["quality_score"] <= 1

    async def test_qa_failure_is_non_fatal(self):
        db = _FakeDbForTraining()
        db.qa_raise = True
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "content": "b",
            "quality_result": _fake_quality_result(),
            "database_service": db,
            "target_length": 0, "tags": [],
        }
        result = await CaptureTrainingDataStage().execute(ctx, {})
        assert result.ok is True  # Non-critical.
        assert len(db.qa_calls) == 0
        assert len(db.td_calls) == 1

    async def test_missing_quality_result_returns_not_ok(self):
        db = _FakeDbForTraining()
        result = await CaptureTrainingDataStage().execute(
            {"task_id": "t", "database_service": db}, {},
        )
        assert result.ok is False


# ---------------------------------------------------------------------------
# FinalizeTaskStage.execute
# ---------------------------------------------------------------------------


class _FakeDbForFinalize:
    def __init__(self):
        self.calls: list[dict] = []

    async def update_task(self, task_id, updates):
        self.calls.append({"task_id": task_id, **updates})


@pytest.mark.asyncio
class TestFinalizeTask:
    async def test_persists_awaiting_approval_row(self):
        db = _FakeDbForFinalize()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "style": "s", "tone": "n",
            "content": "body", "seo_title": "Title", "seo_description": "Desc",
            "seo_keywords_list": ["a", "b"],
            "category": "tech", "target_audience": "devs",
            "title": "Display Title",
            "quality_result": _fake_quality_result(score=78),
            "featured_image_url": "https://img",
            "podcast_script": "pod",
            "video_scenes": ["scene1", "scene2"],
            "short_summary_script": "short",
            "database_service": db,
        }
        with patch(
            "services.content_router_service._normalize_text",
            side_effect=lambda x: x,
        ):
            result = await FinalizeTaskStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["status"] == "awaiting_approval"
        assert len(db.calls) == 1
        persisted = db.calls[0]
        assert persisted["status"] == "awaiting_approval"
        assert persisted["approval_status"] == "pending"
        assert persisted["title"] == "Display Title"
        # Legacy-critical: error_message is cleared
        assert persisted["error_message"] is None
        # Final quality score rounded from context's quality_score (not from quality_result)
        assert persisted["quality_score"] == 78
        # task_metadata includes media scripts
        md = persisted["task_metadata"]
        assert md["podcast_script"] == "pod"
        assert md["video_scenes"] == ["scene1", "scene2"]

    async def test_missing_context_returns_not_ok(self):
        result = await FinalizeTaskStage().execute({}, {})
        assert result.ok is False


# ---------------------------------------------------------------------------
# Wrapper stages — adapter contract only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestReplaceInlineImagesWrapper:
    async def test_calls_legacy_with_adapted_context(self):
        async def fake_legacy(db, tid, topic, content, img_svc, result):
            result["inline_images_replaced"] = 2
            result["stages"]["2c_inline_images_replaced"] = True
            return content + "\n[image]"

        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "content": "body",
            "database_service": MagicMock(),
            "image_service": MagicMock(),
            "category": "tech",
        }
        with patch(
            "services.content_router_service._stage_replace_inline_images",
            new=AsyncMock(side_effect=fake_legacy),
        ):
            result = await ReplaceInlineImagesStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["content"] == "body\n[image]"
        assert result.context_updates["inline_images_replaced"] == 2

    async def test_empty_content_skipped(self):
        result = await ReplaceInlineImagesStage().execute(
            {"content": "", "task_id": "t", "database_service": MagicMock()}, {},
        )
        assert result.ok is True
        assert result.metrics.get("skipped") is True


@pytest.mark.asyncio
class TestSourceFeaturedImageWrapper:
    async def test_calls_legacy_and_propagates_result_keys(self):
        async def fake_legacy(topic, tags, gen, svc, result_dict, task_id=None):
            result_dict["featured_image_url"] = "https://img"
            result_dict["featured_image_alt"] = "alt"
            result_dict["image_style"] = "flat vector"
            result_dict["stages"]["3_featured_image_found"] = True
            return SimpleNamespace(url="https://img")

        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T",
            "tags": ["a"], "generate_featured_image": True,
            "image_service": MagicMock(),
        }
        with patch(
            "services.content_router_service._stage_source_featured_image",
            new=AsyncMock(side_effect=fake_legacy),
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["featured_image_url"] == "https://img"
        assert result.context_updates["image_style"] == "flat vector"
        assert result.metrics["has_featured_image"] is True
