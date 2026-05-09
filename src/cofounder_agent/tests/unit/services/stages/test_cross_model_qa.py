"""Unit tests for ``services/stages/cross_model_qa.py``.

The stage is the most orchestrated one in the pipeline (rewrite loop,
gate check, fallback writer, rejection short-circuit, audit trail).
Tests focus on the control-flow decisions — approved/rejected, gate
off, max-rewrites, fallback writer, timeout — and trust MultiModelQA's
own test coverage for score/review semantics.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage
from services.prompt_manager import get_prompt_manager
from services.stages.cross_model_qa import (
    CrossModelQAStage,
    _build_rejection_reason,
    _resolve_max_rewrites,
    aggregate_issues_to_fix,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _reviewer(name: str, score: float, approved: bool, feedback: str = "fb",
              provider: str = "ollama") -> SimpleNamespace:
    return SimpleNamespace(
        reviewer=name, score=score, approved=approved,
        feedback=feedback, provider=provider,
    )


def _validation(issues: list[tuple[str, str, str]]) -> SimpleNamespace:
    """Build a fake ValidationResult. issues: list of (severity, category, description)."""
    return SimpleNamespace(issues=[
        SimpleNamespace(severity=s, category=c, description=d)
        for s, c, d in issues
    ])


def _qa_approved(score: float = 85.0, validation=None, reviews=None) -> SimpleNamespace:
    return SimpleNamespace(
        approved=True,
        final_score=score,
        validation=validation,
        reviews=reviews or [_reviewer("critic", 90, True)],
        summary=f"approved score={score}",
        cost_log=None,
    )


def _qa_rejected(score: float = 40.0, validation=None, reviews=None) -> SimpleNamespace:
    return SimpleNamespace(
        approved=False,
        final_score=score,
        validation=validation,
        reviews=reviews or [_reviewer("critic", 40, False, feedback="contradiction")],
        summary=f"rejected score={score}",
        cost_log=None,
    )


def _early_quality_result(score: float = 72.0) -> SimpleNamespace:
    dims = SimpleNamespace(
        clarity=1, accuracy=1, completeness=1, relevance=1,
        seo_quality=1, readability=1, engagement=1,
    )
    return SimpleNamespace(
        overall_score=score, passing=True, truncation_detected=False,
        dimensions=dims,
    )


class _FakeDb:
    def __init__(self):
        self.updates: list[dict[str, Any]] = []
        self.costs: list[dict[str, Any]] = []
        self.pool = MagicMock()

    async def update_task(self, task_id, updates):
        self.updates.append({"task_id": task_id, **updates})

    async def log_cost(self, cost_log):
        self.costs.append(cost_log)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms(self):
        assert isinstance(CrossModelQAStage(), Stage)

    def test_metadata(self):
        s = CrossModelQAStage()
        assert s.name == "cross_model_qa"
        # Stage uses halts_on_failure=False so the runner doesn't halt on
        # ok=False — we handle halt explicitly via continue_workflow.
        assert s.halts_on_failure is False


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestAggregateIssuesToFix:
    def test_critical_validation_blocks(self):
        qr = _qa_approved(
            validation=_validation([
                ("critical", "fabrication", "Made up Dr. Smith"),
            ]),
        )
        text, blocking = aggregate_issues_to_fix(qr)
        assert "[critical]" in text
        assert "fabrication" in text
        assert blocking is True

    def test_warning_only_does_not_block(self):
        qr = _qa_approved(
            validation=_validation([
                ("warning", "seo", "weak title"),
            ]),
        )
        text, blocking = aggregate_issues_to_fix(qr)
        assert "[warning]" in text
        assert blocking is False

    def test_non_approving_reviewer_blocks(self):
        qr = _qa_rejected(
            reviews=[_reviewer("critic", 40, False, "too short")],
        )
        text, blocking = aggregate_issues_to_fix(qr)
        assert "critic" in text
        assert "too short" in text
        assert blocking is True

    def test_approved_borderline_reviewer_advisory(self):
        qr = _qa_approved(
            reviews=[_reviewer("critic", 60, True, "meh")],
        )
        text, blocking = aggregate_issues_to_fix(qr)
        assert "[warning]" in text
        assert blocking is False

    def test_programmatic_validator_only_surfaced_via_validation_not_reviews(self):
        # A programmatic_validator entry in reviews should be skipped; the
        # validation block is the authoritative source.
        qr = _qa_approved(
            validation=None,
            reviews=[_reviewer("programmatic_validator", 50, False, "ignored")],
        )
        text, blocking = aggregate_issues_to_fix(qr)
        assert "programmatic_validator" not in text
        assert blocking is False  # nothing else blocks

    def test_issue_cap_at_30(self):
        qr = _qa_approved(
            validation=_validation([
                ("critical", "c", f"issue {i}") for i in range(50)
            ]),
        )
        text, blocking = aggregate_issues_to_fix(qr)
        assert text.count("\n") + 1 == 30
        assert blocking is True


class TestBuildRejectionReason:
    def test_names_vetoer(self):
        qr = _qa_rejected(reviews=[
            _reviewer("topic_delivery", 40, False, "wrong topic"),
        ])
        msg = _build_rejection_reason(qr)
        assert "topic_delivery" in msg
        assert "40" in msg
        assert "wrong topic" in msg

    def test_no_reviews(self):
        qr = SimpleNamespace(
            approved=False, final_score=40.0, validation=None,
            reviews=[], summary="rejected",
        )
        msg = _build_rejection_reason(qr)
        assert "No reviews recorded" in msg


@pytest.mark.asyncio
class TestResolveMaxRewrites:
    async def test_default_when_no_settings(self):
        assert await _resolve_max_rewrites(None, default=2) == 2

    async def test_reads_qa_max_rewrites(self):
        svc = MagicMock()
        svc.get = AsyncMock(side_effect=lambda k: "5" if k == "qa_max_rewrites" else None)
        assert await _resolve_max_rewrites(svc, default=2) == 5

    async def test_falls_back_to_legacy_key(self):
        svc = MagicMock()
        svc.get = AsyncMock(side_effect=[None, "3"])  # first call None, second "3"
        assert await _resolve_max_rewrites(svc, default=2) == 3

    async def test_raises_falls_back_to_default(self):
        svc = MagicMock()
        svc.get = AsyncMock(side_effect=RuntimeError("settings down"))
        assert await _resolve_max_rewrites(svc, default=2) == 2


# ---------------------------------------------------------------------------
# CrossModelQAStage.execute — full flow tests
# ---------------------------------------------------------------------------


def _patch_stage_imports(qa_review_return: Any, max_rewrites: int = 2):
    """Patch every external dep at the stage's import sites."""
    fake_qa = SimpleNamespace(review=AsyncMock(return_value=qa_review_return))
    # side_effect list if qa_review_return is a list (rewrite-loop tests)
    if isinstance(qa_review_return, list):
        fake_qa.review = AsyncMock(side_effect=qa_review_return)

    settings_svc = MagicMock()
    settings_svc.get = AsyncMock(return_value=str(max_rewrites))

    return [
        patch(
            "services.text_utils.normalize_text",
            side_effect=lambda x: x,
        ),
        patch("services.multi_model_qa.MultiModelQA", return_value=fake_qa),
        patch("services.container.get_service", return_value=settings_svc),
        patch("services.audit_log.audit_log_bg", MagicMock()),
    ], fake_qa


