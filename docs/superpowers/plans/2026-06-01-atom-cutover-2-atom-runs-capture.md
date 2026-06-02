# Atom Cutover — Plan 2: `atom_runs` per-atom run + outcome capture

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist one row per atom-node execution of a composed (`build_graph_from_spec`) pipeline run into a new `atom_runs` table — the composition + outcome substrate for #361 (outcome→router feedback) and a future composition-learning architect — plus a writer to backfill the outcome (approval decision / quality_score / edit_distance) after the human-approval gate resolves.

**Architecture:** A new DB migration creates `atom_runs`. A new `services/atom_runs.py` holds two best-effort writers: `persist_atom_runs(pool, *, run_id, task_id, template_slug, records, site_config)` (one row per `TemplateRunRecord` in a run's `record_sink`, gated by `app_settings.atom_runs_capture_enabled`) and `record_atom_run_outcome(pool, *, task_id, …)` (backfills the outcome columns). The atom-node wrapper `pipeline_architect._wrap_atom` is extended to stamp `node_id` + input/output state-key digests onto each record so the _composition shape_ is captured. **This plan ships the substrate only — it adds no production call site.** `build_graph_from_spec` is still dormant (no production caller until Plan 4); Plan 4 wires `persist_atom_runs` into the runner where the graph actually runs, and `record_atom_run_outcome` into the approval path.

**Relationship to `capability_outcomes` (deliberately complementary, NOT a duplicate):** `services/capability_outcomes.py` already writes one row per node scoring `(atom, capability_tier, model)` for the _router_. `atom_runs` adds what that table lacks — a per-invocation `run_id` (groups all atoms of one run), input/output state-key **digests** (the composition shape), `cost`/`retries`, and the full **outcome join** (`post_id` / approval `decision` / `edit_distance`) filled in after approval. Both are best-effort (observational, never load-bearing). Do not fold one into the other.

**Tech Stack:** Python 3.13, asyncpg (pool stub in unit tests — no live DB), pytest (`asyncio_mode = "auto"`, so async test fns need no decorator; mark `@pytest.mark.unit`), the existing `pipeline_architect._wrap_atom` + `template_runner.TemplateRunRecord` + `atom_registry.list_atoms`. The migration DDL is validated by the existing `migrations-smoke` CI job against a fresh Postgres; unit tests cover the writer logic.

**Spec:** `docs/superpowers/specs/2026-06-01-canonical-blog-atom-cutover-design.md` (§ "Data capture (training / learning substrate)" + D5/D6).

**Conventions:** run tests from `src/cofounder_agent` with the main venv python (worktrees have no poetry env):
`<main-venv-python> -m pytest <path> -p no:cacheprovider`
(cwd = the worktree's `src/cofounder_agent`). Windows stdout buffers — redirect pytest output to a file and read it back; never treat empty output as success. This repo forbids merge commits — feature branch, linear commits, normal push. Commit after each green task. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: migration — create `atom_runs` table + seed the capture flag

Creates the table and seeds `atom_runs_capture_enabled='true'` so capture is on (and visible/tunable in the config plane) the moment Plan 4 wires the call. `post_id` is `UUID` because `posts.id` is a uuid. Outcome columns are nullable (backfilled later by `record_atom_run_outcome`). Idempotent via `IF NOT EXISTS` / `ON CONFLICT DO NOTHING`.

**Files:**

- Create: `src/cofounder_agent/services/migrations/<generated-timestamp>_create_atom_runs_table.py` (generate with the helper below — DO NOT hand-name it; the timestamp prevents prefix collisions per #378)

- [ ] **Step 1: Generate the migration file**

Run (cwd = repo root of the worktree):

```bash
python scripts/new-migration.py "create atom_runs table"
```

Note the printed path (e.g. `…/services/migrations/20260602_HHMMSS_create_atom_runs_table.py`). This is the file you edit in Step 2.

- [ ] **Step 2: Replace the generated file's body with the full migration**

Overwrite the generated file with exactly this (keep the generated timestamp in the docstring's first line if you like — content below is what matters):

```python
"""Migration: create atom_runs table + seed atom_runs_capture_enabled

ISSUE: Glad-Labs/poindexter#355 (atom-cutover Plan 2)

Per-atom run + outcome capture for composed (build_graph_from_spec)
pipelines — the (composition -> outcome) substrate for #361
(outcome->router feedback) and a future composition-learning architect.

Complementary to capability_outcomes (which scores (atom, tier, model)
for the router): atom_runs adds a per-invocation run_id, input/output
state-key digests (the composition shape), cost/retries, and the full
outcome join (post_id / approval decision / edit_distance) backfilled
after the human-approval gate resolves.

Additive + dormant: no production code writes to this table yet. Plan 4
wires persist_atom_runs into the runner and record_atom_run_outcome into
the approval path. The capture is gated by app_settings.atom_runs_capture_enabled
(seeded 'true'). post_id is UUID because posts.id is a uuid.

Idempotent via IF NOT EXISTS / ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration. Idempotent."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS atom_runs (
                id              BIGSERIAL PRIMARY KEY,
                run_id          TEXT NOT NULL,
                task_id         TEXT,
                template_slug   TEXT,
                seq             INTEGER NOT NULL DEFAULT 0,
                atom            TEXT NOT NULL,
                node_id         TEXT,
                tier            TEXT,
                model           TEXT,
                latency_ms      INTEGER NOT NULL DEFAULT 0,
                cost            NUMERIC(12, 6),
                retries         INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL,
                input_digest    TEXT,
                output_digest   TEXT,
                input_keys      TEXT[],
                output_keys     TEXT[],
                metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
                -- Outcome join (backfilled by record_atom_run_outcome
                -- after the human-approval gate resolves):
                post_id         UUID,
                decision        TEXT,
                quality_score   NUMERIC(5, 2),
                edit_distance   INTEGER,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_atom_runs_run_id ON atom_runs (run_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_atom_runs_task_id ON atom_runs (task_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_atom_runs_atom ON atom_runs (atom)"
        )
        # Seed the capture toggle (DB-config enable flag, D6). 'true' so
        # capture is on the moment Plan 4 wires the call; operators can
        # flip it without a deploy. ON CONFLICT keeps an operator-tuned value.
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO NOTHING",
            "atom_runs_capture_enabled", "true",
        )
        logger.info("Migration create_atom_runs_table: applied")


async def down(pool) -> None:
    """Revert: drop the table + remove the seeded flag (only if untouched)."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS atom_runs")
        await conn.execute(
            "DELETE FROM app_settings "
            "WHERE key = 'atom_runs_capture_enabled' AND value = 'true'"
        )
        logger.info("Migration create_atom_runs_table down: reverted")
```

- [ ] **Step 3: Lint the migration (static — no DB needed)**

Run (cwd = repo root of the worktree):

```bash
python scripts/ci/migrations_lint.py
```

Expected: exits 0 (no collision, runner interface present). If it complains the file is unparseable, re-check you kept `async def up(pool)` / `async def down(pool)`.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/migrations/<generated-timestamp>_create_atom_runs_table.py
git commit -m "feat(pipeline): add atom_runs table + capture flag (#355)"
```

---

### Task 2: `persist_atom_runs` writer + status/flag helpers

The per-run writer: one `atom_runs` row per `TemplateRunRecord`. Best-effort (DB errors logged + swallowed — capture must never fail content generation), flag-gated through `site_config`. Reads `node_id` / digests / model / cost / retries from each record (`node_id` is a real field after Task 3; the rest ride in `metrics`), and stamps `tier` from the atom registry (mirrors `capability_outcomes.record_run`).

**Files:**

- Create: `src/cofounder_agent/services/atom_runs.py`
- Test: `src/cofounder_agent/tests/unit/services/test_atom_runs.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for services.atom_runs writers (atom-cutover Plan 2, #355).

No DB — uses an asyncpg-pool stub that records execute() calls (and can
return a preset status string) for assertion, mirroring
tests/unit/services/test_capability_outcomes.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from services import atom_runs
from services.atom_runs import persist_atom_runs


# --- asyncpg pool stub ------------------------------------------------------


class _Conn:
    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]], result: Any):
        self._sink = sink
        self._result = result

    async def execute(self, sql: str, *args: Any) -> Any:
        self._sink.append((sql, args))
        return self._result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Acquire:
    def __init__(self, conn: _Conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _Pool:
    def __init__(self, result: Any = None) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._conn = _Conn(self.executed, result)

    def acquire(self):
        return _Acquire(self._conn)


# --- record + site_config stubs --------------------------------------------


@dataclass
class _Rec:
    name: str
    ok: bool = True
    halted: bool = False
    skipped: bool = False
    elapsed_ms: int = 100
    node_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class _Cfg:
    def __init__(self, vals: dict[str, Any]):
        self._vals = vals

    def get(self, key: str, default: Any = None) -> Any:
        return self._vals.get(key, default)


# --- _status_of -------------------------------------------------------------


@pytest.mark.unit
class TestStatusOf:
    def test_ok(self):
        assert atom_runs._status_of(_Rec(name="a", ok=True)) == "ok"

    def test_skipped_wins(self):
        assert atom_runs._status_of(_Rec(name="a", ok=True, skipped=True)) == "skipped"

    def test_halted(self):
        assert atom_runs._status_of(_Rec(name="a", ok=False, halted=True)) == "halted"

    def test_error(self):
        assert atom_runs._status_of(_Rec(name="a", ok=False)) == "error"


# --- persist_atom_runs ------------------------------------------------------


@pytest.mark.unit
class TestPersistAtomRuns:
    async def test_writes_one_row_per_record(self):
        pool = _Pool()
        records = [_Rec(name="atoms.x"), _Rec(name="atoms.y")]
        n = await persist_atom_runs(
            pool, run_id="r1", task_id="t1",
            template_slug="canonical_blog", records=records,
        )
        assert n == 2
        # Two INSERTs into atom_runs.
        inserts = [c for c in pool.executed if "INSERT INTO atom_runs" in c[0]]
        assert len(inserts) == 2

    async def test_maps_record_fields_onto_insert_args(self):
        pool = _Pool()
        rec = _Rec(
            name="atoms.writer", ok=True, elapsed_ms=2500, node_id="n7",
            metrics={
                "model_used": "test-model:9b", "cost": 0.0,
                "retries": 1, "input_digest": "abc123",
                "output_digest": "def456",
                "input_keys": ["task_id", "topic"],
                "output_keys": ["content"],
            },
        )
        await persist_atom_runs(
            pool, run_id="run-9", task_id="task-9",
            template_slug="canonical_blog", records=[rec],
        )
        sql, args = pool.executed[0]
        # Positional INSERT order: run_id, task_id, template_slug, seq, atom,
        # node_id, tier, model, latency_ms, cost, retries, status,
        # input_digest, output_digest, input_keys, output_keys, metrics.
        assert args[0] == "run-9"
        assert args[1] == "task-9"
        assert args[2] == "canonical_blog"
        assert args[3] == 0                 # seq
        assert args[4] == "atoms.writer"    # atom
        assert args[5] == "n7"              # node_id
        assert args[7] == "test-model:9b"   # model
        assert args[8] == 2500              # latency_ms
        assert args[10] == 1                # retries
        assert args[11] == "ok"             # status
        assert args[12] == "abc123"         # input_digest
        assert args[13] == "def456"         # output_digest
        assert args[14] == ["task_id", "topic"]
        assert args[15] == ["content"]

    async def test_disabled_flag_skips_all_writes(self):
        pool = _Pool()
        cfg = _Cfg({"atom_runs_capture_enabled": "false"})
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t",
            template_slug="s", records=[_Rec(name="atoms.x")],
            site_config=cfg,
        )
        assert n == 0
        assert pool.executed == []

    async def test_enabled_flag_true_writes(self):
        pool = _Pool()
        cfg = _Cfg({"atom_runs_capture_enabled": "true"})
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t",
            template_slug="s", records=[_Rec(name="atoms.x")],
            site_config=cfg,
        )
        assert n == 1

    async def test_empty_records_noop(self):
        pool = _Pool()
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t", template_slug="s", records=[],
        )
        assert n == 0
        assert pool.executed == []

    async def test_missing_metrics_persist_nulls_not_crash(self):
        pool = _Pool()
        # A stage-style record: no node_id, no digests in metrics.
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t",
            template_slug="s", records=[_Rec(name="verify_task")],
        )
        assert n == 1
        _, args = pool.executed[0]
        assert args[5] is None    # node_id
        assert args[12] is None   # input_digest
        assert args[15] is None   # output_keys
