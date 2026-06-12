"""Tests for _validate_graph_schema — PipelineState schema validation at
compile time (Glad-Labs/poindexter#753).

LangGraph silently drops state updates whose keys are not declared in the
TypedDict schema. _validate_graph_schema, called from build_graph_from_spec,
surfaces this class of bug at graph compile time so a missing PipelineState
declaration fails loudly instead of silently losing data mid-pipeline.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plugins.atom import AtomMeta
from services import pipeline_architect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(name, *, requires=(), produces=()):
    return AtomMeta(
        name=name, type="atom", version="1.0.0", description=name,
        requires=tuple(requires), produces=tuple(produces),
    )


def _spec_nodes(nodes):
    """Minimal spec dict with just nodes (no edges needed for schema checks)."""
    return {
        "name": "test_spec",
        "entry": nodes[0]["id"] if nodes else "n0",
        "nodes": nodes,
        "edges": [],
    }


def _fake_get_atom_meta(catalog):
    return lambda atom: catalog.get(atom)


# ---------------------------------------------------------------------------
# _validate_graph_schema — produces-key checks
# ---------------------------------------------------------------------------


def test_produces_all_declared_passes():
    """All produces keys in PipelineState → no error."""
    # 'content' and 'title' are declared in PipelineState.
    catalog = {"a": _meta("a", produces=("content", "title"))}
    spec = _spec_nodes([{"id": "n0", "atom": "a"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        # Should not raise.
        pipeline_architect._validate_graph_schema(
            spec, state_keys=frozenset({"content", "title", "task_id"})
        )


def test_produces_undeclared_key_raises():
    """Atom produces a key not in PipelineState → ValueError naming the key."""
    catalog = {"a": _meta("a", produces=("undeclared_mystery_key",))}
    spec = _spec_nodes([{"id": "n0", "atom": "a"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        with pytest.raises(ValueError) as exc_info:
            pipeline_architect._validate_graph_schema(
                spec, state_keys=frozenset({"content", "title"})
            )
    msg = str(exc_info.value)
    assert "undeclared_mystery_key" in msg
    assert "a" in msg  # atom name
    assert "produces" in msg


def test_produces_undeclared_key_names_node_id():
    """Error message includes the offending node id for quick lookup."""
    catalog = {"qa.critic": _meta("qa.critic", produces=("qa_critic_score",))}
    spec = _spec_nodes([{"id": "qa_critic_node", "atom": "qa.critic"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        with pytest.raises(ValueError) as exc_info:
            pipeline_architect._validate_graph_schema(
                spec, state_keys=frozenset({"content"}),
            )
    msg = str(exc_info.value)
    assert "qa_critic_score" in msg
    assert "qa.critic" in msg
    assert "qa_critic_node" in msg


def test_produces_multiple_undeclared_keys_all_reported():
    """All undeclared produces keys appear in the error, not just the first."""
    catalog = {
        "a": _meta("a", produces=("alpha", "beta", "gamma")),
    }
    spec = _spec_nodes([{"id": "n0", "atom": "a"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        with pytest.raises(ValueError) as exc_info:
            pipeline_architect._validate_graph_schema(
                spec, state_keys=frozenset(),  # nothing declared
            )
    msg = str(exc_info.value)
    assert "alpha" in msg
    assert "beta" in msg
    assert "gamma" in msg


def test_produces_multiple_atoms_bad_keys_all_reported():
    """Multiple atoms with undeclared produces keys all appear in one error."""
    catalog = {
        "a": _meta("a", produces=("bad_a",)),
        "b": _meta("b", produces=("bad_b",)),
    }
    spec = _spec_nodes([
        {"id": "n0", "atom": "a"},
        {"id": "n1", "atom": "b"},
    ])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        with pytest.raises(ValueError) as exc_info:
            pipeline_architect._validate_graph_schema(
                spec, state_keys=frozenset(),
            )
    msg = str(exc_info.value)
    assert "bad_a" in msg
    assert "bad_b" in msg


# ---------------------------------------------------------------------------
# _validate_graph_schema — requires-key checks
# ---------------------------------------------------------------------------


def test_requires_undeclared_key_raises():
    """Atom requires a key not in PipelineState → ValueError naming the key."""
    catalog = {"a": _meta("a", requires=("ghost_key",))}
    spec = _spec_nodes([{"id": "n0", "atom": "a"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        with pytest.raises(ValueError) as exc_info:
            pipeline_architect._validate_graph_schema(
                spec, state_keys=frozenset({"content"}),
            )
    msg = str(exc_info.value)
    assert "ghost_key" in msg
    assert "requires" in msg


def test_requires_declared_key_passes():
    """Atom requires a key that IS declared in PipelineState → no error."""
    catalog = {"a": _meta("a", requires=("task_id", "content"))}
    spec = _spec_nodes([{"id": "n0", "atom": "a"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        # Should not raise.
        pipeline_architect._validate_graph_schema(
            spec, state_keys=frozenset({"task_id", "content", "title"})
        )


# ---------------------------------------------------------------------------
# _validate_graph_schema — unknown atoms skipped (handled by _validate_spec)
# ---------------------------------------------------------------------------


def test_unknown_atom_skipped_gracefully():
    """A node with an unregistered atom name is skipped (no crash)."""
    catalog: dict = {}  # empty — nothing registered
    spec = _spec_nodes([{"id": "n0", "atom": "nonexistent.atom"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        # Should not raise — unknown atoms are flagged by _validate_spec, not here.
        pipeline_architect._validate_graph_schema(
            spec, state_keys=frozenset({"task_id"}),
        )


# ---------------------------------------------------------------------------
# _validate_graph_schema — default state_keys from live PipelineState
# ---------------------------------------------------------------------------


def test_default_state_keys_from_pipeline_state():
    """When state_keys is omitted, PipelineState.__annotations__ is used."""
    # 'content' and 'task_id' are real PipelineState keys.
    catalog = {"a": _meta("a", produces=("content",), requires=("task_id",))}
    spec = _spec_nodes([{"id": "n0", "atom": "a"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        # Should not raise — both keys are declared in real PipelineState.
        pipeline_architect._validate_graph_schema(spec)


def test_default_state_keys_rejects_undeclared():
    """With real PipelineState keys, a truly undeclared key raises."""
    catalog = {"a": _meta("a", produces=("absolutely_not_in_pipeline_state_753",))}
    spec = _spec_nodes([{"id": "n0", "atom": "a"}])
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        with pytest.raises(ValueError) as exc_info:
            pipeline_architect._validate_graph_schema(spec)
    assert "absolutely_not_in_pipeline_state_753" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Integration: build_graph_from_spec calls _validate_graph_schema
# ---------------------------------------------------------------------------


def test_build_graph_from_spec_calls_schema_validation(monkeypatch):
    """build_graph_from_spec must call _validate_graph_schema, not skip it."""
    validation_calls: list[dict] = []

    def _recording_validate(spec, *, state_keys=None):
        validation_calls.append({"spec": spec, "state_keys": state_keys})

    monkeypatch.setattr(pipeline_architect, "_validate_graph_schema", _recording_validate)

    # Use the approval_gate atom — it's real and has all keys declared.
    from services.atom_registry import discover
    discover()

    spec = {
        "name": "schema_check_test",
        "entry": "g",
        "nodes": [{"id": "g", "atom": "atoms.approval_gate", "config": {"gate_name": "x"}}],
        "edges": [{"from": "g", "to": "END"}],
    }
    pipeline_architect.build_graph_from_spec(spec, pool=None)
    assert len(validation_calls) == 1, (
        "build_graph_from_spec must call _validate_graph_schema exactly once"
    )


def test_build_graph_from_spec_raises_on_undeclared_produces(monkeypatch):
    """build_graph_from_spec raises ValueError when an atom produces an
    undeclared PipelineState key — the key error is propagated to the caller."""
    from services.atom_registry import discover
    discover()

    original_meta = pipeline_architect.get_atom_meta

    def _patched_meta(name):
        m = original_meta(name)
        if m is not None and name == "atoms.approval_gate":
            # Inject an undeclared produces key onto the real atom.
            return AtomMeta(
                name=m.name, type=m.type, version=m.version,
                description=m.description, inputs=m.inputs, outputs=m.outputs,
                requires=m.requires,
                produces=("gate_artifact", "totally_undeclared_753_test_key"),
                capability_tier=m.capability_tier, cost_class=m.cost_class,
            )
        return m

    monkeypatch.setattr(pipeline_architect, "get_atom_meta", _patched_meta)

    spec = {
        "name": "bad_spec",
        "entry": "g",
        "nodes": [{"id": "g", "atom": "atoms.approval_gate", "config": {"gate_name": "x"}}],
        "edges": [{"from": "g", "to": "END"}],
    }
    with pytest.raises(ValueError) as exc_info:
        pipeline_architect.build_graph_from_spec(spec, pool=None)
    assert "totally_undeclared_753_test_key" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Regression: real canonical_blog atoms all declare PipelineState keys
# ---------------------------------------------------------------------------


def test_real_registered_atoms_all_produce_declared_keys():
    """Every registered atom's produces list must reference a declared
    PipelineState key. This is the core regression guard for #753 — any
    future atom that produces an undeclared key will fail here immediately."""
    from services.atom_registry import discover, list_atoms
    from services.template_runner import PipelineState

    discover()
    state_keys = frozenset(PipelineState.__annotations__)
    failures: list[str] = []

    for meta in list_atoms():
        # Stage virtual atoms inherit produces=() from _stage_to_atom_meta
        # (they don't declare explicit produces); skip them — they're legacy
        # and don't participate in the graph_def state merge.
        if meta.type == "stage":
            continue
        for key in meta.produces:
            if key not in state_keys:
                failures.append(
                    f"Atom {meta.name!r} produces {key!r} not in PipelineState"
                )
        for key in meta.requires:
            if key not in state_keys:
                failures.append(
                    f"Atom {meta.name!r} requires {key!r} not in PipelineState"
                )

    assert not failures, (
        "Undeclared PipelineState keys found (#753):\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
