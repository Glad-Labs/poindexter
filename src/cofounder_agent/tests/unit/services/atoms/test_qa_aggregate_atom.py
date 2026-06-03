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

    async def test_known_wrong_fact_rescue_suppresses_validator_veto(self):
        """#661: the qa_known_wrong_fact_only flag (set by qa.programmatic) +
        an approved web_factcheck rail suppresses the programmatic_validator
        veto — qa.aggregate APPROVES instead of wrongly hard-rejecting legit
        post-cutoff content."""
        state = {
            "site_config": _Cfg(),
            "qa_known_wrong_fact_only": True,
            "qa_rail_reviews": [
                {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
                 "provider": "programmatic", "advisory": False,
                 "feedback": "known_wrong_fact: RTX 5090"},
                {"reviewer": "web_factcheck", "approved": True, "score": 100.0,
                 "provider": "web_factcheck", "advisory": True, "feedback": "verified"},
                {"reviewer": "llm_critic", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False, "feedback": "ok"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert "_halt" not in out

    async def test_no_rescue_when_web_failed_still_rejects(self):
        """Same flag, but the web check did NOT confirm → the validator veto
        STANDS and qa.aggregate rejects + halts (the genuinely-wrong case)."""
        state = {
            "site_config": _Cfg(),
            "qa_known_wrong_fact_only": True,
            "qa_rail_reviews": [
                {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
                 "provider": "programmatic", "advisory": False, "feedback": "known_wrong_fact"},
                {"reviewer": "web_factcheck", "approved": False, "score": 0.0,
                 "provider": "web_factcheck", "advisory": True, "feedback": "unverified"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True

    async def test_no_flag_means_validator_veto_stands(self):
        """Without the flag (a normal fabrication), an approved web_factcheck does
        NOT rescue the validator veto — only stale-regex known_wrong_fact does."""
        state = {
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
                 "provider": "programmatic", "advisory": False, "feedback": "fake_person"},
                {"reviewer": "web_factcheck", "approved": True, "score": 100.0,
                 "provider": "web_factcheck", "advisory": True, "feedback": "verified"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
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


class _GateConn:
    """Supports the acquire()/transaction()/execute() protocol that
    qa_gates_db_writer.record_chain_run uses."""

    def __init__(self, recorder):
        self._rec = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def transaction(self):
        return self

    async def execute(self, sql, *args):
        self._rec.append((sql, args))


class _GatePool:
    def __init__(self):
        self.gate_execs = []   # qa_gates counter UPDATEs (via acquire/transaction)
        self.direct_execs = []  # direct .execute() (e.g. gate_history INSERT)

    def acquire(self):
        return _GateConn(self.gate_execs)

    async def execute(self, sql, *args):
        self.direct_execs.append((sql, args))


class _GateDB:
    """database_service stand-in exposing the asyncpg-style .pool plus the
    update_task / mark_model_performance_outcome the reject path needs."""

    def __init__(self):
        self.pool = _GatePool()
        self.update_task_calls = []
        self.mark_calls = []

    async def update_task(self, task_id, fields):
        self.update_task_calls.append((task_id, fields))

    async def mark_model_performance_outcome(self, task_id, human_approved):
        self.mark_calls.append((task_id, human_approved))


def _bumped_gates(db):
    return {args[0] for _, args in db.pool.gate_execs}


@pytest.mark.unit
class TestQaAggregateBumpsGateCounters:
    """poindexter#553: the #355 atom-cutover routed QA through the qa.*
    atoms → qa.aggregate, bypassing MultiModelQA.review() where
    record_chain_run lived. So qa_gates.total_runs froze at 0 on the prod
    graph_def path. qa.aggregate must bump the per-rail counters on EVERY
    run (approve and reject)."""

    async def test_approve_bumps_per_rail_counters(self):
        db = _GateDB()
        state = {
            "site_config": _Cfg(),
            "database_service": db,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False},
                {"reviewer": "ragas_eval", "approved": True, "score": 85.0,
                 "provider": "ollama", "advisory": True},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        # ollama_critic aliases to the llm_critic gate row.
        assert _bumped_gates(db) == {"llm_critic", "ragas_eval"}

    async def test_reject_also_bumps_counters(self, monkeypatch):
        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _GateDB()
        state = {
            "site_config": _Cfg(),
            "task_id": "task-553",
            "content": "body",
            "title": "T",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rail_reviews": [
                {"reviewer": "guardrails_brand", "approved": False, "score": 30.0,
                 "provider": "consistency_gate", "advisory": False, "feedback": "off-brand"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        # Counter still bumps on the reject path...
        assert _bumped_gates(db) == {"guardrails_brand"}
        # ...AND the existing reject persistence still happens.
        assert db.update_task_calls[0][1]["status"] == "rejected"

    async def test_no_db_service_does_not_raise(self):
        # The counter bump is best-effort: with no database_service the
        # atom must still produce its decision without raising.
        out = await qa_aggregate.run({
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False},
            ],
        })
        assert out["qa_final_verdict"] == "approve"


@pytest.mark.unit
class TestQaAggregateEmitsPassAudit:
    """poindexter#553: #355 bypassed MultiModelQA.review(), which was the
    sole emitter of the ``qa_pass_completed`` audit row that powers the
    /d/qa-rails dashboard AND is the denominator for the rail-skip-rate
    alert. qa.aggregate must re-emit it (one row per QA pass, full
    reviewer breakdown), mirroring the legacy stage."""

    def _spy(self, monkeypatch):
        calls = []

        def _fake(event_type, source, details=None, task_id=None, severity="info"):
            calls.append({
                "event_type": event_type, "source": source,
                "details": details or {}, "task_id": task_id, "severity": severity,
            })

        monkeypatch.setattr("services.audit_log.audit_log_bg", _fake)
        return calls

    async def test_approve_emits_qa_pass_completed(self, monkeypatch):
        calls = self._spy(monkeypatch)
        state = {
            "site_config": _Cfg(),
            "task_id": "task-abc",
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False, "feedback": "solid"},
                {"reviewer": "ragas_eval", "approved": True, "score": 88.0,
                 "provider": "ollama", "advisory": True, "feedback": ""},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        passes = [c for c in calls if c["event_type"] == "qa_pass_completed"]
        assert len(passes) == 1
        d = passes[0]["details"]
        assert d["approved"] is True
        assert d["reviewer_count"] == 2
        assert passes[0]["task_id"] == "task-abc"
        # The reviews breakdown the dashboard's per-reviewer panels read.
        reviewers = {r["reviewer"] for r in d["reviews"]}
        assert reviewers == {"ollama_critic", "ragas_eval"}
        assert all({"provider", "approved", "score", "advisory"} <= set(r) for r in d["reviews"])

    async def test_reject_emits_warning_severity_pass(self, monkeypatch):
        calls = self._spy(monkeypatch)
        state = {
            "site_config": _Cfg(),
            "task_id": "task-xyz",
            "qa_rail_reviews": [
                {"reviewer": "guardrails_brand", "approved": False, "score": 20.0,
                 "provider": "consistency_gate", "advisory": False, "feedback": "off-brand"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        passes = [c for c in calls if c["event_type"] == "qa_pass_completed"]
        assert len(passes) == 1
        assert passes[0]["details"]["approved"] is False
        # Rejected passes are severity=warning (mirrors the legacy stage).
        assert passes[0]["severity"] == "warning"