```

- [ ] **Step 2: Run, verify they fail**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_runs.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: collection ERROR / ImportError — `services.atom_runs` does not exist yet.

- [ ] **Step 3: Implement `services/atom_runs.py`**

```python
"""``atom_runs`` — per-atom run + outcome capture for composed pipelines.

Glad-Labs/poindexter#355 atom-cutover Plan 2. When a pipeline runs as a
composed graph_def (``build_graph_from_spec``), every node appends a
``TemplateRunRecord`` to the run's ``record_sink``. :func:`persist_atom_runs`
writes one ``atom_runs`` row per record — the (composition -> outcome)
substrate for #361 and a future composition-learning architect.

Complementary to ``capability_outcomes`` (which scores
``(atom, tier, model)`` for the router): ``atom_runs`` adds a per-invocation
``run_id`` (groups all atoms of one run), input/output state-key *digests*
(the composition shape), ``cost``/``retries``, and the full outcome join
(``post_id`` / approval ``decision`` / ``edit_distance``) backfilled by
:func:`record_atom_run_outcome` after the human-approval gate resolves.

Both writers are best-effort: capture is observational, never load-bearing,
so a DB error here is logged + swallowed — it must never fail content
generation. The per-run capture is gated by
``app_settings.atom_runs_capture_enabled`` (seeded ``true``) read through the
run-bound ``SiteConfig``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _truthy(value: Any) -> bool:
    """Coerce an app_settings string/bool to a bool (``"true"`` -> True)."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _status_of(record: Any) -> str:
    """Map a TemplateRunRecord's flags to one status token.

    Precedence: skipped -> halted -> error -> ok. ``halted`` covers a node
    that requested a graph halt (e.g. QA reject); ``error`` is a not-ok
    record that didn't explicitly halt; ``ok`` is the success path.
    """
    if getattr(record, "skipped", False):
        return "skipped"
    if getattr(record, "halted", False):
        return "halted"
    if not getattr(record, "ok", False):
        return "error"
    return "ok"


