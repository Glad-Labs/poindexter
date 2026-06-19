"""TemplateRunner.run refuses a drifted graph_def at load time (poindexter#755)."""
import pytest

import services.template_runner as tr
from services.pipeline_architect import GraphContractError
from services.site_config import SiteConfig


def _runner():
    runner = tr.TemplateRunner.__new__(tr.TemplateRunner)
    runner._pool = object()
    runner._site_config = SiteConfig(initial_config={"pipeline_use_graph_def": "true"})
    return runner


@pytest.mark.asyncio
async def test_run_refuses_drifted_graph_def(monkeypatch):
    drifted = {
        "name": "canonical_blog",
        "nodes": [{"id": "a", "atom": "atoms.draft", "config": {}}],
        "edges": [{"from": "a", "to": "END"}],
    }

    async def _fake_load(pool, slug):
        return drifted

    monkeypatch.setattr("services.pipeline_templates.load_active_graph_def", _fake_load)

    def _boom(spec):
        raise GraphContractError("FIX: drift")

    monkeypatch.setattr("services.pipeline_architect.assert_graph_def_current", _boom)

    # Isolate the gate wiring from the notification side-effect.
    async def _noop_emit(*a, **k):
        return None

    monkeypatch.setattr(tr, "_emit_progress", _noop_emit)

    with pytest.raises(GraphContractError):
        await _runner().run("canonical_blog", {"task_id": "t1"})


@pytest.mark.asyncio
async def test_run_notifies_operator_on_drift(monkeypatch):
    """The drift gate emits a progress/notify event before re-raising."""
    drifted = {
        "name": "canonical_blog",
        "nodes": [{"id": "a", "atom": "atoms.draft", "config": {}}],
        "edges": [{"from": "a", "to": "END"}],
    }

    async def _fake_load(pool, slug):
        return drifted

    monkeypatch.setattr("services.pipeline_templates.load_active_graph_def", _fake_load)
    monkeypatch.setattr(
        "services.pipeline_architect.assert_graph_def_current",
        lambda spec: (_ for _ in ()).throw(GraphContractError("FIX: drift")),
    )

    emitted: list = []

    async def _capture_emit(pool, *, event_type, payload, **k):
        emitted.append(event_type)

    monkeypatch.setattr(tr, "_emit_progress", _capture_emit)

    with pytest.raises(GraphContractError):
        await _runner().run("canonical_blog", {"task_id": "t1"})
    assert "template.contract_drift" in emitted


def test_graph_signature_channel_declared():
    # __graph_signature__ must be a declared PipelineState key or LangGraph
    # drops it on the graph_def path (undeclared-key lesson).
    assert "__graph_signature__" in tr.PipelineState.__annotations__


@pytest.mark.asyncio
async def test_discard_thread_checkpoints_guards_missing_tables():
    class _Conn:
        async def fetchval(self, sql, *a):
            return False  # to_regclass → tables absent

        async def execute(self, sql, *a):
            raise AssertionError("must not delete when checkpoint tables absent")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    deleted = await tr._discard_thread_checkpoints(_Pool(), "t1")
    assert deleted == 0


@pytest.mark.asyncio
async def test_discard_thread_checkpoints_deletes_when_present():
    executed: list = []

    class _Conn:
        async def fetchval(self, sql, *a):
            return True  # tables present

        async def execute(self, sql, *a):
            executed.append(sql)
            return "DELETE 2"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    deleted = await tr._discard_thread_checkpoints(_Pool(), "t1")
    # three checkpoint tables × "DELETE 2" each
    assert deleted == 6
    assert any("checkpoint_writes" in s for s in executed)
    assert any("checkpoint_blobs" in s for s in executed)
