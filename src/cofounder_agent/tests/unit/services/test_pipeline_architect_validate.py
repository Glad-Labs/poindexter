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


def test_real_registered_atoms_validate_with_defaults():
    """A spec of real registered atoms whose requires are seed/config/upstream
    satisfied must pass with default seed_keys — the new check must not break
    the architect's compose() path."""
    from services.atom_registry import discover, get_atom_meta as real_get

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