def _capture_enabled(site_config: Any) -> bool:
    if site_config is None:
        return True
    return _truthy(site_config.get("atom_runs_capture_enabled", "true"))


def _catalog_by_name() -> dict[str, Any]:
    """Best-effort atom catalog for capability_tier stamping (memory hit,
    mirrors capability_outcomes.record_run)."""
    try:
        from services import atom_registry
        return {m.name: m for m in atom_registry.list_atoms()}
    except Exception:  # noqa: BLE001
        return {}


def digest_keys(keys: Any) -> str:
    """Stable short digest of a collection of state keys — the composition
    'shape' at an atom boundary. Sorted (order-independent); sha256
    truncated to 16 hex chars (enough to dedupe shapes, cheap to store)."""
    norm = ",".join(sorted(str(k) for k in (keys or [])))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


async def persist_atom_runs(
    pool: Any,
    *,
    run_id: str,
    task_id: str | None,
    template_slug: str,
    records: list[Any],
    site_config: Any = None,
) -> int:
    """Write one ``atom_runs`` row per record in ``records``.

    Returns rows written. Gated by ``atom_runs_capture_enabled`` (via
    ``site_config``; default-on when no site_config is passed). Best-effort —
    exceptions are logged + swallowed so capture never breaks the pipeline.
    """
    if pool is None or not records:
        return 0
    if not _capture_enabled(site_config):
        return 0

    catalog = _catalog_by_name()
    written = 0
    try:
        async with pool.acquire() as conn:
            for seq, r in enumerate(records):
                atom = getattr(r, "name", "") or ""
                meta = catalog.get(atom)
                if meta is None:
                    for cand in (f"atoms.{atom}", f"stage.{atom}"):
                        if cand in catalog:
                            meta = catalog[cand]
                            break
                tier = getattr(meta, "capability_tier", None) if meta else None

                metrics = getattr(r, "metrics", {}) or {}
                node_id = getattr(r, "node_id", None) or metrics.get("node_id")
                model = metrics.get("model_used") or metrics.get("model")
                cost = metrics.get("cost")
                retries = int(metrics.get("retries", 0) or 0)
                input_keys = metrics.get("input_keys")
                output_keys = metrics.get("output_keys")
                input_digest = metrics.get("input_digest")
                output_digest = metrics.get("output_digest")

                await conn.execute(
                    """
                    INSERT INTO atom_runs
                      (run_id, task_id, template_slug, seq, atom, node_id,
                       tier, model, latency_ms, cost, retries, status,
                       input_digest, output_digest, input_keys, output_keys,
                       metrics)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                            $12, $13, $14, $15, $16, $17::jsonb)
                    """,
                    run_id, task_id, template_slug, seq, atom, node_id,
                    tier, model, int(getattr(r, "elapsed_ms", 0) or 0),
                    cost, retries, _status_of(r),
                    input_digest, output_digest,
                    list(input_keys) if input_keys is not None else None,
                    list(output_keys) if output_keys is not None else None,
                    json.dumps(metrics, default=str),
                )
                written += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atom_runs] persist_atom_runs failed: %s", exc)
    return written


