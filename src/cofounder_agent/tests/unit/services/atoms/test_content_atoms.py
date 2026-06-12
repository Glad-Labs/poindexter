"""Tests for the content.* atoms introduced by poindexter#362.

Covers:
- content.generate_draft (TestContentGenerateDraft)
- content.normalize_draft (TestContentNormalizeDraft)
- content.plan_image_markers (TestContentPlanImageMarkers)
- content.inject_images (TestContentInjectImages)
- content.compile_meta (TestContentCompileMeta)
- content.persist_task (TestContentPersistTask)

No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
auto-marks coroutine tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(pool=None):
    db = MagicMock()
    db.pool = pool or MagicMock()
    db.update_task = AsyncMock(return_value=None)
    db.update_task_status_guarded = AsyncMock(return_value="ok")
    return db


def _base_state(**extra):
    """Minimal pipeline state for tests."""
    state = {
        "task_id": "test-task-1",
        "topic": "asyncio best practices",
        "style": "technical",
        "tone": "informative",
        "tags": ["asyncio", "python"],
        "content": "## Introduction\n\nAsync Python is great.\n\n## Why async\n\nIt helps concurrency.",
        "title": "Asyncio Best Practices",
        "seo_title": "Asyncio Best Practices for Python Devs",
        "database_service": _make_db(),
        "site_config": MagicMock(),
        "models_by_phase": {},
    }
    state.update(extra)
    return state


# ---------------------------------------------------------------------------
# TestContentGenerateDraft
# ---------------------------------------------------------------------------


class TestContentGenerateDraft:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_generate_draft import ATOM_META
        assert ATOM_META.name == "content.generate_draft"
        assert "task_id" in ATOM_META.requires
        assert "content" in ATOM_META.produces
        assert ATOM_META.cost_class == "api"
        assert "llm_call" in ATOM_META.side_effects
        assert "db_write" in ATOM_META.side_effects

    async def test_run_delegates_to_stage(self, monkeypatch):
        from modules.content.atoms import content_generate_draft as atom
        from plugins.stage import StageResult

        fake_result = StageResult(
            ok=True,
            detail="500 chars via gemma3:27b",
            context_updates={
                "content": "Generated body",
                "research_context": "research",
                "model_used": "gemma3:27b",
                "models_used_by_phase": {"generate_content": "gemma3:27b"},
                "generate_metrics": {"cost_log": None},
                "stages": {"2_content_generated": True},
            },
            metrics={"content_length": 500},
        )

        mock_stage = MagicMock()
        mock_stage.execute = AsyncMock(return_value=fake_result)

        import modules.content.stages.generate_content as gc_mod
        monkeypatch.setattr(
            gc_mod, "GenerateContentStage", lambda: mock_stage,
        )

        state = _base_state()
        out = await atom.run(state)
        assert out["content"] == "Generated body"
        assert out["model_used"] == "gemma3:27b"
        assert "generate_metrics" in out

    async def test_run_raises_on_stage_failure(self, monkeypatch):
        import pytest

        from modules.content.atoms import content_generate_draft as atom
        from plugins.stage import StageResult

        fail_result = StageResult(ok=False, detail="no content produced")
        mock_stage = MagicMock()
        mock_stage.execute = AsyncMock(return_value=fail_result)

        import modules.content.stages.generate_content as gc_mod
        monkeypatch.setattr(gc_mod, "GenerateContentStage", lambda: mock_stage)

        with pytest.raises(RuntimeError, match="content.generate_draft failed"):
            await atom.run(_base_state())


# ---------------------------------------------------------------------------
# TestContentNormalizeDraft
# ---------------------------------------------------------------------------


class TestContentNormalizeDraft:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_normalize_draft import ATOM_META
        assert ATOM_META.name == "content.normalize_draft"
        assert ATOM_META.idempotent is True
        assert ATOM_META.cost_class == "free"
        assert ATOM_META.side_effects == ()

    async def test_strips_leaked_image_prompts(self, monkeypatch):
        from modules.content.atoms import content_normalize_draft as atom
        monkeypatch.setattr(
            "services.text_utils.normalize_text", lambda t: t, raising=False
        )
        monkeypatch.setattr(
            "services.text_utils.scrub_fabricated_links",
            lambda t, known_slugs=None: t, raising=False
        )
        content = "## Section\n\n*A dramatic scene of fire and ice, vivid and detailed*\n\nReal body text."
        state = _base_state(content=content)
        out = await atom.run(state)
        assert "*A dramatic scene" not in out["content"]
        assert "Real body text." in out["content"]

    async def test_strips_image_figure_placeholders(self, monkeypatch):
        from modules.content.atoms import content_normalize_draft as atom
        monkeypatch.setattr("services.text_utils.normalize_text", lambda t: t, raising=False)
        monkeypatch.setattr(
            "services.text_utils.scrub_fabricated_links",
            lambda t, known_slugs=None: t, raising=False,
        )
        content = "Body text. [IMAGE-1: a futuristic server room]. More text."
        out = await atom.run(_base_state(content=content))
        assert "[IMAGE-1:" not in out["content"]

    async def test_empty_content_returns_empty(self):
        from modules.content.atoms import content_normalize_draft as atom
        assert await atom.run(_base_state(content="")) == {}

    def test_strip_leaked_image_prompts_pure(self):
        from modules.content.atoms.content_normalize_draft import strip_leaked_image_prompts
        result = strip_leaked_image_prompts("Hello\n[FIGURE: some description]\nWorld")
        assert "[FIGURE:" not in result
        assert "Hello" in result
        assert "World" in result


# ---------------------------------------------------------------------------
# TestContentPlanImageMarkers
# ---------------------------------------------------------------------------


class TestContentPlanImageMarkers:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_plan_image_markers import ATOM_META
        assert ATOM_META.name == "content.plan_image_markers"
        assert "content" in ATOM_META.requires
        assert "image_plans" in ATOM_META.produces

    async def test_parses_existing_markers(self, monkeypatch):
        from modules.content.atoms import content_plan_image_markers as atom
        # Patch VRAM guard so it's a no-op.
        monkeypatch.setattr(
            "modules.content.atoms.content_plan_image_markers.maybe_unload_writer_before_sdxl",
            AsyncMock(),
            raising=False,
        )
        # Patch the import inside the function.
        with patch(
            "services.llm_providers.ollama_unload.maybe_unload_writer_before_sdxl",
            AsyncMock(),
        ):
            content = "## Intro\n\n[IMAGE-1: a blue server]\n\n## Body\n\n[IMAGE-2: a graph]\n\nText."
            state = _base_state(content=content)
            out = await atom.run(state)
        assert len(out["image_plans"]) == 2
        assert out["image_plans"][0]["num"] == "1"
        assert out["image_plans"][1]["num"] == "2"

    async def test_no_content_returns_empty(self):
        from modules.content.atoms import content_plan_image_markers as atom
        assert await atom.run(_base_state(content="")) == {}

    async def test_calls_image_agent_when_no_markers(self, monkeypatch):
        from modules.content.atoms import content_plan_image_markers as atom

        async def _fake_unload(*a, **kw):
            pass

        async def _fake_plan(content_text, topic, category, *, site_config):
            # Inject a marker and return it.
            injected = content_text + "\n[IMAGE-1: test image]\n"
            return injected, None

        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._plan_and_inject_placeholders",
            _fake_plan,
        )
        with patch(
            "services.llm_providers.ollama_unload.maybe_unload_writer_before_sdxl",
            AsyncMock(),
        ):
            out = await atom.run(_base_state(content="## Section\n\nNo markers here."))
        assert len(out["image_plans"]) == 1
        assert out["image_plans"][0]["num"] == "1"


# ---------------------------------------------------------------------------
# TestContentInjectImages
# ---------------------------------------------------------------------------


class TestContentInjectImages:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_inject_images import ATOM_META
        assert ATOM_META.name == "content.inject_images"
        assert ATOM_META.idempotent is True
        assert "db_write" in ATOM_META.side_effects

    async def test_injects_sdxl_image(self):
        from modules.content.atoms import content_inject_images as atom
        content = "## Section\n\n[IMAGE-1: a futuristic city]\n\nBody."
        image_results = [{"num": "1", "url": "https://r2.example.com/img.png", "alt_text": "futuristic city", "source": "sdxl"}]
        state = _base_state(content=content, image_results=image_results)
        out = await atom.run(state)
        assert "[IMAGE-1:" not in out["content"]
        assert "https://r2.example.com/img.png" in out["content"]
        assert out["inline_images_replaced"] == 1

    async def test_strips_unresolved_placeholder(self):
        from modules.content.atoms import content_inject_images as atom
        content = "## Section\n\n[IMAGE-1: a thing]\n\nBody."
        image_results = [{"num": "1", "url": None, "alt_text": "", "source": "none"}]
        state = _base_state(content=content, image_results=image_results)
        out = await atom.run(state)
        assert "[IMAGE-1:" not in out["content"]
        assert out["inline_images_replaced"] == 0

    async def test_injects_pexels_image(self):
        from modules.content.atoms import content_inject_images as atom
        content = "[IMAGE-2: mountain landscape]\n\nSome text."
        image_results = [{"num": "2", "url": "https://pexels.com/photo.jpg", "alt_text": "Photo by Jane", "source": "pexels"}]
        state = _base_state(content=content, image_results=image_results)
        out = await atom.run(state)
        assert "[IMAGE-2:" not in out["content"]
        assert "https://pexels.com/photo.jpg" in out["content"]
        assert out["inline_images_replaced"] == 1

    async def test_empty_image_results_no_op(self):
        from modules.content.atoms import content_inject_images as atom
        content = "Some body without images."
        state = _base_state(content=content, image_results=[])
        out = await atom.run(state)
        assert out["inline_images_replaced"] == 0


# ---------------------------------------------------------------------------
# TestContentCompileMeta
# ---------------------------------------------------------------------------


class TestContentCompileMeta:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_compile_meta import ATOM_META
        assert ATOM_META.name == "content.compile_meta"
        assert ATOM_META.idempotent is True
        assert ATOM_META.cost_class == "free"
        assert ATOM_META.side_effects == ()
        assert "excerpt" in ATOM_META.produces
        assert "preview_token" in ATOM_META.produces

    async def test_produces_all_output_keys(self, monkeypatch):
        from modules.content.atoms import content_compile_meta as atom

        monkeypatch.setattr("services.text_utils.normalize_text", lambda t: t, raising=False)
        monkeypatch.setattr(
            "services.excerpt_generator.generate_excerpt",
            lambda title, content: "Short excerpt.",
            raising=False,
        )
        monkeypatch.setattr(
            "modules.content.multi_model_qa.format_qa_feedback_from_reviews",
            lambda reviews, final_score=None, approved=None: "",
            raising=False,
        )

        state = _base_state(
            content="## Intro\n\nBody text here.\n\n## Conclusion\n\nFinal words.",
            quality_score=85.0,
        )
        out = await atom.run(state)
        assert "excerpt" in out
        assert out["excerpt"] == "Short excerpt."
        assert "quality_score" in out
        assert out["quality_score"] == 85
        assert "preview_token" in out
        assert len(out["preview_token"]) == 32  # hex(16) = 32 chars
        assert "qa_feedback_formatted" in out

    async def test_reuses_existing_preview_token(self, monkeypatch):
        from modules.content.atoms import content_compile_meta as atom
        monkeypatch.setattr("services.text_utils.normalize_text", lambda t: t, raising=False)
        monkeypatch.setattr("services.excerpt_generator.generate_excerpt", lambda **kw: "x", raising=False)
        monkeypatch.setattr(
            "modules.content.multi_model_qa.format_qa_feedback_from_reviews",
            lambda *a, **kw: "",
            raising=False,
        )
        existing = "deadbeef" * 4  # 32-char hex
        state = _base_state(preview_token=existing)
        out = await atom.run(state)
        assert out["preview_token"] == existing

    async def test_quality_score_fallback_to_zero(self, monkeypatch):
        from modules.content.atoms import content_compile_meta as atom
        monkeypatch.setattr("services.text_utils.normalize_text", lambda t: t, raising=False)
        monkeypatch.setattr("services.excerpt_generator.generate_excerpt", lambda **kw: "x", raising=False)
        monkeypatch.setattr(
            "modules.content.multi_model_qa.format_qa_feedback_from_reviews",
            lambda *a, **kw: "",
            raising=False,
        )
        state = _base_state()
        state.pop("quality_score", None)
        state["quality_result"] = None
        out = await atom.run(state)
        assert out["quality_score"] == 0


# ---------------------------------------------------------------------------
# TestContentPersistTask
# ---------------------------------------------------------------------------


class TestContentPersistTask:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_persist_task import ATOM_META
        assert ATOM_META.name == "content.persist_task"
        assert "db_write" in ATOM_META.side_effects
        assert "task_id" in ATOM_META.requires
        assert "status" in ATOM_META.produces
        assert ATOM_META.retry.max_attempts == 3

    async def test_writes_awaiting_approval(self, monkeypatch):
        from modules.content.atoms import content_persist_task as atom

        monkeypatch.setattr("services.text_utils.normalize_text", lambda t: t, raising=False)
        monkeypatch.setattr(
            "services.title_generation.strip_qa_batch_suffix",
            lambda t: t,
            raising=False,
        )
        monkeypatch.setattr(
            "services.pipeline_db.PipelineDB",
            MagicMock(return_value=MagicMock(upsert_version=AsyncMock())),
            raising=False,
        )
        monkeypatch.setattr(
            "services.content_revisions_logger.log_revision",
            AsyncMock(),
            raising=False,
        )

        db = _make_db()
        state = _base_state(database_service=db, quality_score=80.0, excerpt="An excerpt.")
        out = await atom.run(state)
        assert out["status"] == "awaiting_approval"
        assert out["approval_status"] == "pending"
        assert out["post_id"] is None
        db.update_task.assert_called_once()

    async def test_raises_when_status_guard_returns_none(self, monkeypatch):
        import pytest

        from modules.content.atoms import content_persist_task as atom

        monkeypatch.setattr("services.text_utils.normalize_text", lambda t: t, raising=False)
        monkeypatch.setattr("services.title_generation.strip_qa_batch_suffix", lambda t: t, raising=False)

        db = _make_db()
        db.update_task_status_guarded = AsyncMock(return_value=None)  # Guard blocks

        with pytest.raises(RuntimeError, match="race with stale-task sweeper"):
            await atom.run(_base_state(database_service=db))

    async def test_missing_task_id_raises(self):
        import pytest

        from modules.content.atoms import content_persist_task as atom
        state = _base_state()
        state.pop("task_id")
        with pytest.raises(ValueError):
            await atom.run(state)
