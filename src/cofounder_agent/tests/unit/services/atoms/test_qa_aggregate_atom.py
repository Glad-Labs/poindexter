"""Unit tests for the qa.aggregate atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_aggregate


class _Cfg:
    def __init__(self, vals=None):
        self._vals = vals or {}

    def get(self, key, default=None):
        return self._vals.get(key, default)


@pytest.mark.unit
class TestQaAggregateAtom:
    def test_meta(self):
        m = qa_aggregate.ATOM_META
        assert m.name == "qa.aggregate"
        assert "qa_rail_reviews" in m.requires
        assert "qa_final_score" in m.produces
        assert "qa_final_verdict" in m.produces

    async def test_approve_path(self):
        state = {
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert out["qa_final_score"] == 90.0
        assert "_halt" not in out

    async def test_reject_halts(self):
        state = {
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": False, "score": 95.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert "ollama_qa" in out["_halt_reason"]

    async def test_reads_threshold_from_site_config(self):
        # Threshold 95 → an 90-scoring all-pass run now REJECTS.
        state = {
            "site_config": _Cfg({"qa_final_score_threshold": "95"}),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"

    async def test_missing_reviews_key_rejects_at_zero(self):
        out = await qa_aggregate.run({"site_config": _Cfg()})
        assert out["qa_final_score"] == 0.0
        assert out["_halt"] is True


class _Pool2:
    def __init__(self):
        self.execs = []

    async def execute(self, sql, *args):
        self.execs.append((sql, args))


class _DB2:
    def __init__(self):
        self.pool = _Pool2()
        self.update_task_calls = []
        self.mark_calls = []

    async def update_task(self, task_id, fields):
        self.update_task_calls.append((task_id, fields))

    async def mark_model_performance_outcome(self, task_id, human_approved):
        self.mark_calls.append((task_id, human_approved))


@pytest.mark.unit
class TestQaAggregateParity:
    async def test_approve_sets_downstream_keys(self):
        state = {
            "site_config": _Cfg(),
            "quality_score": 60.0,
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False, "feedback": "good"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert out["quality_score"] == 90.0          # promoted = max(60, 90)
        assert out["qa_reviews"] == state["qa_rail_reviews"]  # populated for finalize_task
        assert out["qa_rewrite_attempts"] == 0
        assert "_halt" not in out

    async def test_approve_keeps_higher_early_score(self):
        state = {"site_config": _Cfg(), "quality_score": 95.0,
                 "qa_rail_reviews": [{"reviewer": "x", "approved": True, "score": 80.0, "provider": "ollama", "advisory": False, "feedback": ""}]}
        out = await qa_aggregate.run(state)
        assert out["quality_score"] == 95.0  # max(95, 80)

    async def test_reject_does_db_writes_and_halts(self, monkeypatch):
        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            "site_config": _Cfg(),
            "task_id": "task-9",
            "content": "the body",
            "title": "A Title",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": False, "score": 40.0, "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert out["status"] == "rejected"
        # DB writes happened.
        assert db.update_task_calls[0][1]["status"] == "rejected"
        assert db.mark_calls[0] == ("task-9", False)
        assert any("pipeline_gate_history" in sql for sql, _ in db.pool.execs)

    async def test_reject_without_db_service_still_halts(self):
        state = {"site_config": _Cfg(), "task_id": "t",
                 "qa_rail_reviews": [{"reviewer": "x", "approved": False, "score": 10.0, "provider": "ollama", "advisory": False, "feedback": "no"}]}
        out = await qa_aggregate.run(state)  # no database_service
        assert out["_halt"] is True
        assert out["qa_final_verdict"] == "reject"
