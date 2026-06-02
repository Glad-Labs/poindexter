"""Unit tests for the qa reject-persistence helper (atom-cutover Plan 5, #355).
Stub database_service — no live DB."""

from __future__ import annotations

from typing import Any

import pytest

from services.atoms._qa_persist import build_qa_feedback, build_reject_reason, persist_qa_reject


class _Pool:
    def __init__(self):
        self.execs: list[tuple] = []

    async def execute(self, sql: str, *args: Any):
        self.execs.append((sql, args))


class _DB:
    def __init__(self):
        self.pool = _Pool()
        self.update_task_calls: list[tuple] = []
        self.mark_calls: list[tuple] = []

    async def update_task(self, task_id, fields):
        self.update_task_calls.append((task_id, fields))

    async def mark_model_performance_outcome(self, task_id, human_approved):
        self.mark_calls.append((task_id, human_approved))


@pytest.mark.unit
class TestBuildHelpers:
    def test_reject_reason_names_failing_rails(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": False, "score": 40.0, "feedback": "weak intro", "provider": "ollama"},
            {"reviewer": "deepeval_g_eval", "approved": True, "score": 85.0, "feedback": "ok", "provider": "ollama"},
        ]
        reason = build_reject_reason(reviews, vetoed_by=["ollama_qa"], final_score=55.0)
        assert "ollama_qa" in reason
        assert "55" in reason

    def test_qa_feedback_lists_reviews(self):
        reviews = [{"reviewer": "ollama_qa", "approved": False, "score": 40.0, "feedback": "weak intro", "provider": "ollama"}]
        fb = build_qa_feedback(reviews, final_score=55.0, approved=False)
        assert "ollama_qa" in fb and "weak intro" in fb


@pytest.mark.unit
class TestPersistQaReject:
    async def test_does_all_four_writes(self, monkeypatch):
        db = _DB()
        captured = {}

        class _FakePipelineDB:
            def __init__(self, pool):
                captured["pool"] = pool

            async def upsert_version(self, task_id, fields):
                captured["upsert"] = (task_id, fields)

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        await persist_qa_reject(
            db, task_id="t1", reason="bad", final_score=55.0,
            content="body", title="A Title", qa_feedback="fb",
            models_used_by_phase={"writer": "m"},
        )
        # 1. update_task(status=rejected, quality_score)
        assert db.update_task_calls[0][0] == "t1"
        assert db.update_task_calls[0][1]["status"] == "rejected"
        assert db.update_task_calls[0][1]["quality_score"] == 55.0
        # 2. upsert_version
        assert captured["upsert"][0] == "t1"
        assert captured["upsert"][1]["quality_score"] == 55  # int(round(55.0))
        # 3. mark_model_performance_outcome(human_approved=False)
        assert db.mark_calls[0] == ("t1", False)
        # 4. pipeline_gate_history INSERT
        assert any("pipeline_gate_history" in sql for sql, _ in db.pool.execs)

    async def test_best_effort_swallows_pool_error(self, monkeypatch):
        db = _DB()

        async def boom(sql, *args):
            raise RuntimeError("db down")

        db.pool.execute = boom  # gate_history write fails

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        # Must not raise — update_task still happened.
        await persist_qa_reject(
            db, task_id="t1", reason="bad", final_score=55.0,
            content="b", title="t", qa_feedback="fb", models_used_by_phase={},
        )
        assert db.update_task_calls[0][1]["status"] == "rejected"

    async def test_none_db_noop(self):
        await persist_qa_reject(None, task_id="t1", reason="r", final_score=1.0,
                                content="c", title="t", qa_feedback="f", models_used_by_phase={})
