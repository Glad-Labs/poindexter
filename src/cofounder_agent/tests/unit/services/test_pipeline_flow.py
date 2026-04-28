"""Tests for services/pipeline_flow.py — the Prefect 3 wrapper around the
content generation pipeline (#206).

These tests focus on the *flow-level* control flow:
- chunks dispatch in order
- early-return halts (topic_decision_gate, cross_model_qa rejection) skip later chunks
- failure path persists metadata + emits webhook

The Stages themselves are tested separately in tests/unit/services/stages/.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.pipeline_flow import content_generation_flow


def _summary(halted_at: str | None = None, detail: str = "") -> SimpleNamespace:
    """Build a fake StageRunner summary."""
    rec = SimpleNamespace(detail=detail)
    return SimpleNamespace(halted_at=halted_at, records=[rec])


def _fake_runner_with_halts(halts: dict[str, str | None]):
    """Build a MagicMock StageRunner whose run_all returns a halt summary
    keyed by the FIRST stage in each call's ``order`` list.

    halts: {"topic_decision_gate": "topic_decision_gate"} → that chunk halts.
    Anything not in halts returns no-halt.
    """
    runner = MagicMock()

    async def _run_all(_ctx, *, order):
        first = order[0] if order else None
        return _summary(halted_at=halts.get(first))

    runner.run_all = AsyncMock(side_effect=_run_all)
    return runner


def _site_config_stub() -> MagicMock:
    sc = MagicMock()
    sc.get = MagicMock(return_value="")
    sc.get_int = MagicMock(return_value=0)
    return sc


def _database_service_stub() -> MagicMock:
    db = MagicMock()
    db.pool = MagicMock()
    db.update_task = AsyncMock(return_value=True)
    return db


@pytest.mark.unit
class TestContentGenerationFlow:
    @pytest.mark.asyncio
    async def test_topic_decision_gate_halt_returns_early(self):
        """Chunk 1 halt at topic_decision_gate → return immediately with
        awaiting_gate marker; chunks 2-5 must NOT run."""
        runner = _fake_runner_with_halts({"topic_decision_gate": "topic_decision_gate"})

        with patch(
            "plugins.stage_runner.StageRunner", return_value=runner,
        ), patch(
            "plugins.registry.get_core_samples", return_value={"stages": []},
        ), patch(
            "services.pipeline_flow.audit_log_bg",
        ), patch(
            "services.pipeline_flow.get_image_service", return_value=MagicMock(),
        ):
            result = await content_generation_flow(
                topic="Why bootstrap a SaaS in 2026",
                style="conversational",
                tone="confident",
                target_length=1500,
                database_service=_database_service_stub(),
                site_config=_site_config_stub(),
            )

        assert result["awaiting_gate"] == "topic_decision"
        # status is set via setdefault — preserves whatever the stages
        # left behind ("pending" out of the gate); the awaiting_gate
        # marker is the operator-visible signal.
        assert result["status"] in ("pending", "in_progress")
        # Only chunk 1 should have fired.
        assert runner.run_all.await_count == 1

    @pytest.mark.asyncio
    async def test_generate_content_halt_marks_failed(self):
        """generate_content can't halt without content — chunk 1 raises,
        which routes through the flow's exception handler and persists
        failure metadata via update_task."""
        runner = _fake_runner_with_halts({"topic_decision_gate": "generate_content"})

        db = _database_service_stub()
        with patch(
            "plugins.stage_runner.StageRunner", return_value=runner,
        ), patch(
            "plugins.registry.get_core_samples", return_value={"stages": []},
        ), patch(
            "services.pipeline_flow.audit_log_bg",
        ), patch(
            "services.pipeline_flow.get_image_service", return_value=MagicMock(),
        ):
            result = await content_generation_flow(
                topic="t",
                style="s",
                tone="t",
                target_length=1500,
                database_service=db,
                site_config=_site_config_stub(),
            )

        assert result["status"] == "failed"
        assert "generate_content" in result["error"]
        # Failure metadata persisted.
        db.update_task.assert_awaited_once()
        update_kwargs = db.update_task.await_args.kwargs
        assert update_kwargs["updates"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_cross_model_qa_rejection_returns_early(self):
        """Chunk 4 halt at cross_model_qa with status=rejected → clean
        early return; chunk 5 must NOT run."""

        runner = MagicMock()
        call_log: list[list[str]] = []

        async def _run_all(ctx, *, order):
            call_log.append(list(order))
            # cross_model_qa halts with rejection
            if order[0] == "cross_model_qa":
                ctx["status"] = "rejected"
                return _summary(halted_at="cross_model_qa")
            return _summary()

        runner.run_all = AsyncMock(side_effect=_run_all)

        with patch(
            "plugins.stage_runner.StageRunner", return_value=runner,
        ), patch(
            "plugins.registry.get_core_samples", return_value={"stages": []},
        ), patch(
            "services.pipeline_flow.audit_log_bg",
        ), patch(
            "services.pipeline_flow.get_image_service", return_value=MagicMock(),
        ), patch(
            "services.gpu_scheduler.gpu",
        ):
            result = await content_generation_flow(
                topic="t",
                style="s",
                tone="t",
                target_length=1500,
                database_service=_database_service_stub(),
                site_config=_site_config_stub(),
            )

        assert result["status"] == "rejected"
        # Chunks 1-4 ran, chunk 5 did not.
        assert any("cross_model_qa" in c for c in call_log)
        assert not any("generate_seo_metadata" in c for c in call_log)

    @pytest.mark.asyncio
    async def test_full_pipeline_runs_all_five_chunks(self):
        """Happy path — no halts. Verify exact chunk order."""

        runner = MagicMock()
        call_log: list[list[str]] = []

        async def _run_all(_ctx, *, order):
            call_log.append(list(order))
            return _summary()

        runner.run_all = AsyncMock(side_effect=_run_all)

        with patch(
            "plugins.stage_runner.StageRunner", return_value=runner,
        ), patch(
            "plugins.registry.get_core_samples", return_value={"stages": []},
        ), patch(
            "services.pipeline_flow.audit_log_bg",
        ), patch(
            "services.pipeline_flow.get_image_service", return_value=MagicMock(),
        ), patch(
            "services.gpu_scheduler.gpu",
        ):
            result = await content_generation_flow(
                topic="t",
                style="s",
                tone="t",
                target_length=1500,
                database_service=_database_service_stub(),
                site_config=_site_config_stub(),
            )

        # 5 chunks in the documented order
        assert len(call_log) == 5
        assert call_log[0][0] == "topic_decision_gate"
        assert call_log[1][0] == "writer_self_review"
        assert call_log[2] == ["source_featured_image"]
        assert call_log[3] == ["cross_model_qa"]
        assert call_log[4][0] == "generate_seo_metadata"
        # status is whatever the stages set; finalize_task usually flips it
        # to 'awaiting_approval' or 'published' — we don't assert here.
        assert result["task_id"]

    @pytest.mark.asyncio
    async def test_database_service_required(self):
        """No database_service → ValueError."""
        with pytest.raises(ValueError, match="DatabaseService is required"):
            await content_generation_flow(
                topic="t",
                style="s",
                tone="t",
                target_length=1500,
                database_service=None,
                site_config=_site_config_stub(),
            )
