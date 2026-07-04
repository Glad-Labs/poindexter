"""Unit tests for the qa reject-persistence helper (atom-cutover Plan 5, #355).
Stub database_service — no live DB."""

from __future__ import annotations

from typing import Any

import pytest

from modules.content.atoms._qa_persist import (
    build_qa_feedback,
    build_reject_reason,
    persist_qa_approved_snapshot,
    persist_qa_reject,
)


class _Pool:
    def __init__(self, version_row: dict | None = None):
        self.execs: list[tuple] = []
        self.version_row = version_row

    async def execute(self, sql: str, *args: Any):
        self.execs.append((sql, args))

    async def fetchrow(self, sql: str, *args: Any):
        return self.version_row


class _DB:
    def __init__(self, version_row: dict | None = None):
        self.pool = _Pool(version_row=version_row)
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

    async def test_emits_finding_when_version_write_fails(self, monkeypatch):
        # A failed pipeline_versions write only logged at warning (below the
        # GlitchTip ERROR gate) — now it must also emit a finding so a
        # systematic loss of rejected drafts is operator-visible.
        db = _DB()

        class _FakePipelineDB:
            def __init__(self, pool): ...

            async def upsert_version(self, task_id, fields):
                raise RuntimeError("pipeline_versions table gone")

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        calls: list[dict] = []
        monkeypatch.setattr(
            "modules.content.atoms._qa_persist.emit_finding",
            lambda **kw: calls.append(kw),
        )

        # Must not raise — the load-bearing status write still happened.
        await persist_qa_reject(
            db, task_id="task1234", reason="bad", final_score=55.0,
            content="b", title="t", qa_feedback="fb", models_used_by_phase={},
        )
        assert db.update_task_calls[0][1]["status"] == "rejected"
        assert len(calls) == 1
        kw = calls[0]
        assert kw["source"] == "qa.aggregate"
        assert kw["kind"] == "qa_reject_version_persist_failed"
        assert kw["severity"] == "warn"
        assert kw["dedup_key"] == "qa_reject_version_persist_failed"

    async def test_no_finding_when_version_write_succeeds(self, monkeypatch):
        db = _DB()

        class _FakePipelineDB:
            def __init__(self, pool): ...

            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        calls: list[dict] = []
        monkeypatch.setattr(
            "modules.content.atoms._qa_persist.emit_finding",
            lambda **kw: calls.append(kw),
        )
        await persist_qa_reject(
            db, task_id="task1234", reason="bad", final_score=55.0,
            content="b", title="t", qa_feedback="fb", models_used_by_phase={},
        )
        assert calls == []


