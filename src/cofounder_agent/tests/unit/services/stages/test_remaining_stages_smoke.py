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

# Phase H step 5 (GH#95): stages read site_config from the context dict.
_FAKE_SITE_CONFIG = SimpleNamespace(
    get=lambda _k, _d=None: _d if _d is not None else "",
    get_int=lambda _k, _d=0: _d,
    get_float=lambda _k, _d=0.0: _d,
    get_bool=lambda _k, _d=False: _d,
)

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
        ctx: dict[str, Any] = {
            "topic": "AI", "content": "body", "tags": [],
            "site_config": _FAKE_SITE_CONFIG,
        }
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
                    {"topic": "X", "content": "body", "site_config": _FAKE_SITE_CONFIG},
                    {},
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
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.text_utils.normalize_text",
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

    async def test_guard_aborts_when_row_already_cancelled(self):
        """GH-90 AC #3: when the stale-task sweeper flipped the row to
        'failed' mid-pipeline, finalize_task MUST refuse to write
        'awaiting_approval'. The status-guarded update returns None;
        finalize returns ok=False with continue_workflow=False so the
        runner halts and no downstream publish/webhook fires."""

        class _GuardDb:
            def __init__(self):
                self.update_task_calls: list[dict] = []
                self.guarded_calls: list[tuple] = []

            async def update_task(self, task_id, updates):
                # MUST NOT be called when the guard blocks.
                self.update_task_calls.append({"task_id": task_id, **updates})

            async def update_task_status_guarded(
                self, task_id, new_status, allowed_from=("in_progress", "pending"),
                **fields,
            ):
                self.guarded_calls.append((task_id, new_status, allowed_from))
                # Simulate the sweeper having flipped status → 'failed'.
                return None

        db = _GuardDb()
        ctx: dict[str, Any] = {
            "task_id": "t-ghost", "topic": "T", "style": "s", "tone": "n",
            "content": "body", "seo_title": "Title", "seo_description": "Desc",
            "seo_keywords_list": ["a"], "category": "tech",
            "target_audience": "devs", "title": "T",
            "quality_result": _fake_quality_result(score=82),
            "database_service": db,
            "site_config": _FAKE_SITE_CONFIG,
        }
        with (
            patch("services.text_utils.normalize_text", side_effect=lambda x: x),
            patch(
                "services.excerpt_generator.generate_excerpt",
                return_value="excerpt",
            ),
        ):
            result = await FinalizeTaskStage().execute(ctx, {})

        # The guard was consulted with the expected arguments.
        assert db.guarded_calls == [
            ("t-ghost", "awaiting_approval", ("in_progress", "pending")),
        ]
        # The terminal write was aborted — no update_task happened.
        assert db.update_task_calls == []
        # Stage result explicitly halts the workflow.
        assert result.ok is False
        assert result.continue_workflow is False
        assert "GH-90" in result.detail
        assert result.metrics["aborted_by_status_guard"] is True

    async def test_guard_allows_persist_when_still_in_progress(self):
        """Happy path: guard returns 'in_progress' so finalize proceeds
        and the awaiting_approval row is persisted via update_task."""

        class _GuardDb:
            def __init__(self):
                self.update_task_calls: list[dict] = []
                self.guarded_calls: list[tuple] = []

            async def update_task(self, task_id, updates):
                self.update_task_calls.append({"task_id": task_id, **updates})

            async def update_task_status_guarded(
                self, task_id, new_status, allowed_from=("in_progress", "pending"),
                **fields,
            ):
                self.guarded_calls.append((task_id, new_status, allowed_from))
                return "in_progress"  # still live

        db = _GuardDb()
        ctx: dict[str, Any] = {
            "task_id": "t-live", "topic": "T", "style": "s", "tone": "n",
            "content": "body", "seo_title": "Title", "seo_description": "Desc",
            "seo_keywords_list": ["a"], "category": "tech",
            "target_audience": "devs", "title": "T",
            "quality_result": _fake_quality_result(score=82),
            "database_service": db,
            "site_config": _FAKE_SITE_CONFIG,
        }
        with (
            patch("services.text_utils.normalize_text", side_effect=lambda x: x),
            patch(
                "services.excerpt_generator.generate_excerpt",
                return_value="excerpt",
            ),
        ):
            result = await FinalizeTaskStage().execute(ctx, {})

        assert result.ok is True
        assert len(db.guarded_calls) == 1
        # update_task ran exactly once, persisting awaiting_approval.
        assert len(db.update_task_calls) == 1
        assert db.update_task_calls[0]["status"] == "awaiting_approval"

    async def test_guard_fallback_when_db_service_missing_method(self):
        """Backwards compat: if database_service doesn't expose
        update_task_status_guarded (older deployments that haven't
        applied migration 0071), finalize_task falls back to the legacy
        update_task path — no regression for operators mid-upgrade."""

        class _LegacyDb:
            def __init__(self):
                self.update_task_calls: list[dict] = []

            async def update_task(self, task_id, updates):
                self.update_task_calls.append({"task_id": task_id, **updates})
            # NO update_task_status_guarded — simulates a legacy deployment.

        db = _LegacyDb()
        ctx: dict[str, Any] = {
            "task_id": "t-legacy", "topic": "T", "style": "s", "tone": "n",
            "content": "body", "seo_title": "T", "seo_description": "D",
            "seo_keywords_list": [], "category": "c", "target_audience": "d",
            "title": "T",
            "quality_result": _fake_quality_result(score=80),
            "database_service": db,
            "site_config": _FAKE_SITE_CONFIG,
        }
        with (
            patch("services.text_utils.normalize_text", side_effect=lambda x: x),
            patch(
                "services.excerpt_generator.generate_excerpt",
                return_value="e",
            ),
        ):
            result = await FinalizeTaskStage().execute(ctx, {})
        assert result.ok is True
        assert len(db.update_task_calls) == 1


