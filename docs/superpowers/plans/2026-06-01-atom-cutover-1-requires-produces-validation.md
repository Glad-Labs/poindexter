# Atom Cutover — Plan 1: requires/produces build-time validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the pipeline spec validator so a node whose atom `requires` a state key that no upstream node `produces` (and that isn't in the node's `config` or the initial-state contract) fails at build/seed time with a `FIX:` message — instead of a runtime `KeyError` mid-pipeline.

**Architecture:** `services/pipeline_architect.py::_validate_spec` already checks name / node-atom existence / edge references / DAG-ness. Add one more block after the cycle check: a Kahn topological pass that accumulates available keys (`seed_keys ∪ each node's produces`) and flags any node whose `requires` aren't satisfied by upstream produces, the node's `config`, or `seed_keys`. `seed_keys` defaults to the declared `PipelineState` fields. This is the safety net the static-spec cutover (Plan 4) relies on; landing it first changes no runtime behavior (validation only) and accepts every currently-valid spec.

**Tech Stack:** Python 3.13, pytest (`poetry run pytest`), the existing `pipeline_architect` + `plugins/atom.py` (`AtomMeta.requires`/`produces`) + `template_runner.PipelineState`.

**Spec:** `docs/superpowers/specs/2026-06-01-canonical-blog-atom-cutover-design.md` (§ "requires/produces validation").

