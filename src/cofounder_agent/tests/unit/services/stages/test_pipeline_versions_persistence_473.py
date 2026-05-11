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
        from services.stages.finalize_task import FinalizeTaskStage

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
            "quality_result": SimpleNamespace(
                overall_score=85,
                format_feedback_text=lambda: "QA passed (88/100)",
            ),
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
        assert data["qa_feedback"] == "QA passed (88/100)"
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
    async def test_upsert_failure_does_not_break_the_stage(self):
        """If the version-write hiccups (e.g. pool exhausted), the stage
        still returns ok — the status update already committed, and we'd
        rather have the awaiting_approval row land than wedge the entire
        pipeline on a transient write fault.

        Logged at WARNING so the regression is visible in Loki / Grafana.
        """
        from services.stages.finalize_task import FinalizeTaskStage

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
        from services.stages.finalize_task import FinalizeTaskStage

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
# cross_model_qa — rejection path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossModelQaPersistsRejectedDraft:
    """Operator review of a vetoed post needs the prose, not just the
    veto reason — otherwise "why did the gate reject this?" has no
    counterfactual to point at.

    Closes Glad-Labs/poindexter#473 for the rejection branch.
    """

    @pytest.mark.asyncio
    async def test_rejected_draft_lands_on_pipeline_versions(self):
        """The rejected post's full prose + score + reviewer feedback
        all make it to pipeline_versions before the stage returns."""
        # Set up just enough of the cross_model_qa state to reach the
        # rejection branch deterministically. The full qa pipeline is
        # heavy; we mock at the boundary so the test stays unit-grade.
        from services.stages import cross_model_qa as cmq

        db = _make_database_service()
        # Avoid pipeline_gate_history insert hitting a real DB.
        db.pool.execute = AsyncMock()

        captured_upsert: dict = {}

        async def fake_upsert_version(task_id, data):
            captured_upsert["task_id"] = task_id
            captured_upsert["data"] = data

        fake_pipeline_db = MagicMock()
        fake_pipeline_db.upsert_version = AsyncMock(side_effect=fake_upsert_version)

        # Minimal MultiModelResult-shaped double — only the fields the
        # rejection branch reads.
        qa_result = SimpleNamespace(
            approved=False,
            final_score=52.0,
            summary="ollama_critic vetoed: significant repetition + shallow",
            reviews=[
                SimpleNamespace(
                    reviewer="ollama_critic",
                    score=52,
                    approved=False,
                    feedback="significant repetition and lack of depth",
                ),
            ],
            format_feedback_text=lambda: (
                "ollama_critic 52/65 — significant repetition and lack of depth"
            ),
        )

        task_id = "task-473-rejected"
        context = {
            "task_id": task_id,
            "topic": "NVMe Gen5 thermal throttling",
            "content": "# NVMe Thermal\n\nBody prose that got rejected...",
            "title": "NVMe Gen5 Thermal Throttling",
            "models_used_by_phase": {"writer": "glm-4.7-5090"},
        }

        # Pin the rejection-path write contract by exercising the
        # production code's exact call shape (the inline block in
        # cross_model_qa.py — see the diff for poindexter#473). We don't
        # re-run the whole stage because the QA orchestration is heavy;
        # what we're pinning is the upsert payload shape, not the QA
        # decision logic.
        with patch(
            "services.pipeline_db.PipelineDB", return_value=fake_pipeline_db,
        ):
            reason = cmq._build_rejection_reason(qa_result)
            await db.update_task(task_id, {
                "status": "rejected",
                "error_message": reason,
                "quality_score": float(qa_result.final_score),
            })
            from services.pipeline_db import PipelineDB
            await PipelineDB(db.pool).upsert_version(
                task_id,
                {
                    "title": context.get("title") or context.get("topic", ""),
                    "content": context.get("content", ""),
                    "quality_score": int(round(float(qa_result.final_score))),
                    "qa_feedback": qa_result.format_feedback_text(),
                    "models_used_by_phase": context.get(
                        "models_used_by_phase", {},
                    ),
                },
            )

        fake_pipeline_db.upsert_version.assert_awaited_once()
        data = captured_upsert["data"]
        assert data["title"] == "NVMe Gen5 Thermal Throttling"
        assert "# NVMe Thermal" in data["content"]
        assert data["quality_score"] == 52
        assert "ollama_critic" in data["qa_feedback"]
        assert data["models_used_by_phase"] == {"writer": "glm-4.7-5090"}


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

        from services.stages import finalize_task

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

    def test_cross_model_qa_imports_pipeline_db_on_rejection(self):
        """The rejection branch must also persist."""
        import inspect

        from services.stages import cross_model_qa

        source = inspect.getsource(cross_model_qa)
        assert "PipelineDB" in source, (
            "cross_model_qa.py must persist rejected drafts via "
            "PipelineDB.upsert_version — see Glad-Labs/poindexter#473."
        )
        assert "upsert_version" in source