__all__ = ["digest_keys", "persist_atom_runs"]
```

- [ ] **Step 4: Run, verify they pass**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_runs.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all tests in `TestStatusOf` + `TestPersistAtomRuns` pass.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/atom_runs.py src/cofounder_agent/tests/unit/services/test_atom_runs.py
git commit -m "feat(pipeline): atom_runs persist writer + status/flag helpers (#355)"
```

---

### Task 3: capture extension — `node_id` + io-digests on atom records

So `atom_runs` can store the node identity and composition shape, the atom-node wrapper must stamp them. Add an optional `node_id` field to `TemplateRunRecord` (additive — every existing constructor still valid, default `None`), then extend `pipeline_architect._wrap_atom` to receive the node id and stamp `node_id` + input/output key lists + digests onto the record it appends. **Scope:** atoms only (`_wrap_atom`); the `stage.*` shorthand path (`make_stage_node`, shared with the live legacy pipeline) is intentionally left untouched to keep blast radius off the hot path — stage nodes persist with NULL `node_id`/digests, which `persist_atom_runs` already tolerates (Task 2's `test_missing_metrics_persist_nulls_not_crash`).

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` — `TemplateRunRecord` dataclass (~line 329) + `TemplateRunSummary.to_dict` (~line 351)
- Modify: `src/cofounder_agent/services/pipeline_architect.py` — `_wrap_atom` signature + body (~line 692) and its call site in `build_graph_from_spec` (~line 619)
- Test: `src/cofounder_agent/tests/unit/services/test_atom_capture.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the atom-node capture extension (atom-cutover Plan 2, #355):
_wrap_atom stamps node_id + input/output state-key digests onto the
TemplateRunRecord it appends to record_sink."""

from __future__ import annotations

import pytest

from services.atom_runs import digest_keys
from services.pipeline_architect import _wrap_atom


@pytest.mark.unit
class TestAtomCapture:
    async def test_success_record_carries_node_id_and_digests(self):
        sink: list = []

        async def run_fn(state):
            return {"content": "hello world", "new_key": 1}

        node = _wrap_atom(run_fn, "atoms.fake", "n1", sink)
        out = await node({"task_id": "t", "topic": "x"}, None)

        assert out == {"content": "hello world", "new_key": 1}
        assert len(sink) == 1
        rec = sink[0]
        assert rec.ok is True
        assert rec.node_id == "n1"
        # Input keys captured from the merged atom input (services none here).
        assert "task_id" in rec.metrics["input_keys"]
        assert "topic" in rec.metrics["input_keys"]
        # Output keys captured from the atom's returned dict.
        assert set(rec.metrics["output_keys"]) == {"content", "new_key"}
        # Digests are the sha256-of-sorted-keys helper.
        assert rec.metrics["input_digest"] == digest_keys(rec.metrics["input_keys"])
        assert rec.metrics["output_digest"] == digest_keys(rec.metrics["output_keys"])

    async def test_failure_record_carries_node_id(self):
        sink: list = []

        async def boom(state):
            raise ValueError("nope")

        node = _wrap_atom(boom, "atoms.boom", "n2", sink)
        out = await node({"task_id": "t"}, None)

        # Failure path halts the graph.
        assert out.get("_halt") is True
        assert len(sink) == 1
        rec = sink[0]
        assert rec.ok is False
        assert rec.halted is True
        assert rec.node_id == "n2"
        assert "task_id" in rec.metrics["input_keys"]
```

- [ ] **Step 2: Run, verify it fails**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_capture.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: FAIL — `_wrap_atom` takes 3 positional args, not 4 (`TypeError`), and `TemplateRunRecord` has no `node_id`.

- [ ] **Step 3a: Add `node_id` to `TemplateRunRecord`**

In `services/template_runner.py`, the dataclass (~line 329) becomes:

```python
@dataclass
class TemplateRunRecord:
    """One node's execution result, mirroring StageRunRecord shape."""

    name: str
    ok: bool
    detail: str = ""
    halted: bool = False
    skipped: bool = False
    elapsed_ms: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    # Graph node id (architect/graph_def composition). None for legacy
    # stage nodes that don't carry a distinct id (atom-cutover #355).
    node_id: str | None = None
```

And in `TemplateRunSummary.to_dict` (~line 351), add `node_id` to each emitted dict (insert after the `"metrics": r.metrics,` line):

```python
                    "metrics": r.metrics,
                    "node_id": r.node_id,
```

- [ ] **Step 3b: Extend `_wrap_atom` to stamp node_id + digests**

In `services/pipeline_architect.py`, change the call site in `build_graph_from_spec` (~line 619) from:

```python
            g.add_node(nid, _wrap_atom(run_fn, atom_name, record_sink))
```

to:

```python
            g.add_node(nid, _wrap_atom(run_fn, atom_name, nid, record_sink))
```

Then replace the whole `_wrap_atom` function (~lines 692-746) with:

```python
def _wrap_atom(
    run_fn: Callable[..., Any],
    atom_name: str,
    node_id: str,
    record_sink: list | None,
) -> Callable[..., Any]:
    """Wrap a pure atom into the LangGraph node signature with
    record_sink integration so observability matches stage nodes.

    Mirrors the state-vs-services merge from ``make_stage_node``
    (Glad-Labs/poindexter#382): live service handles are pulled from
    ``RunnableConfig.configurable["__services__"]`` and merged into the
    atom's input dict so atoms that read ``state.get("database_service")``
    keep working unchanged. ``config`` MUST be annotated as bare
    ``RunnableConfig`` — see the matching note in ``make_stage_node``.

    Stamps ``node_id`` + input/output state-key lists + digests onto the
    record so ``atom_runs`` captures the composition shape (#355 Plan 2).
    """

    from langchain_core.runnables import RunnableConfig
    from services.atom_runs import digest_keys
    from services.template_runner import TemplateRunRecord, _services_from_config

    async def node(
        state: PipelineState,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        import time as _time
        t0 = _time.time()
        atom_input: dict[str, Any] = dict(state)
        for svc_key, svc_value in _services_from_config(config).items():
            atom_input.setdefault(svc_key, svc_value)
        input_keys = sorted(str(k) for k in atom_input.keys())
        try:
            result = await run_fn(atom_input)
            elapsed_ms = int((_time.time() - t0) * 1000)
            out = result if isinstance(result, dict) else {}
            output_keys = sorted(str(k) for k in out.keys())
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=atom_name, ok=True,
                        detail=f"{len(str(out.get('content','') or ''))} chars",
                        elapsed_ms=elapsed_ms,
                        node_id=node_id,
                        metrics={
                            "input_keys": input_keys,
                            "output_keys": output_keys,
                            "input_digest": digest_keys(input_keys),
                            "output_digest": digest_keys(output_keys),
                        },
                    )
                )
            return out
        except Exception as exc:
            elapsed_ms = int((_time.time() - t0) * 1000)
            logger.exception("[architect] atom %s raised: %s", atom_name, exc)
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=atom_name, ok=False,
                        detail=f"raised {type(exc).__name__}: {exc}",
                        halted=True, elapsed_ms=elapsed_ms,
                        node_id=node_id,
                        metrics={
                            "input_keys": input_keys,
                            "output_keys": [],
                            "input_digest": digest_keys(input_keys),
                            "output_digest": digest_keys([]),
                        },
                    )
                )
            return {"_halt": True, "_halt_reason": f"{atom_name}: {exc}"}

    node.__name__ = f"atom_node_{atom_name.replace('.', '_')}"
    return node