**Conventions:** run tests from `src/cofounder_agent` with `poetry run pytest <path> -v` (or the main venv's `python -m pytest` in a worktree). This repo forbids merge commits — feature branch, linear commits, normal push. Commit after each green task.

---

### Task 1: `seed_keys` parameter + `requires` reachability check

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py` — `_validate_spec` (signature + new block before `return (not errors), errors`)
- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the requires/produces reachability check in _validate_spec
(Glad-Labs/poindexter#355 atom-cutover Plan 1)."""

from unittest.mock import patch

import pytest

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
    # task_id is a declared PipelineState field, so a node requiring it must
    # validate with the DEFAULT seed_keys (no explicit seed_keys passed).
    catalog = {"b": _meta("b", requires=("task_id",))}
    spec = _spec(
        [{"id": "nb", "atom": "b"}],
        [{"from": "nb", "to": "END"}],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec)
    assert ok is True, errors
```

- [ ] **Step 2: Run, verify they fail**

Run: `python -m pytest tests/unit/services/test_pipeline_architect_validate.py -v`
Expected: `test_unsatisfied_requires_fails` FAILs (no check yet → `ok is True`); the others ERROR/FAIL on `TypeError: _validate_spec() got an unexpected keyword argument 'seed_keys'`.

- [ ] **Step 3: Implement** — change the `_validate_spec` signature and add the reachability block.

Signature (line ~357):

```python
def _validate_spec(
    spec: dict[str, Any], *, seed_keys: set[str] | None = None
) -> tuple[bool, list[str]]:
```

Insert this block immediately before the final `return (not errors), errors` (after the cycle-detection block, ~line 488):

```python
    # I/O contract check (#355): every node's atom.requires must be
    # satisfiable from an upstream node's produces, the node's own config,
    # or the initial-state contract (declared PipelineState fields). Catches
    # a mis-wired composition at build/seed time instead of a runtime
    # KeyError mid-pipeline — what makes "a pipeline is a JSON file" SAFE.
    if not errors:
        if seed_keys is None:
            # Lazy import dodges the template_runner <-> pipeline_architect cycle.
            from services.template_runner import PipelineState
            seed_keys = set(PipelineState.__annotations__)

        # Kahn topological order over the DAG (edges already validated acyclic).
        indeg = {nid: 0 for nid in seen_ids}
        adj2: dict[str, list[str]] = {nid: [] for nid in seen_ids}
        for e in edges:
            if e.get("to") != "END" and e.get("from") in seen_ids and e.get("to") in seen_ids:
                adj2[e["from"]].append(e["to"])
                indeg[e["to"]] += 1
        ready = [nid for nid in seen_ids if indeg[nid] == 0]
        order: list[str] = []
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for nxt in adj2[cur]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)

        node_by_id = {
            n["id"]: n for n in nodes
            if isinstance(n, dict) and isinstance(n.get("id"), str)
        }
        available: set[str] = set(seed_keys)
        for nid in order:
            n = node_by_id.get(nid)
            if n is None:
                continue
            meta = get_atom_meta(n.get("atom", ""))
            cfg_keys = set((n.get("config") or {}).keys())
            req = set(meta.requires) if meta else set()
            missing = req - available - cfg_keys
            if missing:
                errors.append(
                    f"FIX node {nid!r}: requires {sorted(missing)} but no "
                    "upstream node produces them, they're not in the node's "
                    "config, and they're not initial-state fields. Reorder so "
                    "a producer runs first, or seed them via config."
                )
            if meta:
                available |= set(meta.produces)
```

- [ ] **Step 4: Run, verify they pass**

Run: `python -m pytest tests/unit/services/test_pipeline_architect_validate.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py
git commit -m "feat(pipeline): validate atom requires/produces reachability at build time"
```

---

### Task 2: regression — existing architect path still validates real specs

Guards against the new check rejecting a currently-valid architect composition (the `compose()` caller uses the default `seed_keys`).

**Files:**

- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_real_registered_atoms_validate_with_defaults():
    """A spec composed only of real registered atoms whose requires are
    seed/config/upstream-satisfied must pass with default seed_keys — so the
    new check doesn't break the architect's compose() path."""
    from services.atom_registry import discover, get_atom_meta as real_get

    discover()  # populate the registry (idempotent)
    gate = real_get("atoms.approval_gate")
    assert gate is not None, "approval_gate atom must be registered"
    # approval_gate requires task_id (seed) + gate_name (config) — both
    # satisfiable, so a lone gate node must validate.
    spec = {
        "name": "gate_only",
        "entry": "g",
        "nodes": [{"id": "g", "atom": "atoms.approval_gate", "config": {"gate_name": "preview"}}],
        "edges": [{"from": "g", "to": "END"}],
    }
    ok, errors = pipeline_architect._validate_spec(spec)
    assert ok is True, errors
```

- [ ] **Step 2: Run, verify it passes** (Task 1's implementation already satisfies it; this pins the contract)

Run: `python -m pytest tests/unit/services/test_pipeline_architect_validate.py::test_real_registered_atoms_validate_with_defaults -v`
Expected: PASS. If it FAILS, the default `seed_keys` (PipelineState fields) is missing `task_id` — fix by confirming `task_id` is declared in `PipelineState` (`services/template_runner.py`), adding it if absent (it is a core state key).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py
git commit -m "test(pipeline): pin that real registered-atom specs still validate"
```

---

## Self-review notes

- **Spec coverage:** implements the spec's "requires/produces validation" section — the build/seed-time reachability check shared by `compose` (default `seed_keys`) and the future static-spec seed path (Plan 4 passes its own `seed_keys`). The other spec sections are separate plans (2–5).
- **Type consistency:** `seed_keys: set[str] | None`; `get_atom_meta(name) -> AtomMeta | None`; `AtomMeta.requires`/`.produces` are `tuple[str, ...]`; `_validate_spec(spec, *, seed_keys=None) -> tuple[bool, list[str]]`. Topo vars (`indeg`/`adj2`/`ready`/`order`) are local and don't collide with the cycle-check's `adj`.
- **No placeholders:** all test + implementation code is concrete. The one conditional (Task 2 Step 2 fallback) names the exact remedy.
- **Blast radius:** validation-only; no runtime/pipeline behavior change. `compose()` keeps calling `_validate_spec(spec)` unchanged (default seed_keys). Independently shippable.
