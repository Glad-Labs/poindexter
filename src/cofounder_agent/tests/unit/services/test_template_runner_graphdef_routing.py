"""Routing tests for the graph_def cutover seam in TemplateRunner.run
(atom-cutover Plan 4, #355). Mirrors the harness in
test_template_runner_state_partition.py: a trivial StateGraph + a
SiteConfig with the Postgres checkpointer off (→ MemorySaver), pool=None."""

from __future__ import annotations

import pytest
from langgraph.graph import END, StateGraph

from services import pipeline_architect
from services.pipeline_templates import TEMPLATES
from services.site_config import SiteConfig
from services.template_runner import PipelineState, TemplateRunner


def _trivial_graph() -> StateGraph:
    g: StateGraph = StateGraph(PipelineState)

    async def _noop(state, config=None):
        return {}

    g.add_node("noop", _noop)
    g.set_entry_point("noop")
    g.add_edge("noop", END)
    return g


def _runner(flag: bool) -> TemplateRunner:
    return TemplateRunner(
        pool=None,
        checkpointer_dsn=None,
        site_config=SiteConfig(initial_config={
            "template_runner_use_postgres_checkpointer": "false",
            "pipeline_use_graph_def": "true" if flag else "false",
        }),
    )


@pytest.mark.unit
class TestGraphDefRouting:
    async def test_flag_off_uses_legacy_factory(self, monkeypatch):
        calls = {"factory": 0, "build": 0}

        def fake_factory(*, pool, record_sink=None):
            calls["factory"] += 1
            return _trivial_graph()

        def fake_build(spec, *, pool, record_sink=None, on_event=None):
            calls["build"] += 1
            return _trivial_graph()

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)
        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)

        summary = await _runner(False).run("canonical_blog", {"task_id": "t1"})
        assert summary.ok is True
        assert calls["factory"] == 1
        assert calls["build"] == 0

    async def test_flag_on_with_graph_def_uses_build(self, monkeypatch):
        calls = {"factory": 0, "build": 0}

        def fake_factory(*, pool, record_sink=None):
            calls["factory"] += 1
            return _trivial_graph()

        def fake_build(spec, *, pool, record_sink=None, on_event=None):
            calls["build"] += 1
            return _trivial_graph()

        async def fake_load(pool, slug):
            return {"name": slug, "entry": "noop", "nodes": [{"id": "noop", "atom": "x"}], "edges": []}

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)
        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)
        # The runner does `from services.pipeline_templates import load_active_graph_def`
        # lazily inside run(); patch it on that module so the lazy import resolves
        # to the fake at call time.
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "load_active_graph_def", fake_load)

        summary = await _runner(True).run("canonical_blog", {"task_id": "t2"})
        assert summary.ok is True
        assert calls["build"] == 1
        assert calls["factory"] == 0

    async def test_flag_on_no_graph_def_falls_back_to_factory(self, monkeypatch):
        calls = {"factory": 0, "build": 0}

        def fake_factory(*, pool, record_sink=None):
            calls["factory"] += 1
            return _trivial_graph()

        def fake_build(spec, *, pool, record_sink=None, on_event=None):
            calls["build"] += 1
            return _trivial_graph()

        async def fake_load(pool, slug):
            return None  # no active graph_def row

        monkeypatch.setitem(TEMPLATES, "canonical_blog", fake_factory)
        monkeypatch.setattr(pipeline_architect, "build_graph_from_spec", fake_build)
        import services.pipeline_templates as pt
        monkeypatch.setattr(pt, "load_active_graph_def", fake_load)

        summary = await _runner(True).run("canonical_blog", {"task_id": "t3"})
        assert summary.ok is True
        assert calls["factory"] == 1
        assert calls["build"] == 0