# ---------------------------------------------------------------------------
# Wrapper stages — adapter contract only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestReplaceInlineImagesAdapter:
    """Direct-port behavior tests. Mocks are at the external-dep boundary."""

    async def test_empty_content_skipped(self):
        result = await ReplaceInlineImagesStage().execute(
            {"content": "", "task_id": "t", "database_service": MagicMock()}, {},
        )
        assert result.ok is True
        assert result.metrics.get("skipped") is True

    async def test_missing_task_id_returns_not_ok(self):
        result = await ReplaceInlineImagesStage().execute(
            {"content": "body"}, {},
        )
        assert result.ok is False
        assert "task_id" in result.detail

    async def test_no_placeholders_and_no_agent_plan_leaves_content_alone(self):

        # Agent returns no suggestions → placeholders remain empty
        async def no_plan(*_a, **_kw):
            return SimpleNamespace(images=[], featured_image=None)

        db = MagicMock()
        db.update_task = AsyncMock()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "content": "no placeholders here",
            "database_service": db, "image_service": MagicMock(),
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.image_decision_agent.plan_images",
            new=AsyncMock(side_effect=no_plan),
        ):
            result = await ReplaceInlineImagesStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["stages"]["2c_inline_images_replaced"] is False
        # db.update_task not called — no content change
        db.update_task.assert_not_called()

    async def test_placeholder_gets_replaced_via_pexels_fallback(self):
        # Force SDXL to fail (by making the helper return None), verify
        # Pexels fallback takes over and the placeholder becomes an <img>.
        img_obj = SimpleNamespace(url="https://pexels.example/cat.jpg", photographer="Jane")
        image_service = SimpleNamespace(search_featured_image=AsyncMock(return_value=img_obj))

        db = MagicMock()
        db.update_task = AsyncMock()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "Cats", "content": "Intro\n\n[IMAGE-1: cat]\n\nOutro",
            "database_service": db, "image_service": image_service,
            "site_config": _FAKE_SITE_CONFIG,
        }

        # Stage calls _normalize_text via lazy import into content_router_service
        with patch(
            "services.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value=None),
        ), patch(
            "services.text_utils.normalize_text", side_effect=lambda x: x,
        ):
            result = await ReplaceInlineImagesStage().execute(ctx, {})
        assert result.ok is True
        body = result.context_updates["content"]
        assert "pexels.example/cat.jpg" in body
        assert "Jane" in body
        assert "[IMAGE-1" not in body
        assert result.context_updates["inline_images_replaced"] == 1

    async def test_placeholder_stripped_when_both_strategies_fail(self):
        db = MagicMock()
        db.update_task = AsyncMock()
        image_service = SimpleNamespace(
            search_featured_image=AsyncMock(return_value=None),
        )
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "X",
            "content": "Intro [IMAGE-1: something] outro.",
            "database_service": db, "image_service": image_service,
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value=None),
        ), patch(
            "services.text_utils.normalize_text", side_effect=lambda x: x,
        ):
            result = await ReplaceInlineImagesStage().execute(ctx, {})
        assert result.ok is True
        body = result.context_updates["content"]
        assert "[IMAGE-1" not in body
        assert result.context_updates["inline_images_replaced"] == 0


class TestReplaceInlineImagesPureHelpers:
    def test_cleanup_leaked_descriptions_strips_italic_scene(self):
        from services.stages.replace_inline_images import _cleanup_leaked_descriptions
        body = (
            "<img src='x' />\n"
            "\n*An editorial illustration of a mountain range stretching into horizon*\n"
            "\nReal paragraph."
        )
        out = _cleanup_leaked_descriptions(body)
        assert "editorial illustration" not in out
        assert "Real paragraph." in out

    def test_cleanup_strips_photo_attribution_lines(self):
        from services.stages.replace_inline_images import _cleanup_leaked_descriptions
        body = "Hi\n\n*Photo by Jane on Pexels*\n\nMore."
        out = _cleanup_leaked_descriptions(body)
        assert "Photo by Jane" not in out
        assert "More." in out

    def test_inject_html_image_substitutes_exactly_one(self):
        from services.stages.replace_inline_images import _inject_html_image
        body = "Start [IMAGE-1: desc] middle [IMAGE-1: other] end"
        out = _inject_html_image(body, "1", "http://img", "alt", width=100, height=100)
        # Only the first should be replaced
        assert out.count("<img") == 1
        assert "[IMAGE-1: other]" in out