```

- [ ] **Step 4: Run, verify it passes**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_capture.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: both tests pass.

- [ ] **Step 5: Run the existing pipeline_architect + template_runner tests (regression)**

Run: `<main-venv-python> -m pytest tests/unit/services/test_pipeline_architect_validate.py tests/unit/services/test_template_runner_state_partition.py tests/unit/services/test_capability_outcomes.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass (the `node_id` field default keeps every existing `TemplateRunRecord(...)` construction valid; `to_dict` gains a key but no test asserts exact dict equality on it — if one does, update that test to include `node_id`).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_atom_capture.py
git commit -m "feat(pipeline): capture node_id + io-digests on atom records (#355)"
```

---

### Task 4: `record_atom_run_outcome` — backfill the outcome join

The writer that links a run's `atom_runs` rows to the eventual outcome (post_id / approval decision / quality_score / edit_distance) once the human-approval gate resolves. Keyed on `task_id` (the whole run shares one outcome); optional `run_id` scopes to a specific invocation when a task was re-run. `COALESCE` keeps any previously-written non-null value so partial updates compose. Best-effort. **No production call site in this plan** — Plan 4/5 calls it from the approval path; Task 5 proves the round-trip.

**Files:**

- Modify: `src/cofounder_agent/services/atom_runs.py` — add `record_atom_run_outcome` + extend `__all__`
- Test: `src/cofounder_agent/tests/unit/services/test_atom_runs.py` (append a `TestRecordOutcome` class)

- [ ] **Step 1: Write the failing tests** (append to `test_atom_runs.py`)

```python
from services.atom_runs import record_atom_run_outcome


