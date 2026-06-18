"""Unit tests for the qa.aggregate atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_aggregate
from plugins.fake_platform import FakePlatform


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
            "platform": FakePlatform(),
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
            # rescue disabled (max_attempts=0) — this test pins the hard-reject/halt path
            "platform": FakePlatform(config={"qa_rewrite_max_attempts": "0"}),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": False, "score": 95.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert "ollama_qa" in out["_halt_reason"]

    async def test_reads_threshold_from_config_handle(self):
        # Seam 1 Wave 3e (#667): the threshold is read via platform.config.
        # Threshold 95 → an 90-scoring all-pass run now REJECTS.
        state = {
            "platform": FakePlatform(config={"qa_final_score_threshold": "95"}),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"

    async def test_weights_fall_back_to_defaults_without_platform(self):
        # No handle (tests / ad-hoc CLI) → _weight returns its defaults, so the
        # default 70.0 threshold applies: a 90-scoring all-pass run APPROVES.
        # Mirrors the prior site_config-None seam — None-tolerant, no raise.
        out = await qa_aggregate.run({
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        })
        assert out["qa_final_verdict"] == "approve"
        assert out["qa_final_score"] == 90.0

    async def test_missing_reviews_key_rejects_at_zero(self):
        # rescue disabled (max_attempts=0) — this test pins the hard-reject/halt path
        out = await qa_aggregate.run(
            {"platform": FakePlatform(config={"qa_rewrite_max_attempts": "0"})}
        )
        assert out["qa_final_score"] == 0.0
        assert out["_halt"] is True

    async def test_known_wrong_fact_rescue_suppresses_validator_veto(self):
        """#661: the qa_known_wrong_fact_only flag (set by qa.programmatic) +
        an approved web_factcheck rail suppresses the programmatic_validator
        veto — qa.aggregate APPROVES instead of wrongly hard-rejecting legit
        post-cutoff content."""
        state = {
            "platform": FakePlatform(),
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
            "platform": FakePlatform(),
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
            "platform": FakePlatform(),
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
            "platform": FakePlatform(),
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
        state = {"platform": FakePlatform(), "quality_score": 95.0,
                 "qa_rail_reviews": [{"reviewer": "x", "approved": True, "score": 80.0, "provider": "ollama", "advisory": False, "feedback": ""}]}
        out = await qa_aggregate.run(state)
        assert out["quality_score"] == 95.0  # max(95, 80)

    async def test_reject_does_db_writes_and_halts(self, monkeypatch):
        class _FakePipelineDB:
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields): pass

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            # rescue disabled (max_attempts=0) — this test pins the hard-reject/halt path
            "platform": FakePlatform(config={"qa_rewrite_max_attempts": "0"}),
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
        # rescue disabled (max_attempts=0) — this test pins the hard-reject/halt path
        state = {"platform": FakePlatform(config={"qa_rewrite_max_attempts": "0"}), "task_id": "t",
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
            "platform": FakePlatform(),
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
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields): pass

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _GateDB()
        state = {
            "platform": FakePlatform(),
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
            "platform": FakePlatform(),
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

    @staticmethod
    def _passes(fake: FakePlatform) -> list[dict]:
        # Seam 1 Wave 3c (#667): the atom now emits through the capability
        # handle (``state['platform'].audit.write_bg``), recorded on the fake's
        # ``writes_bg``, rather than the global ``audit_log_bg``.
        return [w for w in fake.audit.writes_bg if w["event_type"] == "qa_pass_completed"]

    async def test_approve_emits_qa_pass_completed(self):
        fake = FakePlatform()
        state = {
            "platform": fake,
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
        passes = self._passes(fake)
        assert len(passes) == 1
        d = passes[0]["details"]
        assert d["approved"] is True
        assert d["reviewer_count"] == 2
        assert passes[0]["task_id"] == "task-abc"
        # The reviews breakdown the dashboard's per-reviewer panels read.
        reviewers = {r["reviewer"] for r in d["reviews"]}
        assert reviewers == {"ollama_critic", "ragas_eval"}
        assert all({"provider", "approved", "score", "advisory"} <= set(r) for r in d["reviews"])

    async def test_reject_emits_warning_severity_pass(self):
        fake = FakePlatform()
        state = {
            "platform": fake,
            "task_id": "task-xyz",
            "qa_rail_reviews": [
                {"reviewer": "guardrails_brand", "approved": False, "score": 20.0,
                 "provider": "consistency_gate", "advisory": False, "feedback": "off-brand"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        passes = self._passes(fake)
        assert len(passes) == 1
        assert passes[0]["details"]["approved"] is False
        # Rejected passes are severity=warning (mirrors the legacy stage).
        assert passes[0]["severity"] == "warning"


class _FakeSiteConfig:
    def get(self, key, default=None):
        return default


class _VacuousPool:
    async def execute(self, *a):
        pass

    def acquire(self):
        pass


class _VacuousDB:
    def __init__(self):
        self.pool = _VacuousPool()
    async def update_task(self, *a, **kw):
        pass
    async def mark_model_performance_outcome(self, *a, **kw):
        pass


@pytest.mark.unit
class TestQaAggregateVacuousPassGuard:
    """poindexter#680: a required rail that emits no review must fail closed,
    not wave content through as if the rail passed."""

    async def test_missing_required_rail_rejects(self, monkeypatch):
        # Patch resolve_gate_states to report deepeval_g_eval as required+enabled.
        async def _fake_gate_states(_qa):
            return {"deepeval_g_eval": (True, True), "ragas_eval": (True, False)}

        monkeypatch.setattr(
            "modules.content.atoms.qa_aggregate.resolve_gate_states",
            _fake_gate_states,
        )
        # Also stub MultiModelQA construction (it requires Ollama + DB).
        monkeypatch.setattr(
            "modules.content.multi_model_qa.MultiModelQA.__init__",
            lambda self, **kw: None,
        )

        state = {
            "platform": FakePlatform(),
            "database_service": _VacuousDB(),
            "site_config": _FakeSiteConfig(),
            # One non-required rail approved; the required deepeval_g_eval is absent.
            "qa_rail_reviews": [
                {"reviewer": "ragas_eval", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": True, "feedback": ""},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert any("missing_required:deepeval_g_eval" in v for v in out.get("vetoed_by", []))

    async def test_all_required_present_approves(self, monkeypatch):
        # All required rails present → normal approve path.
        async def _fake_gate_states(_qa):
            return {"deepeval_g_eval": (True, True)}

        monkeypatch.setattr(
            "modules.content.atoms.qa_aggregate.resolve_gate_states",
            _fake_gate_states,
        )
        monkeypatch.setattr(
            "modules.content.multi_model_qa.MultiModelQA.__init__",
            lambda self, **kw: None,
        )

        state = {
            "platform": FakePlatform(),
            "database_service": _VacuousDB(),
            "site_config": _FakeSiteConfig(),
            "qa_rail_reviews": [
                {"reviewer": "deepeval_g_eval", "approved": True, "score": 88.0,
                 "provider": "ollama", "advisory": False, "feedback": "good"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert "_halt" not in out


@pytest.mark.unit
class TestQaAggregateRescueDispatch:
    """QA rescue cycle: a rescuable reject defers to qa.rewrite (emits _goto,
    no _halt, no DB persist) while attempts remain; a non-rescuable or
    exhausted reject hard-rejects as before."""

    def _critic_reject_state(self, **extra):
        # ollama_critic (provider ollama) fails non-advisory, score below 70.
        state = {
            "platform": FakePlatform(),  # default max_attempts -> 2 (rescue on)
            "task_id": "task-r",
            "content": "the body",
            "title": "T",
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak intro"},
            ],
        }
        state.update(extra)
        return state

    async def test_critic_veto_defers_to_rewrite(self):
        out = await qa_aggregate.run(self._critic_reject_state())
        assert out["_goto"] == "qa_rewrite"
        assert "_halt" not in out
        assert "status" not in out           # no reject persistence
        # The attempt counter is passed through unchanged (qa.rewrite bumps it).
        assert out["qa_rewrite_attempts"] == 0

    async def test_rescue_dispatch_does_no_db_writes(self, monkeypatch):
        # persist_qa_reject must NOT be called on a deferred rescue.
        called = {"persist": False}

        async def _spy_persist(*a, **kw):
            called["persist"] = True

        monkeypatch.setattr(
            "modules.content.atoms._qa_persist.persist_qa_reject", _spy_persist,
        )
        out = await qa_aggregate.run(self._critic_reject_state())
        assert out["_goto"] == "qa_rewrite"
        assert called["persist"] is False

    async def test_rescue_dispatch_omits_qa_reviews(self):
        # qa_reviews uses operator.add; emitting it on the deferred pass would
        # concat stale+fresh on the terminal pass. The rescue path must omit it.
        out = await qa_aggregate.run(self._critic_reject_state())
        assert "qa_reviews" not in out

    async def test_rescue_emits_qa_rescue_scheduled_audit(self):
        fake = FakePlatform()
        state = self._critic_reject_state(platform=fake)
        await qa_aggregate.run(state)
        events = [w for w in fake.audit.writes_bg if w["event_type"] == "qa_rescue_scheduled"]
        assert len(events) == 1
        assert events[0]["details"]["attempt"] == 1
        # The terminal qa_pass_completed is NOT emitted on a deferred rescue.
        passes = [w for w in fake.audit.writes_bg if w["event_type"] == "qa_pass_completed"]
        assert passes == []

    async def test_score_threshold_reject_defers(self):
        # Critic APPROVED, but the weighted score (62) is below the 70 floor.
        state = {
            "platform": FakePlatform(),
            "task_id": "task-s",
            "content": "body", "title": "T",
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 62.0,
                 "provider": "ollama", "advisory": False, "feedback": "ok-ish"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_goto"] == "qa_rewrite"
        assert "_halt" not in out

    async def test_exhausted_attempts_hard_rejects(self, monkeypatch):
        # attempts already == max: no more rescue → hard reject + halt + persist.
        # Pin max_attempts=1 so attempts=1 is exhausted regardless of the global
        # default (which is now 2, #1692 follow-up).
        class _FakePipelineDB:
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields): pass

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = self._critic_reject_state(
            platform=FakePlatform(config={"qa_rewrite_max_attempts": "1"}),
            qa_rewrite_attempts=1, database_service=db, models_used_by_phase={},
        )
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert out["_goto"] == ""
        assert out["status"] == "rejected"
        assert db.update_task_calls[0][1]["status"] == "rejected"

    async def test_fabrication_veto_never_rescues(self, monkeypatch):
        # programmatic_validator veto (fabrication) is NON-rescuable even with
        # attempts available → hard reject immediately, no _goto to rewrite.
        class _FakePipelineDB:
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields): pass

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            "platform": FakePlatform(),       # rescue ON (max 1)
            "task_id": "task-fab",
            "content": "body", "title": "T",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rail_reviews": [
                {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
                 "provider": "programmatic", "advisory": False, "feedback": "fake_person"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert out["_goto"] == ""
        assert out.get("status") == "rejected"

    async def test_approve_clears_goto(self):
        state = {
            "platform": FakePlatform(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False, "feedback": "great"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert out["_goto"] == ""
        assert "_halt" not in out


@pytest.mark.unit
class TestQaAggregateMaxAttemptsDefault:
    """#1692 follow-up: the default qa_rewrite_max_attempts is now 2, so the
    rescue cycle can do write -> qa -> revise -> qa -> revise before hard-reject.
    Pins the new default via the no-explicit-config FakePlatform path."""

    def _critic_reject_state(self, **extra):
        state = {
            "platform": FakePlatform(),  # no qa_rewrite_max_attempts -> default
            "task_id": "task-ma",
            "content": "the body",
            "title": "T",
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        state.update(extra)
        return state

    async def test_default_allows_second_revision_pass(self):
        # attempts=1 would have hard-rejected under the old default of 1; under
        # the new default of 2 it still DEFERS for a second revision.
        out = await qa_aggregate.run(self._critic_reject_state(qa_rewrite_attempts=1))
        assert out["_goto"] == "qa_rewrite"
        assert "_halt" not in out

    async def test_default_exhausts_at_two(self, monkeypatch):
        # attempts=2 == default max -> terminal hard reject (no third pass).
        class _FakePipelineDB:
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields): pass

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        out = await qa_aggregate.run(self._critic_reject_state(
            qa_rewrite_attempts=2, database_service=db, models_used_by_phase={},
        ))
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert out["_goto"] == ""


@pytest.mark.unit
class TestQaAggregateKeepBest:
    """Keep-best regression guard (#1692 follow-up): a rescue revision can score
    LOWER than the draft it replaced (observed: placeholders amplified, critic
    score 52 -> 35). qa.aggregate tracks the best-scoring body across the rescue
    cycle in the qa_best_content / qa_best_score channels and, on a terminal
    REJECT, retains the higher-scoring draft. On APPROVE the revision always
    wins (it cleared the gate the earlier drafts failed)."""

    async def test_defer_stashes_running_best_first_pass(self):
        # First rescuable-reject defer: the current draft + its score become the
        # running best carried forward for the next pass.
        state = {
            "platform": FakePlatform(),
            "task_id": "task-kb1",
            "content": "draft v0",
            "title": "T",
            "qa_rewrite_attempts": 0,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 50.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["_goto"] == "qa_rewrite"
        assert out["qa_best_content"] == "draft v0"
        assert out["qa_best_score"] == 50.0

    async def test_defer_keeps_prior_higher_stash(self):
        # Second defer: the current revision (40) scored BELOW the stashed
        # earlier draft (55) -> the running best stays the earlier, higher draft.
        state = {
            "platform": FakePlatform(),
            "task_id": "task-kb2",
            "content": "draft v1 (worse)",
            "title": "T",
            "qa_rewrite_attempts": 1,            # 1 < default max 2 -> still defers
            "qa_best_content": "draft v0 (better)",
            "qa_best_score": 55.0,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 40.0,
                 "provider": "ollama", "advisory": False, "feedback": "worse"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["_goto"] == "qa_rewrite"
        assert out["qa_best_content"] == "draft v0 (better)"
        assert out["qa_best_score"] == 55.0

    async def test_defer_adopts_improved_current_as_best(self):
        # Second defer where the revision (60) IMPROVED over the stash (52) but is
        # still a reject -> the running best advances to the current draft.
        state = {
            "platform": FakePlatform(),
            "task_id": "task-kb3",
            "content": "draft v1 (better)",
            "title": "T",
            "qa_rewrite_attempts": 1,
            "qa_best_content": "draft v0",
            "qa_best_score": 52.0,
            "qa_rail_reviews": [
                # critic approves but weighted score 60 < 70 threshold -> reject.
                {"reviewer": "ollama_critic", "approved": True, "score": 60.0,
                 "provider": "ollama", "advisory": False, "feedback": "closer"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["_goto"] == "qa_rewrite"
        assert out["qa_best_content"] == "draft v1 (better)"
        assert out["qa_best_score"] == 60.0

    async def test_terminal_reject_restores_higher_scoring_earlier_draft(self, monkeypatch):
        # The exhausting revision (35) regressed below the stashed earlier draft
        # (52) -> keep-best restores the earlier body + score for persistence.
        captured = {}

        class _FakePipelineDB:
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields):
                captured["version"] = fields

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            "platform": FakePlatform(config={"qa_rewrite_max_attempts": "1"}),
            "task_id": "task-kb4",
            "content": "regressed revision body",      # the worse final revision
            "title": "T",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rewrite_attempts": 1,                  # == max -> terminal
            "qa_best_content": "original better body",
            "qa_best_score": 52.0,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 35.0,
                 "provider": "ollama", "advisory": False, "feedback": "now worse"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        # Higher-scoring earlier draft retained — not the regression.
        assert out["content"] == "original better body"
        assert out["qa_final_score"] == 52.0
        assert out["quality_score"] == 52.0
        # ...and that is what lands in pipeline_versions + pipeline_tasks.
        assert captured["version"]["content"] == "original better body"
        assert db.update_task_calls[0][1]["quality_score"] == 52.0

    async def test_terminal_reject_keeps_improved_revision(self, monkeypatch):
        # The final revision (60) beat the stash (52) but is still below the 70
        # threshold -> reject, and the improved revision is kept (no restore).
        captured = {}

        class _FakePipelineDB:
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields):
                captured["version"] = fields

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            "platform": FakePlatform(config={"qa_rewrite_max_attempts": "1"}),
            "task_id": "task-kb5",
            "content": "improved revision body",
            "title": "T",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rewrite_attempts": 1,
            "qa_best_content": "older worse body",
            "qa_best_score": 52.0,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 60.0,
                 "provider": "ollama", "advisory": False, "feedback": "better but low"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        # No restore: the higher-scoring current revision stays.
        assert "content" not in out
        assert out["qa_final_score"] == 60.0
        assert captured["version"]["content"] == "improved revision body"

    async def test_approve_never_restores_vetoed_higher_scoring_draft(self):
        # The pre-rescue draft scored 90 but was critic-VETOED (rescuable). The
        # revision scores 75 and the critic now approves -> APPROVE. The approved
        # (lower-scoring) body must win; a vetoed draft is never published just
        # because its weighted score was higher.
        state = {
            "platform": FakePlatform(),
            "task_id": "task-kb6",
            "content": "approved revision body",
            "qa_rewrite_attempts": 1,
            "qa_best_content": "vetoed original body",
            "qa_best_score": 90.0,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 75.0,
                 "provider": "ollama", "advisory": False, "feedback": "good now"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert "_halt" not in out
        assert "content" not in out          # revision body left untouched in state
        assert out["quality_score"] == 75.0  # not the vetoed draft's 90

    async def test_first_pass_hard_reject_does_not_restore(self, monkeypatch):
        # A non-rescuable first-pass reject (no prior stash) must behave exactly
        # as before keep-best: persist the current body, no spurious content key.
        class _FakePipelineDB:
            def __init__(self, pool): pass
            async def upsert_version(self, task_id, fields): pass

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            "platform": FakePlatform(config={"qa_rewrite_max_attempts": "0"}),
            "task_id": "task-kb7",
            "content": "the only body",
            "title": "T",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rail_reviews": [
                {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
                 "provider": "programmatic", "advisory": False, "feedback": "fake_person"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert "content" not in out
        assert db.update_task_calls[0][1]["status"] == "rejected"
