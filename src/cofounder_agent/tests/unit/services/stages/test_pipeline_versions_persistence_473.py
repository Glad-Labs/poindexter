"""Regression tests for Glad-Labs/poindexter#473.

Critical data-loss bug discovered 2026-05-11 06:00 UTC: the Prefect-
driven ``content_generation_flow`` (live in prod since the Phase 1+2
cutover landed 669bf1ef on 2026-05-10 ~22:06 UTC) was completing the
content pipeline end-to-end without persisting any draft content to
``pipeline_versions``.

Status transitions and ``pipeline_tasks.error_message`` writes happened,
but the actual ``content``, ``title``, ``excerpt``, ``featured_image_url``,
``quality_score``, ``qa_feedback``, ``models_used_by_phase``, ``stage_data``
were dropped on the floor. Every awaiting_approval row had NULL content;
every rejected row's draft prose was unrecoverable.

Root cause: ``services/pipeline_db.py::upsert_version`` was the only
function in the codebase that wrote to ``pipeline_versions``, and it
had ZERO production callers — the legacy stage chain that used it was
deleted in the 2026-05-09 services audit. Prefect's flow didn't re-wire
the write, so the gap went silent for ~19 hours before the overnight
A/B batch surfaced it.

Fix: ``finalize_task.py`` (awaiting_approval path) and
``cross_model_qa.py`` (rejection path) now both call ``upsert_version``
right after their respective ``update_task`` status transitions. These
tests pin both call sites — if a future edit silently removes the
``upsert_version`` call again, this suite breaks.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_database_service(pool: MagicMock | None = None) -> MagicMock:
    db = MagicMock()
    db.pool = pool or MagicMock()
    db.update_task = AsyncMock()
    db.update_task_status_guarded = AsyncMock(return_value="ok")
    db.mark_model_performance_outcome = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# finalize_task — awaiting_approval path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFinalizeTaskPersistsToPipelineVersions:
    """The terminal awaiting_approval stage must populate pipeline_versions.

    Closes Glad-Labs/poindexter#473.
    """

    @pytest.mark.asyncio
    async def test_finalize_task_calls_upsert_version_with_full_draft(self):
        """All the writer's output reaches pipeline_versions, not just the
        status transition.

        This is the regression that #473 closed. Asserts every field a
        reader / operator UI needs is present in the upsert payload.
        """
        from modules.content.stages.finalize_task import FinalizeTaskStage

        db = _make_database_service()

        captured_upsert: dict = {}

        async def fake_upsert_version(task_id, data):
            captured_upsert["task_id"] = task_id
            captured_upsert["data"] = data

        fake_pipeline_db = MagicMock()
        fake_pipeline_db.upsert_version = AsyncMock(side_effect=fake_upsert_version)

        ctx = {
            "task_id": "task-473-aa",
            "topic": "Why context windows aren't free (2026-05-11 05:55 overnight A #3)",
            "style": "technical",
            "tone": "analyst",
            "content": "# Why Context Windows Aren't Free\n\nBody prose...",
            "category": "ai_ml",
            "target_audience": "indie devs",
            "title": "Why Context Windows Aren't Free",
            "featured_image_url": "https://r2.example/featured.jpg",
            "featured_image_alt": "AI illustration",
            "seo_title": "Context Windows Cost Analysis",
            "seo_description": "The hidden costs of 128k context windows",
            "seo_keywords": ["context windows", "LLM costs", "inference"],
            "quality_score": 88,
            "qa_final_score": 88,
            "qa_approved": True,
            "models_used_by_phase": {"writer": "glm-4.7-5090", "qa": "gemma3:27b"},
            "database_service": db,
            # quality_result is the early QualityAssessment (no
            # format_feedback_text); QA feedback flows from qa_reviews,
            # which cross_model_qa writes to state. (#879)
            "quality_result": SimpleNamespace(overall_score=85),
            "qa_reviews": [
                {
                    "reviewer": "critic", "score": 88, "approved": True,
                    "feedback": "Solid analysis.", "provider": "ollama",
                },
            ],
        }

        with patch(
            "services.pipeline_db.PipelineDB", return_value=fake_pipeline_db,
        ), patch(
            "services.text_utils.normalize_text", side_effect=lambda s: s,
        ), patch(
            "services.excerpt_generator.generate_excerpt",
            return_value="The hidden costs of 128k context windows...",
        ), patch(
            "services.title_generation.strip_qa_batch_suffix",
            side_effect=lambda s: s.split(" (2026-")[0] if " (2026-" in s else s,
        ), patch(
            "services.content_revisions_logger.log_revision",
            new=AsyncMock(),
        ):
            await FinalizeTaskStage().execute(ctx, {})

        fake_pipeline_db.upsert_version.assert_awaited_once()
        assert captured_upsert["task_id"] == "task-473-aa"

        data = captured_upsert["data"]
        # The whole draft must make it onto the version row.
        assert data["title"] == "Why Context Windows Aren't Free"
        assert "# Why Context Windows Aren't Free" in data["content"]
        assert data["featured_image_url"] == "https://r2.example/featured.jpg"
        assert data["seo_title"] == "Context Windows Cost Analysis"
        assert data["seo_description"] == "The hidden costs of 128k context windows"
        assert data["seo_keywords"] == "context windows, LLM costs, inference"
        assert data["quality_score"] == 88
        from modules.content.multi_model_qa import format_qa_feedback_from_reviews
        assert data["qa_feedback"] == format_qa_feedback_from_reviews(
            ctx["qa_reviews"], final_score=88, approved=True,
        )
        assert data["qa_feedback"]  # non-empty — feedback actually persisted
        assert data["models_used_by_phase"] == {
            "writer": "glm-4.7-5090", "qa": "gemma3:27b",
        }
        # Excerpt is derived in-stage; must flow through.
        assert data["excerpt"] == "The hidden costs of 128k context windows..."
        # Stage_data passthrough — operators want the full metadata blob
        # available on the version row for replay / debugging.
        assert "task_metadata" in data
        assert data["task_metadata"]["content_length"] > 0

    @pytest.mark.asyncio
    async def test_finalize_task_generates_preview_token(self):
        """The Grafana approval-queue panel's clickable-title column
        is built from ``app_settings.preview_base_url || '/preview/' ||
        metadata->>'preview_token'``. Historically the preview_token
        was generated by ``services/task_executor.py::_process_loop``
        AFTER the pipeline returned, but the Prefect cutover #410
        short-circuits past that loop entirely — Prefect-orchestrated
        tasks landed in awaiting_approval with no preview_token and
        the dashboard's title links broke.

        Fix: finalize_task generates the token at the terminal stage
        and includes it in task_metadata (which becomes
        ``pipeline_versions.stage_data['metadata']['preview_token']``
        via upsert_version). Both legacy and Prefect orchestrators
        get clickable links.
        """
        from modules.content.stages.finalize_task import FinalizeTaskStage

        db = _make_database_service()
        captured: dict = {}

        async def fake_upsert_version(task_id, data):
            captured["data"] = data

        fake_pipeline_db = MagicMock()
        fake_pipeline_db.upsert_version = AsyncMock(side_effect=fake_upsert_version)

        ctx = {
            "task_id": "task-preview-token",
            "topic": "test topic",
            "content": "# Title\n\nBody.",
            "title": "Test Title",
            "category": "ai_ml",
            "database_service": db,
            "models_used_by_phase": {},
            "quality_result": SimpleNamespace(overall_score=75),
        }

        with patch(
            "services.pipeline_db.PipelineDB", return_value=fake_pipeline_db,
        ), patch(
            "services.text_utils.normalize_text", side_effect=lambda s: s,
        ), patch(
            "services.excerpt_generator.generate_excerpt", return_value="excerpt",
        ), patch(
            "services.title_generation.strip_qa_batch_suffix", side_effect=lambda s: s,
        ), patch(
            "services.content_revisions_logger.log_revision", new=AsyncMock(),
        ):
            await FinalizeTaskStage().execute(ctx, {})

        # task_metadata flows into pipeline_versions.stage_data['metadata'].
        # preview_token must be present and a 32-char hex string (16 bytes).
        task_metadata = captured["data"]["task_metadata"]
        assert "preview_token" in task_metadata, (
            "task_metadata must include preview_token so the Grafana "
            "approval-queue panel can render clickable title links."
        )
        token = task_metadata["preview_token"]
        assert isinstance(token, str) and len(token) == 32 and all(
            c in "0123456789abcdef" for c in token
        ), f"preview_token should be a 32-char hex string, got {token!r}"

    @pytest.mark.asyncio
    async def test_upsert_failure_does_not_break_the_stage(self):
        """If the version-write hiccups (e.g. pool exhausted), the stage
        still returns ok — the status update already committed, and we'd
        rather have the awaiting_approval row land than wedge the entire
        pipeline on a transient write fault.

        Logged at WARNING so the regression is visible in Loki / Grafana.
        """
        from modules.content.stages.finalize_task import FinalizeTaskStage

        db = _make_database_service()
        fake_pipeline_db = MagicMock()
        fake_pipeline_db.upsert_version = AsyncMock(
            side_effect=RuntimeError("pool exhausted"),
        )

        ctx = {
            "task_id": "task-473-failsoft",
            "topic": "test",
            "content": "body",
            "title": "test title",
            "category": "ai_ml",
            "database_service": db,
            "models_used_by_phase": {},
            "quality_result": SimpleNamespace(overall_score=70),
        }

        with patch(
            "services.pipeline_db.PipelineDB", return_value=fake_pipeline_db,
        ), patch(
            "services.text_utils.normalize_text", side_effect=lambda s: s,
        ), patch(
            "services.excerpt_generator.generate_excerpt",
            return_value="excerpt",
        ), patch(
            "services.title_generation.strip_qa_batch_suffix",
            side_effect=lambda s: s,
        ), patch(
            "services.content_revisions_logger.log_revision",
            new=AsyncMock(),
        ):
            result = await FinalizeTaskStage().execute(ctx, {})

        # Stage succeeds despite the upsert hiccup.
        assert result.ok is True
        # And it did try — the call happened, the failure was caught.
        fake_pipeline_db.upsert_version.assert_awaited_once()
        # update_task still got called — the status transition is the
        # authoritative signal, the version row is the "for humans" copy.
        db.update_task.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_upsert_when_status_guard_aborts(self):
        """The status-guard short-circuits before any writes when the
        task was cancelled mid-stage (GH-90). Persistence should follow.

        Pins that we don't accidentally persist a ghost draft for a
        task the sweeper already terminated.
        """
        from modules.content.stages.finalize_task import FinalizeTaskStage

        db = _make_database_service()
        # Status guard returns None = "task is no longer pending/in_progress"
        db.update_task_status_guarded = AsyncMock(return_value=None)

        fake_pipeline_db = MagicMock()
        fake_pipeline_db.upsert_version = AsyncMock()

        ctx = {
            "task_id": "task-473-aborted",
            "topic": "test",
            "content": "body",
            "database_service": db,
            "models_used_by_phase": {},
            "quality_result": SimpleNamespace(overall_score=70),
        }

        with patch(
            "services.pipeline_db.PipelineDB", return_value=fake_pipeline_db,
        ), patch(
            "services.text_utils.normalize_text", side_effect=lambda s: s,
        ), patch(
            "services.excerpt_generator.generate_excerpt",
            return_value="excerpt",
        ), patch(
            "services.title_generation.strip_qa_batch_suffix",
            side_effect=lambda s: s,
        ), patch(
            "services.content_revisions_logger.log_revision",
            new=AsyncMock(),
        ):
            result = await FinalizeTaskStage().execute(ctx, {})

        # Stage halts the workflow per GH-90.
        assert result.ok is False
        # And it never tried to persist — the task is gone.
        fake_pipeline_db.upsert_version.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sanity: upsert_version still has a production caller after this fix
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpsertVersionHasProductionCallers:
    """Lock the bug shut: ``upsert_version`` must remain reachable from
    production code paths, not just tests.

    If a future refactor accidentally severs the call site again, this
    test surfaces the regression at unit-test time instead of at the
    next overnight batch.
    """

    def test_finalize_task_imports_pipeline_db(self):
        """The terminal stage must reference PipelineDB.upsert_version."""
        import inspect

        from modules.content.stages import finalize_task

        source = inspect.getsource(finalize_task)
        assert "PipelineDB" in source, (
            "finalize_task.py must call PipelineDB.upsert_version to "
            "persist drafts to pipeline_versions — see "
            "Glad-Labs/poindexter#473."
        )
        assert "upsert_version" in source, (
            "finalize_task.py must call upsert_version — see "
            "Glad-Labs/poindexter#473."
        )

    def test_qa_persist_imports_pipeline_db_on_rejection(self):
        """The rejection branch must also persist (now via qa.aggregate + _qa_persist).

        cross_model_qa.py was deleted (atom-cutover Plan 5, #355). The DB
        writes migrated to services/atoms/_qa_persist.py.
        """
        import inspect

        from modules.content.atoms import _qa_persist

        source = inspect.getsource(_qa_persist)
        assert "PipelineDB" in source, (
            "services/atoms/_qa_persist.py must call PipelineDB.upsert_version to "
            "persist rejected drafts to pipeline_versions — see "
            "Glad-Labs/poindexter#473 and atom-cutover Plan 5 #355."
        )
        assert "upsert_version" in source
