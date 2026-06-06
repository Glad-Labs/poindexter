"""Wiring tests for atom-runs capture (atom-cutover Plan 2, #355 / #552).

The writers (``persist_atom_runs`` / ``record_atom_run_outcome``) are
exercised directly in test_atom_runs.py + test_atom_runs_roundtrip.py.
These tests pin the *wiring* added in #552:

1. ``TemplateRunner.run`` drains the run's ``record_sink`` into
   ``persist_atom_runs`` (run_id == thread_id, task_id from state) — this
   is what makes ``atom_runs`` populate on every graph_def run.
2. ``record_post_approve_metrics`` backfills the approve outcome
   (decision="approved" + edit_distance=char_diff) via
   ``record_atom_run_outcome``.

Both call sites are best-effort; the assertions only check that the call
happens with the right arguments — the writers' own behavior is tested
elsewhere.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, StateGraph

import services.atom_runs as atom_runs
from modules.content.auto_publish_gate import record_post_approve_metrics
from services.pipeline_templates import TEMPLATES
from services.site_config import SiteConfig
from services.template_runner import (
    PipelineState,
    TemplateRunner,
    TemplateRunRecord,
)

# ---------------------------------------------------------------------------
# Part A — TemplateRunner.run drains records into persist_atom_runs
# ---------------------------------------------------------------------------


def _runner() -> TemplateRunner:
    # Postgres checkpointer off → MemorySaver; pool=None (the faked
    # persist_atom_runs never touches it). graph_def flag off → the
    # monkeypatched TEMPLATES factory is used.
    return TemplateRunner(
        pool=None,
        checkpointer_dsn=None,
        site_config=SiteConfig(initial_config={
            "template_runner_use_postgres_checkpointer": "false",
            "pipeline_use_graph_def": "false",
        }),
    )


@pytest.mark.unit
class TestRunnerDrainsAtomRuns:
    async def test_run_calls_persist_atom_runs_with_run_records(
        self, monkeypatch
    ):
        captured: dict[str, Any] = {}

        async def fake_persist(
            pool, *, run_id, task_id, template_slug, records, site_config=None
        ):
            captured.update(
                run_id=run_id,
                task_id=task_id,
                template_slug=template_slug,
                records=records,
                site_config=site_config,
            )
            return len(records)

        # The runner imports persist_atom_runs lazily from services.atom_runs
        # inside run(); patch the source attribute so the lazy import resolves
        # to the fake at call time.
        monkeypatch.setattr(atom_runs, "persist_atom_runs", fake_persist)

        def fake_factory(*, pool, record_sink=None):
            # Append a record so we can assert the SAME sink list reaches
            # persist_atom_runs (the node-append → drain contract).
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(name="atoms.demo", ok=True, node_id="n1")
                )
            g: StateGraph = StateGraph(PipelineState)

            async def _noop(state, config=None):
                return {}

            g.add_node("noop", _noop)
            g.set_entry_point("noop")
            g.add_edge("noop", END)
            return g

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)

        summary = await _runner().run("canonical_blog", {"task_id": "tX"})

        assert summary.ok is True
        # run_id defaults to thread_id which defaults to task_id.
        assert captured.get("run_id") == "tX"
        assert captured.get("task_id") == "tX"
        assert captured.get("template_slug") == "canonical_blog"
        # The drained list is the same sink the factory appended to.
        recs = captured.get("records") or []
        assert len(recs) == 1
        assert recs[0].name == "atoms.demo"
        # The run-bound SiteConfig is forwarded so the capture gate is read.
        assert captured.get("site_config") is not None

    async def test_persist_failure_does_not_fail_run(self, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("capture exploded")

        monkeypatch.setattr(atom_runs, "persist_atom_runs", boom)

        def fake_factory(*, pool, record_sink=None):
            g: StateGraph = StateGraph(PipelineState)

            async def _noop(state, config=None):
                return {}

            g.add_node("noop", _noop)
            g.set_entry_point("noop")
            g.add_edge("noop", END)
            return g

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)

        # A capture exception must be swallowed — the run still succeeds.
        summary = await _runner().run("canonical_blog", {"task_id": "tY"})
        assert summary.ok is True


# ---------------------------------------------------------------------------
# Part B — record_post_approve_metrics backfills the approve outcome
# ---------------------------------------------------------------------------


class _Conn:
    def __init__(self, sink: list):
        self._sink = sink

    async def execute(self, sql: str, *args: Any):
        self._sink.append((sql, args))

    async def fetchrow(self, *a: Any, **kw: Any):
        return None

    async def fetch(self, *a: Any, **kw: Any):
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _Pool:
    def __init__(self):
        self.executed: list = []
        self._conn = _Conn(self.executed)

    def acquire(self):
        return _Acquire(self._conn)


@pytest.mark.unit
class TestApproveOutcomeBackfill:
    async def test_records_approved_outcome_with_edit_distance(
        self, monkeypatch
    ):
        calls: list[dict[str, Any]] = []

        async def fake_outcome(
            pool, *, task_id, run_id=None, post_id=None, decision=None,
            quality_score=None, edit_distance=None,
        ):
            calls.append(dict(
                task_id=task_id, decision=decision, edit_distance=edit_distance,
            ))
            return 1

        monkeypatch.setattr(atom_runs, "record_atom_run_outcome", fake_outcome)

        pool = _Pool()
        ok = await record_post_approve_metrics(
            pool,
            task_id="t9",
            pre_approve_content="the original draft body",
            post_approve_content="the edited, approved body text",
            niche_slug="glad-labs",
        )

        assert ok is True
        assert len(calls) == 1
        assert calls[0]["task_id"] == "t9"
        assert calls[0]["decision"] == "approved"
        # edit_distance is the computed char_diff — non-negative, and the
        # two strings differ so it should be > 0.
        assert calls[0]["edit_distance"] > 0

    async def test_outcome_backfill_failure_does_not_fail_metrics(
        self, monkeypatch
    ):
        async def boom(*a, **kw):
            raise RuntimeError("backfill exploded")

        monkeypatch.setattr(atom_runs, "record_atom_run_outcome", boom)

        pool = _Pool()
        # The edit-metrics row still persists + returns True even if the
        # outcome backfill raises (best-effort).
        ok = await record_post_approve_metrics(
            pool,
            task_id="t10",
            pre_approve_content="a",
            post_approve_content="b",
        )
        assert ok is True
