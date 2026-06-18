"""Tests for the requires/produces reachability check in _validate_spec
(Glad-Labs/poindexter#355 atom-cutover Plan 1)."""

from unittest.mock import patch

from plugins.atom import AtomMeta
from services import pipeline_architect


def _meta(name, *, requires=(), produces=()):
    return AtomMeta(
        name=name, type="atom", version="1.0.0", description=name,
        requires=tuple(requires), produces=tuple(produces),
    )


def _spec(nodes, edges, *, entry=None):
    return {"name": "t", "entry": entry or nodes[0]["id"], "nodes": nodes, "edges": edges}


def _fake_get_atom_meta(catalog):
    return lambda atom: catalog.get(atom)


def test_unsatisfied_requires_fails():
    catalog = {"a": _meta("a"), "b": _meta("b", requires=("x",))}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}],
        [{"from": "na", "to": "nb"}, {"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("nb" in e and "x" in e for e in errors), errors


def test_requires_satisfied_by_upstream_produces():
    catalog = {"a": _meta("a", produces=("x",)), "b": _meta("b", requires=("x",))}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}],
        [{"from": "na", "to": "nb"}, {"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors


def test_requires_satisfied_by_config():
    catalog = {"b": _meta("b", requires=("x",))}
    spec = _spec(
        [{"id": "nb", "atom": "b", "config": {"x": 1}}],
        [{"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors


def test_requires_satisfied_by_seed_state():
    catalog = {"b": _meta("b", requires=("task_id",))}
    spec = _spec(
        [{"id": "nb", "atom": "b"}],
        [{"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys={"task_id"})
    assert ok is True, errors


def test_default_seed_keys_come_from_pipeline_state():
    catalog = {"b": _meta("b", requires=("task_id",))}
    spec = _spec(
        [{"id": "nb", "atom": "b"}],
        [{"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec)
    assert ok is True, errors


import pytest


@pytest.mark.asyncio
async def test_wrap_atom_observes_node_duration_seconds():
    """_wrap_atom must observe NODE_DURATION_SECONDS so atom durations appear
    in the Pipeline dashboard (poindexter#652 regression guard).

    The histogram is labeled by (node, outcome). Both the success and error
    branches must call .labels(...).observe(elapsed_seconds).
    """
    from unittest.mock import MagicMock, patch

    import services.template_runner as _tr
    from services.pipeline_architect import _wrap_atom

    mock_histogram = MagicMock()

    async def _fast_atom(state):
        return {"out_key": "done"}

    with patch.object(_tr, "NODE_DURATION_SECONDS", mock_histogram):
        node_fn = _wrap_atom(_fast_atom, "atoms.test_atom", "node_ok", record_sink=None)
        await node_fn({}, None)

    mock_histogram.labels.assert_called_once()
    call_kwargs = mock_histogram.labels.call_args
    assert call_kwargs.kwargs.get("node") == "atoms.test_atom"
    assert call_kwargs.kwargs.get("outcome") in ("ok", "halted")
    mock_histogram.labels.return_value.observe.assert_called_once()
    elapsed = mock_histogram.labels.return_value.observe.call_args.args[0]
    assert elapsed >= 0


@pytest.mark.asyncio
async def test_wrap_atom_observes_error_outcome():
    """Exceptions from the atom fn must emit outcome='error' to NODE_DURATION_SECONDS."""
    from unittest.mock import MagicMock, patch

    import services.template_runner as _tr
    from services.pipeline_architect import _wrap_atom

    mock_histogram = MagicMock()

    async def _failing_atom(state):
        raise ValueError("test failure")

    with patch.object(_tr, "NODE_DURATION_SECONDS", mock_histogram):
        node_fn = _wrap_atom(_failing_atom, "atoms.fail_atom", "node_err", record_sink=None)
        result = await node_fn({}, None)

    assert result.get("_halt") is True
    mock_histogram.labels.assert_called_once_with(node="atoms.fail_atom", outcome="error")
    mock_histogram.labels.return_value.observe.assert_called_once()


def test_real_registered_atoms_validate_with_defaults():
    """A spec of real registered atoms whose requires are seed/config/upstream
    satisfied must pass with default seed_keys — the new check must not break
    the architect's compose() path."""
    from services.atom_registry import discover
    from services.atom_registry import get_atom_meta as real_get

    discover()  # idempotent
    gate = real_get("atoms.approval_gate")
    assert gate is not None, "approval_gate atom must be registered"
    spec = {
        "name": "gate_only",
        "entry": "g",
        "nodes": [{"id": "g", "atom": "atoms.approval_gate", "config": {"gate_name": "preview"}}],
        "edges": [{"from": "g", "to": "END"}],
    }
    ok, errors = pipeline_architect._validate_spec(spec)
    assert ok is True, errors


# ---------------------------------------------------------------------------
# QA rescue cycle: loop-flagged back-edges are exempt from DAG validation,
# while unflagged accidental cycles still fail loud.
# ---------------------------------------------------------------------------


def test_loop_flagged_back_edge_validates():
    # a -> b -> c, with c -> a flagged "loop": the designated rescue cycle.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c")}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na", "loop": True},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors


def test_unflagged_back_edge_still_errors():
    # Same shape but WITHOUT the loop flag — an accidental cycle must fail loud.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c")}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na"},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("cycle" in e.lower() for e in errors), errors


def test_loop_edge_does_not_drop_downstream_require_check():
    # The loop edge must not inflate the loopback target's indegree and silently
    # drop the whole chain from the requires-reachability pass. nc requires "k"
    # which nothing produces -> the check must still fire and error on nc.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c", requires=("k",))}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na", "loop": True},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("nc" in e and "k" in e for e in errors), errors