@pytest.mark.asyncio
class TestReplaceInlineImagesAltTextScrub:
    """Gitea #240 — alt text must not leak the ``||source:style||`` planner hint."""

    async def _run_with_pexels(self, desc: str) -> str:
        """Drive one placeholder through the Pexels fallback, return the rendered alt."""
        pexels_img = SimpleNamespace(url="https://pex.example/x.jpg", photographer="Jane")
        image_service = SimpleNamespace(
            search_featured_image=AsyncMock(return_value=pexels_img),
        )
        db = MagicMock()
        db.update_task = AsyncMock()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "X",
            "content": f"Intro [IMAGE-1: {desc}] outro.",
            "database_service": db, "image_service": image_service,
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value=None),
        ), patch(
            "services.text_utils.normalize_text", side_effect=lambda x: x,
        ):
            result = await ReplaceInlineImagesStage().execute(ctx, {})
        body = result.context_updates["content"]
        # Extract alt="..." value
        import re as _re
        m = _re.search(r'alt="([^"]*)"', body)
        return m.group(1) if m else ""

    async def test_strips_trailing_source_hint(self):
        alt = await self._run_with_pexels("A server room. ||pexels:real-world objects||")
        assert "pexels" not in alt.lower()
        assert "A server room." in alt

    async def test_strips_sdxl_hint_too(self):
        alt = await self._run_with_pexels("Cinematic blueprint ||sdxl:editorial||")
        assert "sdxl" not in alt.lower()
        assert "Cinematic blueprint" in alt

    async def test_preserves_normal_alt(self):
        alt = await self._run_with_pexels("Just a simple description")
        assert alt == "Just a simple description"


@pytest.mark.asyncio
class TestSourceFeaturedImageAdapter:
    """Direct-port behavior tests after wrapper removal."""

    async def test_disabled_flag_skips_without_calls(self):
        image_service = SimpleNamespace(
            sdxl_available=False, sdxl_initialized=True,
            search_featured_image=AsyncMock(),
        )
        ctx: dict[str, Any] = {
            "topic": "T", "tags": [], "generate_featured_image": False,
            "image_service": image_service,
            "site_config": _FAKE_SITE_CONFIG,
        }
        result = await SourceFeaturedImageStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["stages"]["3_featured_image_found"] is False
        image_service.search_featured_image.assert_not_called()

    async def test_pexels_fallback_when_sdxl_unavailable(self):
        pexels_img = SimpleNamespace(
            url="https://pex.example/photo.jpg", photographer="Alex",
            source="pexels", width=800, height=600,
        )
        image_service = SimpleNamespace(
            sdxl_available=False, sdxl_initialized=True,  # sdxl not attempted
            search_featured_image=AsyncMock(return_value=pexels_img),
        )
        ctx: dict[str, Any] = {
            "topic": "Cats", "tags": ["kittens"],
            "generate_featured_image": True, "image_service": image_service,
            "site_config": _FAKE_SITE_CONFIG,
        }
        result = await SourceFeaturedImageStage().execute(ctx, {})
        assert result.ok is True
        u = result.context_updates
        assert u["featured_image_url"] == "https://pex.example/photo.jpg"
        assert u["featured_image_photographer"] == "Alex"
        assert u["stages"]["3_featured_image_found"] is True
        assert u["stages"]["3_image_source"] == "pexels"

    async def test_sdxl_succeeds_populates_sdxl_source(self):
        from services.stages.source_featured_image import GeneratedImage
        image_service = SimpleNamespace(
            sdxl_available=True, sdxl_initialized=True,
            search_featured_image=AsyncMock(),
        )
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "AI", "tags": [],
            "generate_featured_image": True, "image_service": image_service,
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=GeneratedImage(
                url="https://r2.example/featured.jpg",
                photographer="AI Generated (SDXL)",
                source="sdxl_local",
            )),
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})
        u = result.context_updates
        assert u["featured_image_url"] == "https://r2.example/featured.jpg"
        assert u["featured_image_source"] == "sdxl_local"
        assert u["stages"]["3_image_source"] == "sdxl"
        # Pexels not consulted when SDXL wins
        image_service.search_featured_image.assert_not_called()

    async def test_both_strategies_fail_records_not_found(self):
        image_service = SimpleNamespace(
            sdxl_available=True, sdxl_initialized=True,
            search_featured_image=AsyncMock(return_value=None),
        )
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "X", "tags": [],
            "generate_featured_image": True, "image_service": image_service,
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=None),
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["stages"]["3_featured_image_found"] is False
        assert result.context_updates["featured_image"] is None