@pytest.mark.unit
class TestKeepBestGuard:
    """A re-run's QA reject must never destroy an earlier QA-approved draft
    (feedback_iterate_with_qa_not_oneshot / last-run-wins incident 2026-07-03).
    When pipeline_versions carries the ``qa_approved_snapshot`` marker, the
    reject persists NOTHING over it — the task is restored to
    awaiting_approval with the stored approved version instead."""

    def _marker_row(self, score: int = 86):
        return {"quality_score": score, "has_qa_approved_snapshot": True}

    async def test_reject_with_marker_restores_awaiting_approval(self, monkeypatch):
        db = _DB(version_row=self._marker_row(86))
        upserts: list = []

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields):
                upserts.append((task_id, fields))

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        outcome = await persist_qa_reject(
            db, task_id="t1", reason="worse re-run", final_score=59.0,
            content="worse body", title="T", qa_feedback="fb",
            models_used_by_phase={},
        )
        assert outcome == "kept_qa_approved"
        # Task restored to the approved terminal state, at the APPROVED score.
        assert db.update_task_calls[0][1]["status"] == "awaiting_approval"
        assert db.update_task_calls[0][1]["quality_score"] == 86.0
        # The stored approved version was NOT overwritten by the worse draft.
        assert upserts == []
        # No negative learning signal for a draft we didn't keep.
        assert db.mark_calls == []
        # Gate history records the keep-best restore for the audit trail.
        kept = [args for sql, args in db.pool.execs if "pipeline_gate_history" in sql]
        assert kept and "kept_qa_approved" in kept[0]

    async def test_reject_without_marker_proceeds_normally(self, monkeypatch):
        db = _DB(version_row={"quality_score": 50, "has_qa_approved_snapshot": False})

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        outcome = await persist_qa_reject(
            db, task_id="t1", reason="bad", final_score=55.0,
            content="b", title="t", qa_feedback="fb", models_used_by_phase={},
        )
        assert outcome == "rejected"
        assert db.update_task_calls[0][1]["status"] == "rejected"

    async def test_marker_read_failure_falls_back_to_reject(self, monkeypatch):
        db = _DB()

        async def _boom(sql, *args):
            raise RuntimeError("db blip")

        db.pool.fetchrow = _boom

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        outcome = await persist_qa_reject(
            db, task_id="t1", reason="bad", final_score=55.0,
            content="b", title="t", qa_feedback="fb", models_used_by_phase={},
        )
        assert outcome == "rejected"
        assert db.update_task_calls[0][1]["status"] == "rejected"

    async def test_emits_finding_on_keep_best_restore(self, monkeypatch):
        db = _DB(version_row=self._marker_row(80))

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        calls: list[dict] = []
        monkeypatch.setattr(
            "modules.content.atoms._qa_persist.emit_finding",
            lambda **kw: calls.append(kw),
        )
        await persist_qa_reject(
            db, task_id="task5678", reason="bad", final_score=59.0,
            content="b", title="t", qa_feedback="fb", models_used_by_phase={},
        )
        assert len(calls) == 1
        assert calls[0]["kind"] == "qa_rerun_kept_approved_draft"
        assert calls[0]["severity"] == "warn"


@pytest.mark.unit
class TestPersistQaApprovedSnapshot:
    """Durable QA-approval snapshot: the approve decision is persisted the
    moment qa.aggregate makes it, so a crash in the ~9 nodes between QA
    approval and content.persist_task (SEO / media scripts / shot list — the
    observed overnight crash window) no longer loses the approved draft."""

    async def test_writes_version_with_marker(self, monkeypatch):
        db = _DB()
        captured = {}

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields):
                captured["upsert"] = (task_id, fields)

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        await persist_qa_approved_snapshot(
            db, task_id="t1", title="A Title", content="approved body",
            final_score=86.0, qa_feedback="fb",
            models_used_by_phase={"writer": "m"},
            featured_image_url="https://img",
        )
        task_id, fields = captured["upsert"]
        assert task_id == "t1"
        assert fields["content"] == "approved body"
        assert fields["quality_score"] == 86
        assert fields["featured_image_url"] == "https://img"
        marker = fields["qa_approved_snapshot"]
        assert marker["score"] == 86.0
        assert marker["approved_at"]  # ISO timestamp present

    async def test_noop_without_db_or_content(self):
        await persist_qa_approved_snapshot(
            None, task_id="t1", title="t", content="c", final_score=1.0,
            qa_feedback="f", models_used_by_phase={},
        )
        db = _DB()
        await persist_qa_approved_snapshot(
            db, task_id="t1", title="t", content="", final_score=1.0,
            qa_feedback="f", models_used_by_phase={},
        )

    async def test_emits_finding_when_write_fails(self, monkeypatch):
        db = _DB()

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields):
                raise RuntimeError("db down")

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        calls: list[dict] = []
        monkeypatch.setattr(
            "modules.content.atoms._qa_persist.emit_finding",
            lambda **kw: calls.append(kw),
        )
        # Must not raise — the graph continues toward persist_task regardless.
        await persist_qa_approved_snapshot(
            db, task_id="t1", title="t", content="c", final_score=80.0,
            qa_feedback="f", models_used_by_phase={},
        )
        assert len(calls) == 1
        assert calls[0]["kind"] == "qa_approved_snapshot_persist_failed"
        assert calls[0]["severity"] == "warn"