@pytest.mark.unit
class TestRecordOutcome:
    async def test_updates_by_task_id_and_returns_rowcount(self):
        pool = _Pool(result="UPDATE 3")
        n = await record_atom_run_outcome(
            pool, task_id="t1", post_id="00000000-0000-0000-0000-0000000000aa",
            decision="approved", quality_score=88.5, edit_distance=12,
        )
        assert n == 3
        sql, args = pool.executed[0]
        assert "UPDATE atom_runs" in sql
        assert args[0] == "t1"
        assert args[1] is None  # run_id not scoped
        assert args[2] == "00000000-0000-0000-0000-0000000000aa"
        assert args[3] == "approved"
        assert args[4] == 88.5
        assert args[5] == 12

    async def test_run_id_scopes_the_update(self):
        pool = _Pool(result="UPDATE 1")
        await record_atom_run_outcome(
            pool, task_id="t1", run_id="run-1", decision="rejected",
        )
        _, args = pool.executed[0]
        assert args[0] == "t1"
        assert args[1] == "run-1"

    async def test_empty_task_id_noop(self):
        pool = _Pool()
        n = await record_atom_run_outcome(pool, task_id="")
        assert n == 0
        assert pool.executed == []

    async def test_unparseable_rowcount_returns_zero(self):
        pool = _Pool(result=None)  # stub returns None like a no-op execute
        n = await record_atom_run_outcome(pool, task_id="t1", decision="revised")
        assert n == 0
