# Atom Contract Fingerprint Handshake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stamp every stored `graph_def` node with the atom's I/O-contract fingerprint at seed time, refuse to run a graph whose atoms have drifted, and discard checkpoints created under a different graph signature.

**Architecture:** A pure `AtomMeta.contract_fingerprint()` hashes the structural contract (`requires`/`produces`/typed `inputs`/`outputs`). `stamp_graph_def` writes `_contract_fp` + `_atom_version` per node at both write paths (architect `cache_template` + a re-stamp migration for existing rows). `assert_graph_def_current` re-derives and compares at load time in `TemplateRunner.run`, raising `GraphContractError` (fail-loud + notify). A `graph_signature` rides the checkpointed state as `__graph_signature__`; on a resume with a mismatched signature the thread's checkpoint rows are deleted and the run starts fresh.

**Tech Stack:** Python 3.11, asyncpg, LangGraph (StateGraph + AsyncPostgresSaver checkpointer), pytest/pytest-asyncio.

## Global Constraints

- **Repo:** implement in `glad-labs-stack`; it mirrors to `poindexter`. The issue (poindexter#755) closes on merge via the PR body.
- **Fail loud, no silent defaults** (`feedback_no_silent_defaults`): contract drift raises + `notify_operator`; never degrade to `None`/legacy-factory fallback.
- **Fingerprint excludes** `description`, `cost_class`, `capability_tier`, `retry`, `side_effects`, `fallback`, `parallelizable`, `idempotent` — only `requires`, `produces`, and `inputs`/`outputs` `(name, type, required)` are hashed.
- **Migrations are schema/DDL + data mutations only** (`feedback_seed_data_in_baseline_not_new_migrations`); this migration mutates existing `pipeline_templates.graph_def` data (stamping), which is a legitimate data mutation, not a settings seed. It MUST stay import-safe for `scripts/ci/migrations_smoke.py` (guard the registry import).
- **Test command (run from `src/cofounder_agent`):** `poetry run pytest <path> -q` (verified working in this worktree).
- **asyncpg casts** (`reference_asyncpg_type_cast_quirks`): `thread_id`/`task_id` are text — cast `::text[]` not `::uuid[]`.
- **Commit style:** Conventional Commits; every commit ends with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `AtomMeta.contract_fingerprint()`

**Files:**

- Modify: `src/cofounder_agent/plugins/atom.py` (add method to `AtomMeta`, ~line 122 after `to_jsonb`)
- Test: `src/cofounder_agent/tests/unit/plugins/test_atom_fingerprint.py` (create)

**Interfaces:**

- Consumes: `AtomMeta` (`name`, `requires: tuple[str,...]`, `produces: tuple[str,...]`, `inputs: tuple[FieldSpec,...]`, `outputs: tuple[FieldSpec,...]`), `FieldSpec(name, type, description, required)`.
- Produces: `AtomMeta.contract_fingerprint() -> str` — 12-hex-char sha256 over the canonical structural contract.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/plugins/test_atom_fingerprint.py
"""contract_fingerprint() — structural drift tripwire (poindexter#755)."""
from plugins.atom import AtomMeta, FieldSpec


def _meta(**kw):
    base = dict(name="atoms.x", type="atom", version="1.0.0", description="d")
    base.update(kw)
    return AtomMeta(**base)


class TestContractFingerprint:
    def test_stable_for_identical_contract(self):
        a = _meta(requires=("draft",), produces=("title",))
        b = _meta(requires=("draft",), produces=("title",))
        assert a.contract_fingerprint() == b.contract_fingerprint()

    def test_is_short_hex(self):
        fp = _meta().contract_fingerprint()
        assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)

    def test_changes_when_requires_changes(self):
        a = _meta(requires=("draft",))
        b = _meta(requires=("draft", "context_bundle"))
        assert a.contract_fingerprint() != b.contract_fingerprint()

    def test_changes_when_produces_changes(self):
        a = _meta(produces=("title",))
        b = _meta(produces=("title", "slug"))
        assert a.contract_fingerprint() != b.contract_fingerprint()

    def test_changes_when_input_field_type_changes(self):
        a = _meta(inputs=(FieldSpec(name="n", type="str"),))
        b = _meta(inputs=(FieldSpec(name="n", type="int"),))
        assert a.contract_fingerprint() != b.contract_fingerprint()

    def test_unchanged_when_only_description_changes(self):
        a = _meta(description="old", requires=("draft",))
        b = _meta(description="totally different", requires=("draft",))
        assert a.contract_fingerprint() == b.contract_fingerprint()

    def test_unchanged_when_only_field_description_changes(self):
        a = _meta(inputs=(FieldSpec(name="n", type="str", description="x"),))
        b = _meta(inputs=(FieldSpec(name="n", type="str", description="y"),))
        assert a.contract_fingerprint() == b.contract_fingerprint()

    def test_requires_order_independent(self):
        a = _meta(requires=("a", "b"))
        b = _meta(requires=("b", "a"))
        assert a.contract_fingerprint() == b.contract_fingerprint()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/plugins/test_atom_fingerprint.py -q`
Expected: FAIL — `AttributeError: 'AtomMeta' object has no attribute 'contract_fingerprint'`

- [ ] **Step 3: Write minimal implementation**

Add to `plugins/atom.py` inside `class AtomMeta`, directly after `to_jsonb` (before the closing of the class). Ensure `hashlib` and `json` are imported at the top of the module (add if absent).

```python
    def contract_fingerprint(self) -> str:
        """Stable 12-hex digest of this atom's *structural I/O contract*.

        Hashes only the parts that determine whether a stored graph_def's
        wiring is still valid: ``requires``, ``produces``, and each
        ``inputs``/``outputs`` field's ``(name, type, required)``. Excludes
        description, cost, tier, retry, side_effects, etc. — changing those
        must NOT trip the graph_def drift gate (poindexter#755).
        """
        import hashlib
        import json

        def _fields(fs: tuple[FieldSpec, ...]) -> list[list[Any]]:
            # sorted by name; (name, type, required) only — description excluded
            return [[f.name, f.type, f.required] for f in sorted(fs, key=lambda x: x.name)]

        payload = {
            "requires": sorted(self.requires),
            "produces": sorted(self.produces),
            "inputs": _fields(self.inputs),
            "outputs": _fields(self.outputs),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/plugins/test_atom_fingerprint.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/plugins/atom.py src/cofounder_agent/tests/unit/plugins/test_atom_fingerprint.py
git commit -m "feat(pipeline): AtomMeta.contract_fingerprint() structural digest (#755)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `stamp_graph_def`, `GraphContractError`, `assert_graph_def_current`, `graph_signature`

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py` (add near the top-level helpers; `GraphContractError` next to other module exceptions; functions before `cache_template`)
- Test: `src/cofounder_agent/tests/unit/services/test_graph_def_contract.py` (create)

**Interfaces:**

- Consumes: `get_atom_meta(name) -> AtomMeta | None` (from `services.atom_registry`); `AtomMeta.contract_fingerprint()` (Task 1).
- Produces:
  - `stamp_graph_def(spec: dict[str, Any]) -> dict[str, Any]` — returns a deep-copied spec with each node carrying `_contract_fp` (str) and `_atom_version` (str); raises `GraphContractError` if a node names an unknown atom.
  - `class GraphContractError(RuntimeError)`
  - `assert_graph_def_current(spec: dict[str, Any]) -> None` — raises `GraphContractError` if any node is unstamped, names a missing atom, or whose stored `_contract_fp` != the atom's current fingerprint.
  - `graph_signature(spec: dict[str, Any]) -> str` — 12-hex digest over each node's `(id, _contract_fp)` plus the edge list.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/test_graph_def_contract.py
"""graph_def contract stamping + drift gate (poindexter#755)."""
import pytest

from plugins.atom import AtomMeta
import services.pipeline_architect as pa


def _meta(name, **kw):
    base = dict(name=name, type="atom", version="1.0.0", description="d")
    base.update(kw)
    return AtomMeta(**base)


@pytest.fixture
def registry(monkeypatch):
    table = {
        "atoms.draft": _meta("atoms.draft", produces=("draft",)),
        "atoms.title": _meta("atoms.title", requires=("draft",), produces=("title",)),
    }
    monkeypatch.setattr(pa, "get_atom_meta", lambda n: table.get(n))
    return table


def _spec():
    return {
        "name": "t",
        "nodes": [
            {"id": "a", "atom": "atoms.draft", "config": {}},
            {"id": "b", "atom": "atoms.title", "config": {}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
    }


class TestStamp:
    def test_stamps_every_node(self, registry):
        out = pa.stamp_graph_def(_spec())
        for n in out["nodes"]:
            assert n["_contract_fp"] and n["_atom_version"] == "1.0.0"

    def test_does_not_mutate_input(self, registry):
        spec = _spec()
        pa.stamp_graph_def(spec)
        assert "_contract_fp" not in spec["nodes"][0]

    def test_unknown_atom_raises(self, registry):
        spec = _spec()
        spec["nodes"][0]["atom"] = "atoms.nope"
        with pytest.raises(pa.GraphContractError):
            pa.stamp_graph_def(spec)


class TestAssertCurrent:
    def test_passes_when_current(self, registry):
        pa.assert_graph_def_current(pa.stamp_graph_def(_spec()))

    def test_unstamped_node_raises(self, registry):
        with pytest.raises(pa.GraphContractError, match="re-seed"):
            pa.assert_graph_def_current(_spec())

    def test_drift_raises_with_atom_name(self, registry):
        stamped = pa.stamp_graph_def(_spec())
        # atom 'atoms.title' contract changes underneath the stamp
        registry["atoms.title"] = _meta(
            "atoms.title", requires=("draft", "outline"), produces=("title",)
        )
        with pytest.raises(pa.GraphContractError, match="atoms.title"):
            pa.assert_graph_def_current(stamped)

    def test_missing_atom_raises(self, registry):
        stamped = pa.stamp_graph_def(_spec())
        del registry["atoms.title"]
        with pytest.raises(pa.GraphContractError, match="atoms.title"):
            pa.assert_graph_def_current(stamped)


class TestGraphSignature:
    def test_stable(self, registry):
        s = pa.stamp_graph_def(_spec())
        assert pa.graph_signature(s) == pa.graph_signature(pa.stamp_graph_def(_spec()))

    def test_changes_when_node_fp_changes(self, registry):
        s = pa.stamp_graph_def(_spec())
        s["nodes"][0]["_contract_fp"] = "deadbeefcafe"
        assert pa.graph_signature(s) != pa.graph_signature(pa.stamp_graph_def(_spec()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/test_graph_def_contract.py -q`
Expected: FAIL — `AttributeError: module 'services.pipeline_architect' has no attribute 'stamp_graph_def'`

- [ ] **Step 3: Write minimal implementation**

In `services/pipeline_architect.py`. Confirm `from services.atom_registry import get_atom_meta, list_atoms` exists near the top (it is already imported for `_validate_spec`); if `get_atom_meta` is not in the import, add it. Add near the other module-level helpers (above `cache_template`):

```python
import copy
import hashlib
import json


class GraphContractError(RuntimeError):
    """A stored graph_def references an atom whose contract has drifted from
    the live registry (or is unstamped / missing). Raised at load time so a
    drifted graph fails loud instead of running against the wrong contract
    (poindexter#755)."""


def stamp_graph_def(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``spec`` with each node stamped with the atom's current
    contract fingerprint (``_contract_fp``) and version (``_atom_version``)."""
    out = copy.deepcopy(spec)
    for node in out.get("nodes", []):
        atom = node.get("atom")
        meta = get_atom_meta(atom) if isinstance(atom, str) else None
        if meta is None:
            raise GraphContractError(
                f"FIX: node {node.get('id')!r} names atom {atom!r} which is not "
                f"in the registry — cannot stamp. Check the atom name."
            )
        node["_contract_fp"] = meta.contract_fingerprint()
        node["_atom_version"] = meta.version
    return out


def assert_graph_def_current(spec: dict[str, Any]) -> None:
    """Raise ``GraphContractError`` if any node is unstamped, names a missing
    atom, or carries a ``_contract_fp`` that no longer matches the registry."""
    drift: list[str] = []
    for node in spec.get("nodes", []):
        nid, atom = node.get("id"), node.get("atom")
        stored = node.get("_contract_fp")
        if not stored:
            drift.append(
                f"node {nid!r} (atom {atom!r}) is unstamped — re-seed this "
                f"graph_def to record contract fingerprints"
            )
            continue
        meta = get_atom_meta(atom) if isinstance(atom, str) else None
        if meta is None:
            drift.append(f"node {nid!r}: atom {atom!r} no longer exists in the registry")
            continue
        current = meta.contract_fingerprint()
        if current != stored:
            drift.append(
                f"node {nid!r}: atom {atom!r} contract drifted "
                f"(stamped {stored}, current {current})"
            )
    if drift:
        raise GraphContractError(
            "FIX: stored graph_def is out of date with the atom registry:\n  - "
            + "\n  - ".join(drift)
            + "\nRe-seed the affected graph_def (re-run its seeder/migration) so "
            "the stamps match the current atom contracts."
        )


def graph_signature(spec: dict[str, Any]) -> str:
    """12-hex digest over node (id, _contract_fp) pairs + edges — identifies the
    exact graph a checkpoint was produced under (poindexter#755)."""
    nodes = sorted(
        (n.get("id"), n.get("_contract_fp")) for n in spec.get("nodes", [])
    )
    edges = sorted(
        (e.get("from"), e.get("to")) for e in spec.get("edges", []) if isinstance(e, dict)
    )
    blob = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
```

Add `GraphContractError`, `stamp_graph_def`, `assert_graph_def_current`, `graph_signature` to the module `__all__` list.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/test_graph_def_contract.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_graph_def_contract.py
git commit -m "feat(pipeline): graph_def contract stamp + drift gate + signature (#755)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Stamp the architect write path (`cache_template`)

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py` — `cache_template` (~line 1096)
- Test: `src/cofounder_agent/tests/unit/services/test_graph_def_contract.py` (append a class)

**Interfaces:**

- Consumes: `stamp_graph_def` (Task 2).
- Produces: `cache_template` now persists a stamped `graph_def`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/services/test_graph_def_contract.py`:

```python
class TestCacheTemplateStamps:
    @pytest.mark.asyncio
    async def test_cache_template_persists_stamped_spec(self, registry):
        captured = {}

        class _Conn:
            async def execute(self, sql, *args):
                captured["args"] = args
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        class _Pool:
            def acquire(self): return _Conn()

        spec = _spec()
        spec["name"] = "architect_made"
        await pa.cache_template(_Pool(), spec)
        # the graph_def JSON arg must contain a stamped node
        import json as _j
        payload = next(a for a in captured["args"] if isinstance(a, str) and "_contract_fp" in a)
        assert "_contract_fp" in _j.loads(payload)["nodes"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/test_graph_def_contract.py::TestCacheTemplateStamps -q`
Expected: FAIL — no arg contains `_contract_fp` (StopIteration).

- [ ] **Step 3: Write minimal implementation**

In `cache_template`, stamp before serializing. Change the `payload = json.dumps(spec, default=str)` line to:

```python
    stamped = stamp_graph_def(spec)
    payload = json.dumps(stamped, default=str)
```

(Leave the rest of `cache_template` unchanged — it already UPSERTs `payload`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/test_graph_def_contract.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_graph_def_contract.py
git commit -m "feat(pipeline): stamp contract fingerprints in cache_template write path (#755)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire the load-time gate into `TemplateRunner.run`

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` — in `run()`, the graph_def branch (~lines 1141-1152)
- Test: `src/cofounder_agent/tests/unit/services/test_template_runner_contract_gate.py` (create)

**Interfaces:**

- Consumes: `assert_graph_def_current` (Task 2), `load_active_graph_def`.
- Produces: `run()` raises `GraphContractError` (before compiling/executing) when the loaded graph_def has drifted.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/test_template_runner_contract_gate.py
"""TemplateRunner.run refuses a drifted graph_def at load time (poindexter#755)."""
import pytest

import services.template_runner as tr
from services.pipeline_architect import GraphContractError


@pytest.mark.asyncio
async def test_run_refuses_drifted_graph_def(monkeypatch):
    runner = tr.TemplateRunner.__new__(tr.TemplateRunner)
    runner._pool = object()

    class _Cfg:
        def get_bool(self, k, d=False): return k == "pipeline_use_graph_def"
        def get(self, k, d=None): return d
    runner._site_config = _Cfg()

    drifted = {"name": "canonical_blog",
               "nodes": [{"id": "a", "atom": "atoms.draft", "config": {}}],
               "edges": [{"from": "a", "to": "END"}]}

    async def _fake_load(pool, slug): return drifted
    monkeypatch.setattr(tr, "load_active_graph_def", _fake_load, raising=False)

    def _boom(spec):
        raise GraphContractError("FIX: drift")
    monkeypatch.setattr("services.pipeline_architect.assert_graph_def_current", _boom)

    with pytest.raises(GraphContractError):
        await runner.run("canonical_blog", {"task_id": "t1"})
```

> Note: `load_active_graph_def` is imported _inside_ `run()` (`from services.pipeline_templates import TEMPLATES, load_active_graph_def`). For the monkeypatch to take effect, the gate must call `assert_graph_def_current` via the `services.pipeline_architect` module path (imported alongside `build_graph_from_spec`). If the in-function import shadows the patch, patch `services.pipeline_templates.load_active_graph_def` instead — verify which during Step 2 and pin the test to the path that works.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/test_template_runner_contract_gate.py -q`
Expected: FAIL — no `GraphContractError` raised (gate not wired).

- [ ] **Step 3: Write minimal implementation**

In `template_runner.py` `run()`, the existing graph_def branch reads:

```python
            graph_def = await load_active_graph_def(self._pool, template_slug)
            if graph_def:
                from services.pipeline_architect import build_graph_from_spec
                logger.info(...)
                graph = build_graph_from_spec(graph_def, pool=self._pool, record_sink=records, on_event=on_event)
```

Insert the gate immediately after `if graph_def:` and before `build_graph_from_spec`:

```python
            graph_def = await load_active_graph_def(self._pool, template_slug)
            if graph_def:
                from services.pipeline_architect import (
                    assert_graph_def_current,
                    build_graph_from_spec,
                )
                # poindexter#755 — refuse to run a graph whose atoms have
                # drifted from the registry it was seeded against. Fail loud +
                # notify; do NOT degrade to the legacy factory (deleted for
                # canonical_blog → KeyError would be a silent failure).
                try:
                    assert_graph_def_current(graph_def)
                except GraphContractError as exc:
                    await _emit_progress(
                        self._pool,
                        event_type="template.contract_drift",
                        payload={"template_slug": template_slug, "error": str(exc)},
                        notify_operator_message=(
                            f"⛔ {template_slug}: graph_def contract drift — refusing to run.\n{exc}"
                        ),
                        site_config=self._site_config,
                        on_event=on_event,
                    )
                    raise
                logger.info(
                    "[template_runner] graph_def path for slug=%r "
                    "(pipeline_use_graph_def on)", template_slug,
                )
                graph = build_graph_from_spec(
                    graph_def, pool=self._pool, record_sink=records, on_event=on_event,
                )
```

Add the import at the top of `template_runner.py` (module scope, alongside other `services.pipeline_architect`-free imports is not possible due to cycle — so import `GraphContractError` lazily): change the `except GraphContractError` to reference the lazily-imported name by importing it in the same `from services.pipeline_architect import (...)` block above (add `GraphContractError` to that import list). Confirm `_emit_progress` is defined in this module (it is — see ~line 297).

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/test_template_runner_contract_gate.py -q`
Expected: PASS. If it fails because the in-function import shadows the monkeypatch, switch the test to patch `services.pipeline_templates.load_active_graph_def` and re-derive (per the Step 1 note), then re-run.

- [ ] **Step 5: Run the broader runner suite (no regressions)**

Run: `poetry run pytest tests/unit/services/test_pipeline_architect_halt.py tests/unit/services/test_checkpoint_resumable.py -q`
Expected: PASS (all existing).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/tests/unit/services/test_template_runner_contract_gate.py
git commit -m "feat(pipeline): refuse drifted graph_def at load with notify (#755)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Re-stamp migration for the active graph_def rows

**Files:**

- Create: `src/cofounder_agent/services/migrations/<TIMESTAMP>_restamp_active_graph_defs.py` (generate via `python scripts/new-migration.py "restamp active graph_defs with atom contract fingerprints"`)
- Test: `src/cofounder_agent/tests/unit/services/migrations/test_restamp_active_graph_defs.py` (create)

**Interfaces:**

- Consumes: `stamp_graph_def` (Task 2), `atom_registry.discover` + `get_atom_meta`.
- Produces: an `up(pool)` that re-stamps every `active=true` `pipeline_templates` row in place; import-guarded so `migrations_smoke` (no full app boot) does not break.

- [ ] **Step 1: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "restamp active graph_defs with atom contract fingerprints"`
Expected: prints the created path `services/migrations/<TIMESTAMP>_restamp_active_graph_defs.py`.

- [ ] **Step 2: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/migrations/test_restamp_active_graph_defs.py
"""Re-stamp migration stamps active graph_def rows; smoke-safe (poindexter#755)."""
import glob
import importlib.util
import json
import os
import pytest

_MIG = sorted(glob.glob(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "services", "migrations",
    "*_restamp_active_graph_defs.py")))


def _load():
    assert _MIG, "migration file not generated yet"
    spec = importlib.util.spec_from_file_location("restamp_mig", _MIG[-1])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Conn:
    def __init__(self, rows): self._rows = rows; self.updates = []
    async def fetch(self, sql, *a): return self._rows
    async def execute(self, sql, *a): self.updates.append((sql, a))
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Pool:
    def __init__(self, rows): self._c = _Conn(rows)
    def acquire(self): return self._c


@pytest.mark.asyncio
async def test_up_stamps_active_rows(monkeypatch):
    mod = _load()
    from plugins.atom import AtomMeta
    import services.atom_registry as reg
    monkeypatch.setattr(reg, "get_atom_meta",
                        lambda n: AtomMeta(name=n, type="atom", version="1.0.0",
                                           description="d", produces=("draft",)))
    monkeypatch.setattr(reg, "discover", lambda: None)
    rows = [{"slug": "canonical_blog",
             "graph_def": json.dumps({"name": "canonical_blog",
                "nodes": [{"id": "a", "atom": "atoms.draft", "config": {}}],
                "edges": [{"from": "a", "to": "END"}]})}]
    pool = _Pool(rows)
    await mod.up(pool)
    assert pool._c.updates, "expected an UPDATE writing the stamped graph_def"
    assert any("_contract_fp" in a for _, args in pool._c.updates for a in args)


@pytest.mark.asyncio
async def test_up_is_noop_when_registry_unavailable(monkeypatch):
    mod = _load()
    import services.atom_registry as reg
    def _boom(): raise ImportError("no atoms in smoke env")
    monkeypatch.setattr(reg, "discover", _boom)
    # must not raise — smoke-safe
    await mod.up(_Pool([]))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/migrations/test_restamp_active_graph_defs.py -q`
Expected: FAIL — `up`/`down` not yet implemented as specified.

- [ ] **Step 4: Write the migration**

Replace the generated file body with:

```python
"""Re-stamp active graph_def rows with atom contract fingerprints (poindexter#755).

Establishes the drift-gate baseline for graph_defs already in production:
recomputes each node's _contract_fp/_atom_version from the live atom registry
and writes the stamped graph_def back in place.

Import-guarded: the atom registry walks modules.content.atoms.* which pull
runtime deps not present in the migrations-smoke environment. If discovery is
unavailable we skip (smoke uses a throwaway DB with nothing to protect); on the
real worker boot the registry is available and the rows get stamped.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    try:
        from services.atom_registry import discover, get_atom_meta  # noqa: F401
        from services.pipeline_architect import stamp_graph_def
        discover()
    except Exception as exc:  # noqa: BLE001 — smoke-safe skip
        logger.warning("restamp_active_graph_defs: registry unavailable, skipping (%s)", exc)
        return

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug, graph_def FROM pipeline_templates WHERE active = true"
        )
        stamped_count = 0
        for row in rows:
            raw = row["graph_def"]
            spec = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(spec, dict) or not spec.get("nodes"):
                continue
            try:
                stamped = stamp_graph_def(spec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("restamp: slug=%s could not be stamped (%s)", row["slug"], exc)
                continue
            await conn.execute(
                "UPDATE pipeline_templates SET graph_def = $1::jsonb, updated_at = NOW() "
                "WHERE slug = $2 AND active = true",
                json.dumps(stamped),
                row["slug"],
            )
            stamped_count += 1
    logger.info("restamp_active_graph_defs up: stamped %d active graph_def(s)", stamped_count)


async def down(pool) -> None:
    # Stamps are additive node keys; nothing to reverse. No-op.
    logger.info("restamp_active_graph_defs down: no-op (stamps are additive)")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/migrations/test_restamp_active_graph_defs.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the migrations lint + smoke**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_lint.py && python scripts/ci/migrations_smoke.py`
Expected: both exit 0 (the migration applies against a fresh DB without error; registry-skip path keeps it safe).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/migrations/*_restamp_active_graph_defs.py src/cofounder_agent/tests/unit/services/migrations/test_restamp_active_graph_defs.py
git commit -m "feat(pipeline): migration re-stamps active graph_defs with contract fps (#755)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Checkpoint signature — discard on mismatch

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` — declare `__graph_signature__` channel on `PipelineState` (~line 379); inject + compare/discard in `run()` (checkpointer block ~line 1225-1245)
- Test: `src/cofounder_agent/tests/unit/services/test_template_runner_contract_gate.py` (append)

**Interfaces:**

- Consumes: `graph_signature` (Task 2); the resolved checkpointer's `aget_tuple(config)`.
- Produces: a module-level `async def _discard_thread_checkpoints(pool, thread_id) -> int`; `run()` deletes a thread's checkpoint rows and starts fresh when the stored `__graph_signature__` differs from the current graph's.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/services/test_template_runner_contract_gate.py`:

```python
@pytest.mark.asyncio
async def test_discard_thread_checkpoints_guards_missing_tables():
    class _Conn:
        async def fetchval(self, sql, *a): return False  # to_regclass → tables absent
        async def execute(self, sql, *a): raise AssertionError("must not delete when absent")
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class _Pool:
        def acquire(self): return _Conn()
    deleted = await tr._discard_thread_checkpoints(_Pool(), "t1")
    assert deleted == 0


def test_graph_signature_channel_declared():
    # __graph_signature__ must be a declared PipelineState key or LangGraph
    # drops it on the graph_def path (undeclared-key lesson).
    assert "__graph_signature__" in tr.PipelineState.__annotations__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/test_template_runner_contract_gate.py -k "discard or channel" -q`
Expected: FAIL — `_discard_thread_checkpoints` missing; `__graph_signature__` not in annotations.

- [ ] **Step 3: Write minimal implementation**

(a) Declare the channel on `PipelineState` (add with the other `total=False` keys, near line 379):

```python
    __graph_signature__: str  # poindexter#755 — graph identity for checkpoint compat
```

(b) Add the module-level helper (near `has_resumable_checkpoint`, ~line 983). Mirror the guarded 3-table delete from `TasksDatabase._clear_checkpoints_for_threads`:

```python
async def _discard_thread_checkpoints(pool: Any, thread_id: str) -> int:
    """Delete LangGraph checkpoint rows for ``thread_id`` (poindexter#755).

    Guarded by ``to_regclass`` so it is a safe no-op when the Postgres
    checkpointer was never initialised. ``thread_id`` is text — never cast to
    uuid. Returns rows deleted (0 when skipped). Never raises.
    """
    if not pool or not thread_id:
        return 0
    try:
        async with pool.acquire() as conn:
            present = await conn.fetchval(
                "SELECT to_regclass('public.checkpoints') IS NOT NULL"
            )
            if not present:
                return 0
            total = 0
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                res = await conn.execute(
                    f"DELETE FROM {tbl} WHERE thread_id = $1::text", str(thread_id)
                )
                # asyncpg execute returns "DELETE <n>"
                try:
                    total += int(str(res).split()[-1])
                except (ValueError, IndexError):
                    pass
            return total
    except Exception as exc:  # noqa: BLE001
        logger.warning("[template_runner] _discard_thread_checkpoints(%s) failed: %s", thread_id, exc)
        return 0
```

(c) In `run()`, after `graph_def` passes the gate, compute the signature once:

```python
                graph = build_graph_from_spec(...)
                from services.pipeline_architect import graph_signature
                current_graph_sig = graph_signature(graph_def)
```

Initialize `current_graph_sig = None` before the `if self._site_config.get_bool("pipeline_use_graph_def", False):` block so it always exists.

(d) Inside the `async with self._resolve_checkpointer() as checkpointer:` block, after `config` is built and before `ainvoke`, add the compare/discard:

```python
                if current_graph_sig is not None and checkpointer is not None:
                    try:
                        existing = await checkpointer.aget_tuple(config)
                    except Exception:  # noqa: BLE001
                        existing = None
                    if existing is not None:
                        prev = (existing.checkpoint or {}).get("channel_values", {}).get(
                            "__graph_signature__"
                        )
                        if prev is not None and prev != current_graph_sig:
                            n = await _discard_thread_checkpoints(self._pool, thread_id)
                            logger.warning(
                                "[template_runner] graph signature changed for thread %s "
                                "(checkpoint %s != current %s) — discarded %d checkpoint row(s), "
                                "starting fresh (poindexter#755)",
                                thread_id, prev, current_graph_sig, n,
                            )
                            resume = False
                    # stamp the current signature so this run's checkpoints carry it
                    data_state["__graph_signature__"] = current_graph_sig
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/services/test_template_runner_contract_gate.py -q`
Expected: PASS (all).

- [ ] **Step 5: Full runner + architect regression sweep**

Run: `poetry run pytest tests/unit/services/ -k "template_runner or pipeline_architect or graph_def or checkpoint or atom_fingerprint" -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/tests/unit/services/test_template_runner_contract_gate.py
git commit -m "feat(pipeline): discard checkpoints on graph signature mismatch (#755)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Integration-db drift test + docs

**Files:**

- Create: `src/cofounder_agent/tests/integration_db/test_graph_def_drift.py`
- Modify: mark poindexter#755 complete in the spec status line.

**Interfaces:**

- Consumes: everything above; a real DB pool fixture from `tests/integration_db/conftest.py`.

- [ ] **Step 1: Confirm the integration_db fixture name**

Run: `poetry run pytest tests/integration_db/ --co -q | head -5` and inspect `tests/integration_db/conftest.py` for the pool fixture name (commonly `db_pool`/`pool`). Use that name in Step 2.

- [ ] **Step 2: Write the integration test**

```python
# src/cofounder_agent/tests/integration_db/test_graph_def_drift.py
"""End-to-end: a stamped graph_def loads; a drifted atom makes load refuse (#755)."""
import json
import pytest

from services.atom_registry import discover, list_atoms
from services.pipeline_architect import (
    GraphContractError, assert_graph_def_current, stamp_graph_def,
)

pytestmark = pytest.mark.asyncio


async def test_stamped_passes_then_drift_refuses(monkeypatch):
    discover()
    atoms = list_atoms()
    assert atoms, "registry empty"
    a0 = atoms[0]
    spec = {"name": "drift_probe",
            "nodes": [{"id": "n0", "atom": a0.name, "config": {}}],
            "edges": [{"from": "n0", "to": "END"}]}
    stamped = stamp_graph_def(spec)
    assert_graph_def_current(stamped)  # current → passes

    # Simulate drift: registry returns a contract-changed meta for a0.name
    import services.pipeline_architect as pa
    from dataclasses import replace
    drifted_meta = replace(a0, requires=a0.requires + ("__synthetic_drift__",))
    monkeypatch.setattr(pa, "get_atom_meta",
                        lambda n: drifted_meta if n == a0.name else None)
    with pytest.raises(GraphContractError):
        assert_graph_def_current(stamped)
```

- [ ] **Step 3: Run it (gated on a DB; may be host-only)**

Run: `poetry run pytest tests/integration_db/test_graph_def_drift.py -q`
Expected: PASS where a DB is available; the test itself needs no DB (pure registry) so it should pass in-container too.

- [ ] **Step 4: Update spec status + commit**

Edit `docs/superpowers/specs/2026-06-18-atom-contract-fingerprint-handshake-design.md` status line to `Status: Implemented`.

```bash
git add src/cofounder_agent/tests/integration_db/test_graph_def_drift.py docs/superpowers/specs/2026-06-18-atom-contract-fingerprint-handshake-design.md
git commit -m "test(pipeline): integration drift check + mark #755 spec implemented

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (before PR)

- [ ] `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_atom_fingerprint.py tests/unit/services/test_graph_def_contract.py tests/unit/services/test_template_runner_contract_gate.py tests/unit/services/migrations/test_restamp_active_graph_defs.py -q` → all pass
- [ ] `poetry run pytest tests/unit/services/ -q` → no regressions in the runner/architect suites
- [ ] `python scripts/ci/migrations_smoke.py` → exit 0
- [ ] `poetry run ruff check plugins/atom.py services/pipeline_architect.py services/template_runner.py` → clean
- [ ] Open PR against `glad-labs-stack` with body `Closes poindexter#755` + summary; cross-reference the spec/plan docs.

## Self-review notes (coverage map)

- Spec §1 fingerprint → Task 1. Spec §2 stamping (architect + migration) → Tasks 3, 5. Spec §3 load gate → Task 4. Spec §4 checkpoint → Task 6. Spec §5 testing → tasks' tests + Task 7 integration.
- Deviation from spec: dropped the separate `upsert_graph_def(pool, spec)` wrapper as YAGNI — `stamp_graph_def` is the shared primitive; the migration and `cache_template` each call it then do their own UPSERT (matches the existing per-migration `_UPSERT_SQL` convention). Future seeders call `stamp_graph_def` before their INSERT.
- Naming consistency: `contract_fingerprint`, `stamp_graph_def`, `assert_graph_def_current`, `graph_signature`, `GraphContractError`, `_discard_thread_checkpoints`, channel `__graph_signature__` — used identically across all tasks.