@pytest.mark.asyncio
class TestExecuteGate:
    """Post-Phase-E2 note: the stage no longer carries an internal
    enable gate. StageRunner reads ``plugin.stage.cross_model_qa.enabled``
    from app_settings and skips the stage entirely when disabled. Those
    paths are covered by ``tests/unit/plugins/test_stage_runner.py``."""

    async def test_stage_does_not_self_gate(self):
        # Even with a "disabled" disposition, the stage executes when
        # the runner invokes it — enforcement is at the runner layer.
        db = _FakeDb()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "content": "body",
            "database_service": db,
            "quality_result": _early_quality_result(score=73),
        }
        patches, _ = _patch_stage_imports(_qa_approved(score=88))
        for p in patches:
            p.start()
        try:
            result = await CrossModelQAStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()
        assert result.ok is True
        assert result.context_updates["qa_final_score"] == 88


@pytest.mark.asyncio
class TestExecuteApproved:
    async def test_first_pass_approval_populates_reviews(self):
        db = _FakeDb()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "content": "body",
            "database_service": db,
            "quality_result": _early_quality_result(score=70),
            "quality_score": 70,  # early eval score
        }
        qa = _qa_approved(score=88, reviews=[
            _reviewer("critic", 90, True), _reviewer("validator", 85, True),
        ])
        patches, _ = _patch_stage_imports(qa)
        for p in patches:
            p.start()
        try:
            result = await CrossModelQAStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()
        assert result.ok is True
        assert result.continue_workflow is True  # pipeline continues
        u = result.context_updates
        assert u["qa_final_score"] == 88
        # quality_score promoted to max(70, 88) = 88
        assert u["quality_score"] == 88
        assert len(u["qa_reviews"]) == 2
        assert u["qa_rewrite_attempts"] == 0