```

- [ ] **Step 2: Run, verify they fail**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_runs.py::TestRecordOutcome -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `record_atom_run_outcome` not defined yet.

- [ ] **Step 3: Implement** — add to `services/atom_runs.py` (before `__all__`):

```python
async def record_atom_run_outcome(
    pool: Any,
    *,
    task_id: str,
    run_id: str | None = None,
    post_id: str | None = None,
    decision: str | None = None,
    quality_score: float | None = None,
    edit_distance: int | None = None,
) -> int:
    """Backfill the outcome columns on a run's ``atom_runs`` rows after the
    approval gate resolves. Returns rows updated. Best-effort.

    Keyed on ``task_id`` (the whole run shares one outcome); pass ``run_id``
    to scope to a specific invocation when a task was re-run. ``COALESCE``
    keeps any previously-written non-null value so a partial update (e.g.
    quality_score now, decision later) composes.
    """
    if pool is None or not task_id:
        return 0
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE atom_runs SET
                    post_id       = COALESCE($3::uuid, post_id),
                    decision      = COALESCE($4, decision),
                    quality_score = COALESCE($5, quality_score),
                    edit_distance = COALESCE($6, edit_distance)
                WHERE task_id = $1
                  AND ($2::text IS NULL OR run_id = $2)
                """,
                task_id, run_id, post_id, decision, quality_score, edit_distance,
            )
        # asyncpg returns a status string like "UPDATE 3" — parse the count.
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atom_runs] record_atom_run_outcome failed: %s", exc)
        return 0
```

And update the bottom of the file:

```python
__all__ = ["digest_keys", "persist_atom_runs", "record_atom_run_outcome"]
```

- [ ] **Step 4: Run, verify they pass**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_runs.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all `TestStatusOf` + `TestPersistAtomRuns` + `TestRecordOutcome` pass.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/atom_runs.py src/cofounder_agent/tests/unit/services/test_atom_runs.py
git commit -m "feat(pipeline): atom_runs outcome-join writer (#355)"
```

---

### Task 5: composition test — `_wrap_atom` record → `persist_atom_runs` round-trip

Proves Task 2 and Task 3 agree on the metrics key names (catches drift between what `_wrap_atom` stamps and what `persist_atom_runs` reads) by driving the real wrapper, then the real writer (pool stub), and asserting the INSERT args carry the threaded `node_id` + digests + derived status.

**Files:**

- Test: `src/cofounder_agent/tests/unit/services/test_atom_runs_roundtrip.py` (create)

- [ ] **Step 1: Write the test**

```python
"""Composition test (atom-cutover Plan 2, #355): a record produced by the
real _wrap_atom flows through the real persist_atom_runs and lands the
node_id + digests + status on the INSERT — pins that the wrapper's metrics
keys match the writer's reads."""

from __future__ import annotations

from typing import Any

import pytest

from services.atom_runs import persist_atom_runs
from services.pipeline_architect import _wrap_atom


class _Conn:
    def __init__(self, sink):
        self._sink = sink

    async def execute(self, sql: str, *args: Any):
        self._sink.append((sql, args))

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
class TestWrapPersistRoundtrip:
    async def test_record_sink_persists_with_node_id_and_digests(self):
        sink: list = []

        async def run_fn(state):
            return {"content": "abc", "draft": "x"}

        node = _wrap_atom(run_fn, "atoms.demo", "node-A", sink)
        await node({"task_id": "task-1", "topic": "t"}, None)

        pool = _Pool()
        n = await persist_atom_runs(
            pool, run_id="run-1", task_id="task-1",
            template_slug="canonical_blog", records=sink,
        )
        assert n == 1
        _, args = pool.executed[0]
        # run_id, task_id, template_slug, seq, atom, node_id, tier, model,
        # latency_ms, cost, retries, status, input_digest, output_digest, ...
        assert args[0] == "run-1"
        assert args[4] == "atoms.demo"
        assert args[5] == "node-A"        # node_id threaded end-to-end
        assert args[11] == "ok"           # status derived
        assert args[12] is not None       # input_digest
        assert args[13] is not None       # output_digest
        assert "content" in args[15]      # output_keys
```

- [ ] **Step 2: Run, verify it passes**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_runs_roundtrip.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: PASS. If `args[5]` is None, the `node_id` thread in Task 3 Step 3b's call site (`_wrap_atom(run_fn, atom_name, nid, record_sink)`) wasn't applied.

- [ ] **Step 3: Final full-module test sweep + commit**

Run: `<main-venv-python> -m pytest tests/unit/services/test_atom_runs.py tests/unit/services/test_atom_capture.py tests/unit/services/test_atom_runs_roundtrip.py tests/unit/services/test_pipeline_architect_validate.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass.

```bash
git add src/cofounder_agent/tests/unit/services/test_atom_runs_roundtrip.py
git commit -m "test(pipeline): _wrap_atom record -> persist_atom_runs round-trip (#355)"
```

---

## Self-review notes

- **Spec coverage:** implements the spec's "Data capture (training / learning substrate)" section (D5) — a new `atom_runs` table (run_id, task_id, atom, node_id, tier/model, latency_ms, cost, retries, status, io-key digests) + outcome join (post_id, decision, quality_score, edit_distance), with a DB-config enable flag (D6: `atom_runs_capture_enabled`). The persist + outcome writers are best-effort, mirroring the existing `capability_outcomes` pattern. **Out of plan (by design):** the production call sites for `persist_atom_runs` (the runner's graph_def branch) and `record_atom_run_outcome` (the approval path) land in Plan 4/5 when the cutover seam exists and `build_graph_from_spec` actually runs — this plan ships the additive, dormant substrate, independently shippable and green.
- **Complementary, not duplicative:** `atom_runs` deliberately differs from `capability_outcomes` (run_id grouping + io-digests + cost/retries + the post_id/decision/edit_distance outcome join). The module docstring + plan header state the boundary so the implementer doesn't fold them together.
- **Blast radius:** one new table (additive migration, idempotent), one new module (`services/atom_runs.py`), one additive dataclass field (`TemplateRunRecord.node_id`, default `None` — every existing constructor stays valid), and one extended function (`_wrap_atom`, atom path only; `make_stage_node` and the live legacy pipeline are untouched). No production code path changes behavior — nothing calls the new writers yet.
- **Type consistency:** `persist_atom_runs(pool, *, run_id: str, task_id: str | None, template_slug: str, records: list, site_config=None) -> int`; `record_atom_run_outcome(pool, *, task_id: str, run_id=None, post_id: str | None, decision: str | None, quality_score: float | None, edit_distance: int | None) -> int`; `digest_keys(keys) -> str`; `_status_of(record) -> str`; `TemplateRunRecord.node_id: str | None = None`; `_wrap_atom(run_fn, atom_name, node_id: str, record_sink)`. The INSERT positional order in `persist_atom_runs` matches the column order asserted in Task 2/Task 5 tests; `post_id` is `UUID` (cast `$3::uuid`) because `posts.id` is a uuid.
- **No placeholders:** every step has concrete SQL / code / commands + expected output. The only runtime-variable is the migration's generated timestamp filename (Task 1 Step 1 prints it; later steps reference `<generated-timestamp>`).
- **Tests run without a DB:** unit tests use an asyncpg pool stub (mirrors `test_capability_outcomes.py`); the migration DDL is exercised by the `migrations-smoke` CI job against fresh Postgres. The worktree's venv-less constraint is satisfied — run with the main venv python.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