@pytest.mark.asyncio
class TestExecuteRejected:
    async def test_rejection_halts_and_writes_db(self):
        db = _FakeDb()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "content": "body",
            "database_service": db,
            "quality_result": _early_quality_result(score=70),
        }
        # Rejected, with a topic_delivery failure → bail after first pass
        # (no rewrite attempted).
        qa = _qa_rejected(score=30, reviews=[
            _reviewer("topic_delivery", 30, False, "article is about the wrong thing"),
        ])
        patches, _ = _patch_stage_imports(qa)
        for p in patches:
            p.start()
        try:
            result = await CrossModelQAStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()
        # Stage returns ok=True but continue_workflow=False to halt the runner
        assert result.ok is True
        assert result.continue_workflow is False
        assert result.context_updates["status"] == "rejected"
        # DB update persisted
        reject_update = next(u for u in db.updates if u.get("status") == "rejected")
        assert "topic_delivery" in reject_update["error_message"]
        assert "30" in reject_update["error_message"]


@pytest.mark.asyncio
class TestExecuteTimeout:
    async def test_qa_review_returning_none_uses_early_score(self):
        db = _FakeDb()
        ctx: dict[str, Any] = {
            "task_id": "t1", "topic": "T", "content": "body",
            "database_service": db,
            "quality_result": _early_quality_result(score=71),
        }
        patches, _ = _patch_stage_imports(None)  # review returns None
        for p in patches:
            p.start()
        try:
            result = await CrossModelQAStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()
        assert result.ok is True
        assert result.context_updates["qa_final_score"] == 71
        assert result.context_updates["qa_reviews"][0]["reviewer"] == "timeout"


@pytest.mark.asyncio
class TestExecuteMissingContext:
    async def test_missing_db(self):
        result = await CrossModelQAStage().execute(
            {"task_id": "t", "quality_result": _early_quality_result()}, {},
        )
        assert result.ok is False

    async def test_missing_quality_result(self):
        result = await CrossModelQAStage().execute(
            {"task_id": "t", "database_service": _FakeDb()}, {},
        )
        assert result.ok is False


# ---------------------------------------------------------------------------
# Prompt template sanity
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    def test_template_renders_with_required_placeholders(self):
        rendered = get_prompt_manager().get_prompt(
            "qa.aggregate_rewrite",
            title="TITLE_MARKER",
            issues_to_fix="ISSUES_MARKER",
            content="CONTENT_MARKER",
        )
        assert "TITLE_MARKER" in rendered
        assert "ISSUES_MARKER" in rendered
        assert "CONTENT_MARKER" in rendered
        # Sanity check against template drift
        assert "Return ONLY the revised article text" in rendered
