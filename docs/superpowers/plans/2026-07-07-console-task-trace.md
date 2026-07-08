# Console Task-Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A console "Task Trace" — a live front-door board + a full-bleed per-task deep-dive that assembles the whole pipeline story (node spine, corpus, per-node output, QA/gate decisions, cost, Langfuse deeplinks) in one place, backed by incremental per-node capture so running/killed runs leave a legible trail.

**Architecture:** Four phases. **P1 — capture:** switch `atom_runs` from batch-at-end to incremental per-node upsert (+ `output_preview` column) so a live/killed run has rows. **P2 — read API:** one assembling service (`trace_read`) + 3 guarded routes. **P3 — console UI:** port the committed prototype into `js/trace.jsx`, wire a `Trace` rail section + `mode='trace'` full-bleed. **P4 — forensics/alerts/cross-links.** The trace's unit is a **request → 1..N runs** keyed `(task_id, run_id, template_slug)` — retries and composed media DAGs are both just "more runs."

**Tech Stack:** Python 3 / FastAPI / asyncpg / LangGraph (backend); no-build React + in-browser Babel (console); node:test (console tests); pytest + `db_pool` fixture (backend); Playwright (browser verify).

**Spec:** `docs/superpowers/specs/2026-07-07-console-task-trace-design.md` — read it first. **Prototype (FE source of truth to port):** `src/cofounder_agent/console/prototype-trace.html` (gitignored, on-disk in this worktree; delete in the final task).

## Global Constraints

- **No env vars / no hardcoded tunables** — new config goes in `services/settings_defaults.py` (`DEFAULTS` dict), never a migration; read via `site_config.get(key, default)`.
- **Seed discipline** — settings in `settings_defaults.py`; migrations are schema DDL only.
- **Adapter-contract** — no inline SQL in `routes/`; SQL lives in the service (`trace_read.py`). Routes are thin serializers.
- **Fail-loud, never silent** — required config missing → raise; but _capture_ is best-effort (logged + swallowed) and must never break generation.
- **Honest-empty** — reads guard to `{}`/`[]`/`null` on error; the UI never fabricates a spine or number.
- **Colorblind-safe** — status carried by glyph (`✓ ◐ ○ ✕`) + number; color only reinforces. Primary=cyan, warn=amber, success=mint (always with ✓), error=red (always with ✕).
- **One global lexical scope** in the console — do NOT re-`const {useState}=React` in `trace.jsx`; `primitives.jsx` owns the hooks (memory: sub-project C build gotcha #2).
- **Census guards, same commit** — a new `services/*.py` trips the `regen-services-doc` CI script (regen + commit `docs/reference/services.md`); a new worker route trips `test_route_registration` count guards (bump + docstring). Both bit sub-projects C/E.
- **Mirror** — `console/` is stripped from the public mirror (FE ships operator-only); `trace_read.py`/`trace_routes.py`/migration are OSS — carry no operator identity. Audit both axes.
- **Every commit** ends with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer; work on the current branch, never main.

---

## Phase 1 — Capture: incremental `atom_runs` + `output_preview`

_The load-bearing change (spec §5.2). After this phase a running or killed run has per-node rows._

### Task 1.1: Migration + setting — `output_preview` column, `(run_id, seq)` uniqueness

**Files:**

- Create: `src/cofounder_agent/services/migrations/<generated>_atom_runs_output_preview.py` (via `python scripts/new-migration.py "atom_runs output_preview + run_id/seq unique"`)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (add one `DEFAULTS` key)
- Test: `src/cofounder_agent/tests/integration_db/test_migrations_smoke.py` is the existing fresh-DB gate (no new test file; verify via the smoke runner)

**Interfaces:**

- Produces: `atom_runs.output_preview text` (nullable); `UNIQUE (run_id, seq)` constraint named `atom_runs_run_id_seq_key`; setting `atom_runs_output_preview_max_bytes` default `"2048"`.

- [ ] **Step 1: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "atom_runs output_preview + run_id/seq unique"`
Expected: prints a new path `services/migrations/YYYYMMDD_HHMMSS_atom_runs_output_preview.py`.

- [ ] **Step 2: Write the migration body**

Replace the generated `upgrade` body with (keep the file's existing runner-interface scaffolding):

```python
async def upgrade(conn) -> None:
    # Per-node output snapshot for the console task-trace (bounded at write time).
    await conn.execute(
        "ALTER TABLE atom_runs ADD COLUMN IF NOT EXISTS output_preview text"
    )
    # Incremental persist upserts on (run_id, seq); a run's seq is monotonic
    # (rescue-loop re-runs get fresh seqs), so this pair is unique per record.
    # Guard against pre-existing dupes from the old batch path before adding it.
    await conn.execute(
        """
        DELETE FROM atom_runs a USING atom_runs b
         WHERE a.ctid < b.ctid AND a.run_id = b.run_id AND a.seq = b.seq
        """
    )
    await conn.execute(
        """
        ALTER TABLE atom_runs
          ADD CONSTRAINT atom_runs_run_id_seq_key UNIQUE (run_id, seq)
        """
    )
```

Note: `ADD CONSTRAINT` has no `IF NOT EXISTS` on this PG; the runner records applied migrations by filename so it runs once. On the squashed baseline `output_preview` won't exist yet, so `ADD COLUMN IF NOT EXISTS` is correct for both fresh + prod.

- [ ] **Step 3: Add the setting default**

In `services/settings_defaults.py`, add to the `DEFAULTS` dict (alphabetical neighborhood of `atom_runs_capture_enabled`):

```python
    "atom_runs_output_preview_max_bytes": "2048",
```

- [ ] **Step 4: Run the migration smoke test (fresh DB)**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_smoke.py`
Expected: applies clean; exit 0. Then `python scripts/ci/migrations_lint.py` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/ src/cofounder_agent/services/settings_defaults.py
git commit -m "feat(atom_runs): output_preview column + (run_id,seq) uniqueness for incremental capture"
```

### Task 1.2: `TemplateRunRecord.output_preview` + capture it at record-append

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py:665` (the `TemplateRunRecord` dataclass)
- Modify: `src/cofounder_agent/services/pipeline_architect.py` (the two `record_sink.append(...)` sites, ~L1106 success / ~L1142 failure)
- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect_output_preview.py` (new)

**Interfaces:**

- Produces: `TemplateRunRecord.output_preview: str | None = None`; a module-level `_preview(out: dict, keys: list[str], max_bytes: int) -> str` in `pipeline_architect.py`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_pipeline_architect_output_preview.py
from services.pipeline_architect import _preview

def test_preview_serializes_changed_keys_and_truncates():
    out = {"draft": "x" * 5000, "title": "Hello"}
    s = _preview(out, ["draft", "title"], max_bytes=64)
    assert "title" in s and "Hello" in s   # small key survives
    assert len(s.encode()) <= 64 + 16      # bounded (allow small ellipsis slack)

def test_preview_skips_control_keys_and_empty():
    assert _preview({"_halt": True}, ["_halt"], 2048) == ""
    assert _preview({}, [], 2048) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_pipeline_architect_output_preview.py -q`
Expected: FAIL — `ImportError: cannot import name '_preview'`.

- [ ] **Step 3: Implement `_preview` + the dataclass field + wire capture**

In `template_runner.py` at the `TemplateRunRecord` dataclass (line 665), add the field:

```python
    output_preview: str | None = None
```

In `pipeline_architect.py`, add the helper near the top-level helpers:

```python
def _preview(out: dict, keys: list[str], max_bytes: int) -> str:
    """Bounded, human-readable snapshot of a node's changed output values.

    Observational only — skips control channels (leading '_') and truncates
    to max_bytes so a full draft never lands in every atom_runs row.
    """
    import json as _json
    payload = {
        k: out[k]
        for k in keys
        if k in out and not str(k).startswith("_")
    }
    if not payload:
        return ""
    s = _json.dumps(payload, default=str, ensure_ascii=False)
    if len(s.encode()) <= max_bytes:
        return s
    return s.encode()[:max_bytes].decode("utf-8", "ignore") + "…"
```

At the **success** append (~L1106), read the cap off the run config and set the field. The node closure has `config`/`site_config` in scope via the surrounding factory — thread the max-bytes as a captured local `_preview_max` computed once in `make_stage_node`/atom-adapter setup (`int(site_config.get("atom_runs_output_preview_max_bytes","2048") or 2048)`), then:

```python
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=atom_name, ok=True,
                        detail=f"{len(str(out.get('content','') or ''))} chars",
                        elapsed_ms=elapsed_ms,
                        node_id=node_id,
                        output_preview=_preview(out, output_keys, _preview_max),
                        metrics={
                            "input_keys": input_keys,
                            "output_keys": output_keys,
                            "input_digest": digest_keys(input_keys),
                            "output_digest": digest_keys(output_keys),
                        },
                    )
                )
```

At the **failure** append (~L1142), set `output_preview=f"{type(exc).__name__}: {exc}"[:_preview_max]` so a crashed node still carries its error text.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_pipeline_architect_output_preview.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_pipeline_architect_output_preview.py
git commit -m "feat(atom_runs): capture bounded per-node output_preview at record-append"
```

### Task 1.3: `persist_one_atom_run` (idempotent upsert, writes `output_preview`)

**Files:**

- Modify: `src/cofounder_agent/services/atom_runs.py` (extract single-row persist; upsert; write `output_preview`)
- Test: `src/cofounder_agent/tests/integration_db/test_atom_runs_incremental.py` (new; uses `db_pool`)

**Interfaces:**

- Produces: `async def persist_one_atom_run(pool, *, run_id, task_id, template_slug, seq, record, site_config=None) -> int` (0/1 rows). `persist_atom_runs(...)` unchanged signature but now loops `persist_one_atom_run` and is idempotent (upsert).
- Consumes: `TemplateRunRecord.output_preview` (Task 1.2).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration_db/test_atom_runs_incremental.py
import pytest
from services.atom_runs import persist_one_atom_run
from services.template_runner import TemplateRunRecord

@pytest.mark.asyncio
async def test_persist_one_is_idempotent_on_run_id_seq(db_pool):
    rec = TemplateRunRecord(name="qa.critic", ok=True, elapsed_ms=1200,
                            node_id="qa_critic", output_preview='{"x":1}',
                            metrics={"model": "sonnet", "output_keys": ["x"]})
    a = await persist_one_atom_run(db_pool, run_id="R1", task_id="T1",
                                   template_slug="canonical_blog", seq=5, record=rec)
    b = await persist_one_atom_run(db_pool, run_id="R1", task_id="T1",
                                   template_slug="canonical_blog", seq=5, record=rec)
    assert a == 1 and b == 1  # second is an upsert, not a dup
    async with db_pool.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM atom_runs WHERE run_id='R1' AND seq=5")
        prev = await c.fetchval("SELECT output_preview FROM atom_runs WHERE run_id='R1' AND seq=5")
    assert n == 1 and prev == '{"x":1}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_atom_runs_incremental.py -q`
Expected: FAIL — `ImportError: cannot import name 'persist_one_atom_run'`.

- [ ] **Step 3: Refactor persist to a single-row upsert**

In `atom_runs.py`, extract the per-row body (currently the `for seq, r in enumerate(records)` loop in `persist_atom_runs`) into:

```python
async def persist_one_atom_run(pool, *, run_id, task_id, template_slug, seq, record, site_config=None) -> int:
    """Upsert one atom_runs row (keyed run_id+seq). Best-effort, idempotent.

    Used both incrementally (per node, live) and by the end-of-run batch —
    the ON CONFLICT makes a re-persist a no-op update, never a duplicate.
    """
    if pool is None or record is None:
        return 0
    if not _capture_enabled(site_config):
        return 0
    try:
        async with pool.acquire() as conn:
            await _write_row(conn, run_id, task_id, template_slug, seq, record)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atom_runs] persist_one_atom_run failed: %s", exc)
        return 0
```

Add `_write_row(conn, run_id, task_id, template_slug, seq, r)` containing the existing field extraction (catalog/tier/model/cost/keys/digests) PLUS `output_preview = getattr(r, "output_preview", None)`, and change the INSERT to:

```sql
INSERT INTO atom_runs
  (run_id, task_id, template_slug, seq, atom, node_id, tier, model,
   latency_ms, cost, retries, status, input_digest, output_digest,
   input_keys, output_keys, metrics, output_preview)
VALUES ($1,...,$17::jsonb,$18)
ON CONFLICT (run_id, seq) DO UPDATE SET
  status=EXCLUDED.status, latency_ms=EXCLUDED.latency_ms, cost=EXCLUDED.cost,
  model=EXCLUDED.model, output_keys=EXCLUDED.output_keys,
  output_digest=EXCLUDED.output_digest, metrics=EXCLUDED.metrics,
  output_preview=EXCLUDED.output_preview
```

Rewrite `persist_atom_runs` to loop `_write_row` inside one acquired connection (unchanged external behavior, now upsert). Add `persist_one_atom_run` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_atom_runs_incremental.py -q`
Expected: PASS. Also run the existing atom_runs tests: `poetry run pytest tests/ -k atom_runs -q` → green.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/atom_runs.py src/cofounder_agent/tests/integration_db/test_atom_runs_incremental.py
git commit -m "feat(atom_runs): persist_one_atom_run upsert + output_preview write"
```

### Task 1.4: Thread `on_record` — incremental persist during the run

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py` (add `on_record` param alongside `on_event`; call it after each `record_sink.append`)
- Modify: `src/cofounder_agent/services/template_runner.py` (build an `on_record` closure that calls `persist_one_atom_run`; pass it into `build_graph_from_spec`; keep the end-of-run batch as a safety net)
- Test: `src/cofounder_agent/tests/unit/services/test_on_record_incremental.py` (new; monkeypatch persist)

**Interfaces:**

- Consumes: `persist_one_atom_run` (1.3).
- Produces: `build_graph_from_spec(..., on_record: Callable[[int, TemplateRunRecord], Awaitable[None]] | None = None)`; `TemplateRunner` computes `run_id=thread_id` and persists each record live.

- [ ] **Step 1: Write the failing test** — assert each node append triggers exactly one incremental persist, keyed by monotonic seq:

```python
# tests/unit/services/test_on_record_incremental.py
import asyncio
from services import pipeline_architect as pa

def test_record_sink_append_fires_on_record(monkeypatch):
    seen = []
    async def fake_on_record(seq, rec): seen.append((seq, rec.name))
    # a minimal record_sink wrapper: appending must call on_record with its index
    sink = pa._RecordingSink(on_record=fake_on_record)   # new helper (Step 3)
    from services.template_runner import TemplateRunRecord
    asyncio.run(sink.append_and_notify(TemplateRunRecord(name="a", ok=True)))
    asyncio.run(sink.append_and_notify(TemplateRunRecord(name="b", ok=True)))
    assert seen == [(0, "a"), (1, "b")] and len(sink) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_on_record_incremental.py -q`
Expected: FAIL — `AttributeError: module 'services.pipeline_architect' has no attribute '_RecordingSink'`.

- [ ] **Step 3: Implement the seam**

In `pipeline_architect.py`, add a tiny list-subclass sink that notifies on append (keeps the existing `record_sink.append(...)` call sites working — they call `.append`, and the notify is driven by the wrapper the architect builds when `on_record` is set):

```python
class _RecordingSink(list):
    """A record_sink that also fires an async on_record(seq, record) after
    each append, so incremental persistence sees a node the instant it lands."""
    def __init__(self, on_record=None):
        super().__init__()
        self._on_record = on_record
    async def append_and_notify(self, rec):
        seq = len(self)
        self.append(rec)
        if self._on_record is not None:
            try:
                await self._on_record(seq, rec)
            except Exception:  # noqa: BLE001 — capture never breaks the run
                pass
```

Where node closures currently do `record_sink.append(rec)` (both sites), call `await record_sink.append_and_notify(rec)` **when** the sink is a `_RecordingSink`, else fall back to `.append` (keeps other callers working). Thread `on_record` from `build_graph_from_spec` into the sink it constructs.

In `template_runner.py`, before compiling, build the closure and sink:

```python
async def _persist_record(seq, rec):
    from services.atom_runs import persist_one_atom_run
    await persist_one_atom_run(
        self._pool, run_id=thread_id,
        task_id=str(initial_state.get("task_id") or "") or None,
        template_slug=template_slug, seq=seq, record=rec,
        site_config=self._site_config,
    )
```

Pass `on_record=_persist_record` into `build_graph_from_spec`. Leave the end-of-run `persist_atom_runs(...)` batch at L1460 in place — it now upserts, so it's a harmless safety net that also covers the legacy factory path.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_on_record_incremental.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/services/template_runner.py src/cofounder_agent/tests/unit/services/test_on_record_incremental.py
git commit -m "feat(atom_runs): incremental per-node persist via on_record (live + partial-on-kill traces)"
```

### Task 1.5: Integration guard — a killed run leaves a partial trace

**Files:**

- Test: `src/cofounder_agent/tests/integration_db/test_partial_trace_on_halt.py` (new; `db_pool`)

- [ ] **Step 1: Write the test** — run a 3-node graph where node 2 raises; assert `atom_runs` has rows for nodes 0 and 1 (persisted incrementally) even though the run halted before the end-of-run batch:

```python
@pytest.mark.asyncio
async def test_halt_midway_leaves_partial_atom_runs(db_pool, seeded_site_config):
    # build a tiny 3-node spec where node[1] raises; run via TemplateRunner
    # (see tests/integration_db/conftest for the graph_def fixture helper)
    ...
    async with db_pool.acquire() as c:
        seqs = await c.fetch("SELECT seq, status FROM atom_runs WHERE run_id=$1 ORDER BY seq", run_id)
    assert [r["seq"] for r in seqs] == [0, 1, 2]   # 2 = the halted node
    assert seqs[-1]["status"] in ("halted", "error")
```

- [ ] **Step 2: Run → verify PASS** (`poetry run pytest tests/integration_db/test_partial_trace_on_halt.py -q`). If the fixture helper doesn't exist, add a minimal graph_def builder in the test.

- [ ] **Step 3: Commit** — `test(atom_runs): partial trace survives a mid-graph halt`.

---

## Phase 2 — Read API: `trace_read` + routes

### Task 2.1: `services/trace_read.py` — the assembling read layer

**Files:**

- Create: `src/cofounder_agent/services/trace_read.py`
- Modify: `docs/reference/services.md` (census: regen)
- Test: `src/cofounder_agent/tests/integration_db/test_trace_read.py` (new; `db_pool`)

**Interfaces (Produces — later tasks + the routes rely on these exact names):**

- `async def runs_for_request(pool, task_id: str) -> list[dict]` → `[{run_id, template_slug, kind, status, node_count, halted_at}]` (kind ∈ `"content"|"media"|"retry"`; grouped/ordered content-first). _(This is the R7 seam — the one place "gather a request's runs" lives.)_
- `async def get_active(pool, recent_limit: int) -> dict` → `{runs:[...], recent:[...]}`.
- `async def get_summary(pool) -> dict` → 24h KPIs (cached by the route, not here).
- `async def get_trace(pool, task_id: str, run_id: str | None) -> dict` → the deep-dive assembly (spec §6 shape).

- [ ] **Step 1: Write the failing test** (seed 2 runs under one task_id — a killed `canonical_blog` + a completed one + a `media_pipeline` run — and assert grouping):

```python
@pytest.mark.asyncio
async def test_runs_for_request_groups_retries_and_media(db_pool):
    # seed atom_runs: (T1, R1, canonical_blog, halted), (T1, R2, canonical_blog, ok),
    #                 (T1, R3, media_pipeline, ok)
    ...
    runs = await runs_for_request(db_pool, "T1")
    kinds = {r["run_id"]: r["kind"] for r in runs}
    assert kinds == {"R1": "retry", "R2": "content", "R3": "media"}
    assert runs[0]["run_id"] == "R2"  # latest content run first (default selection)
```

- [ ] **Step 2: Run → FAIL** (`ImportError`).

- [ ] **Step 3: Implement `trace_read.py`.** Real queries (asyncpg), all read-only. Key ones:

`runs_for_request` — one query over `atom_runs` grouped by `(run_id, template_slug)`:

```python
_RUNS_SQL = """
    SELECT run_id, template_slug,
           count(*)                              AS node_count,
           bool_or(status IN ('halted','error')) AS halted,
           max(created_at)                        AS last_at
      FROM atom_runs
     WHERE task_id = $1
     GROUP BY run_id, template_slug
     ORDER BY max(created_at) DESC
"""
```

Then in Python: `kind = "media" if template_slug in {"media_pipeline","podcast_pipeline"} else ("content" if run is the latest of its template else "retry")`; status from the run's terminal row; `halted_at` = seq of first halted node. Content run(s) sorted first, latest content run is the default selection.

`get_active` — running/pending from `pipeline_tasks` (live progress fields) joined to the current run's node count:

```python
_ACTIVE_SQL = """
    SELECT pt.task_id, pt.topic, pt.template_slug, pt.status, pt.stage,
           pt.percentage, pt.message, pt.started_at, pt.last_progress_at,
           (SELECT count(*) FROM atom_runs ar WHERE ar.run_id = pt.task_id) AS nodes_done
      FROM pipeline_tasks pt
     WHERE pt.status IN ('pending','in_progress','awaiting_gate')
     ORDER BY pt.started_at DESC NULLS LAST
"""
_RECENT_SQL = """
    SELECT task_id, topic, template_slug, status, started_at, completed_at
      FROM pipeline_tasks
     WHERE status IN ('approved','awaiting_approval','published','rejected','failed','cancelled')
     ORDER BY COALESCE(completed_at, updated_at) DESC
     LIMIT $1
"""
```

`get_summary` — 24h aggregates (`count`, pass-rate, `avg(quality)`, `avg(cost)`, `avg(wall)`, rescued count) over `pipeline_tasks`/`atom_runs`.

`get_trace` — call `runs_for_request`; pick `run_id` (arg or default); fetch that run's nodes (`SELECT ... FROM atom_runs WHERE run_id=$1 ORDER BY seq`); pull corpus from `pipeline_versions.stage_data->'task_metadata'->>'research_context'`; QA summary + decisions derived from the run's nodes + `qa_reviews`; cost rollup grouped by atom; images from `stage_data`; final_post from `pipeline_versions`/`posts`; `halt` = first halted node (for open-on-halt); `langfuse.run_url` from `site_config.get("langfuse_public_url")` + the run's trace id if present. Everything guarded → honest-empty.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Regen the services doc (census guard A)**

Run: `cd src/cofounder_agent && python scripts/ci/regen_services_doc.py` (or the documented script name; check `docs/reference/services.md` header for the exact command). Commit the regenerated doc with the code.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/trace_read.py docs/reference/services.md src/cofounder_agent/tests/integration_db/test_trace_read.py
git commit -m "feat(trace): trace_read assembling read layer (runs_for_request/active/summary/get_trace)"
```

### Task 2.2: `routes/trace_routes.py` + worker-manifest registration

**Files:**

- Create: `src/cofounder_agent/routes/trace_routes.py`
- Modify: `src/cofounder_agent/utils/route_registration.py` (add 1 tuple to `_WORKER_ROUTES`)
- Modify: `src/cofounder_agent/tests/unit/utils/test_route_registration.py` (bump the count assert + history line)
- Test: `src/cofounder_agent/tests/unit/routes/test_trace_routes.py` (new; TestClient, monkeypatch `trace_read`)

**Interfaces:**

- Consumes: `services.trace_read`.
- Produces: `router` (prefix `/api/trace`) with `GET /active`, `GET /summary`, `GET /{task_id}`.

- [ ] **Step 1: Write the failing test** — `GET /api/trace/active` returns `{runs, recent}` with `trace_read.get_active` monkeypatched; `GET /api/trace/T1` returns the assembled shape. (Mirror `tests/unit/routes/` patterns for auth + `app.state.http_client`.)

- [ ] **Step 2: Run → FAIL** (router not found / 404).

- [ ] **Step 3: Implement the router** (thin; model on `routes/traces_routes.py`). Cache `summary` in-process ≤60s:

```python
router = APIRouter(prefix="/api/trace", tags=["trace"],
                   dependencies=[Depends(verify_api_token)])

@router.get("/active", response_model=dict)
async def active(request: Request, site_config=Depends(get_site_config_dependency)):
    pool = request.app.state.db_pool
    limit = int(site_config.get("trace_recent_limit", "10") or 10)
    try:
        return await trace_read.get_active(pool, recent_limit=limit)
    except Exception:  # noqa: BLE001
        return {"runs": [], "recent": []}

# /summary — wrap get_summary in a 60s TTL cache (module-level {ts,val})
# /{task_id} — run_id via Query(""), return get_trace(...) guarded to {}
```

Add setting `trace_recent_limit` = `"10"` to `settings_defaults.py` (R6).

- [ ] **Step 4: Register + bump the manifest guard (census guard B).** Add to `_WORKER_ROUTES` in `route_registration.py`:

```python
    ("routes.trace_routes", "router", "trace_router", "console task-trace read API (#console-task-trace)"),
```

In `tests/unit/utils/test_route_registration.py`, bump `assert len(_WORKER_ROUTES) == N` to `N+1` and add the one-line history note the test's docstring keeps.

- [ ] **Step 5: Run → PASS** (`poetry run pytest tests/unit/routes/test_trace_routes.py tests/unit/utils/test_route_registration.py -q`).

- [ ] **Step 6: Commit** — `feat(trace): /api/trace read routes + worker-manifest registration`.

### Task 2.3: Contract-net rows + fixtures

**Files:**

- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` (add rows for `/api/trace/active`, `/api/trace/summary`, `/api/trace/{id}`)
- Modify: `.../contracts/fixtures/` (record real fixtures)

- [ ] **Step 1:** Add tier-1 (request) + tier-2 (body) + tier-3 (shape) rows following the existing manifest format.
- [ ] **Step 2:** Record fixtures against a live worker: `node console/js/__tests__/contracts/record-fixtures.mjs` (per the contracts README).
- [ ] **Step 3:** Run `npm run test` (console) → the contract net passes.
- [ ] **Step 4: Commit** — `test(trace): contract-net rows + fixtures for /api/trace`.

---

## Phase 3 — Console UI

### Task 3.1: `PX.api` trace methods

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (add 3 methods)
- Test: `src/cofounder_agent/console/js/__tests__/api.trace.test.js` (new)

**Interfaces (Produces):** `PX.api.traceActive()`, `PX.api.traceSummary()`, `PX.api.traceDetail(taskId, runId)` — all guarded (unreachable → `{runs:[],recent:[]}` / `{}` / `{}`), never throw. Mock mode returns `PX.trace*` seeds (add small honest mock seeds to `data.js`).

- [ ] **Step 1:** Failing test — `traceActive()` returns `{runs:[],recent:[]}` when `http` rejects (mirror `api.prom.test.js`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement, mirroring the guarded `traces()`/`promRange()` methods already in `api.js`.
- [ ] **Step 4:** Run → PASS (`npm run test`).
- [ ] **Step 5: Commit** — `feat(console): PX.api trace methods (guarded)`.

### Task 3.2: `js/trace.jsx` — port the prototype to live data

**Files:**

- Create: `src/cofounder_agent/console/js/trace.jsx`
- Modify: `src/cofounder_agent/console/index.html` (add the `<script type="text/babel" src="js/trace.jsx">` after `panels2.jsx`)
- Test: `src/cofounder_agent/console/js/__tests__/trace.test.js` (new)

**Port source:** `console/prototype-trace.html` is the component source of truth. Lift `TraceBoard` (+ health strip), `TraceDeepDive`, `TraceSpine`, `RunOverview`, `NodeDetail`, and the helpers (`money`, `glyph`, `scoreColor`, timing-bar math). **Do NOT** re-declare React hooks (`primitives.jsx` owns them) or re-inline `Icon` (use `window.Icon`). Expose components on `window` (like other console modules).

**Live-data mapping (replace every mock const with the API shape):**
| Prototype mock | Live source |
|---|---|
| `LIVE` / `RECENT` | `PX.api.traceActive()` → `{runs, recent}` |
| `HEALTH` | `PX.api.traceSummary()` |
| `RUN.nodes` | `traceDetail(taskId).nodes` |
| `CORPUS` | `.corpus` | `DECISIONS` | `.decisions` | `COSTROLL`/`MODELS` | `.cost_rollup` | `IMAGES` | `.images` | `DDIFF` | `.draft_diff` (fast-follow — render only if present) |
| run/DAG switcher | `.runs` (Task 2.1 grouping) |
| open-on-halt default `sel` | `.halt.seq` when present, else `'overview'` |

- [ ] **Step 1: Write failing tests** (node:test, jsdom-free — test the pure helpers + a render smoke via the project's existing console-test harness). Assert: spine groups by `group`; `glyph(status)` maps halt→✕/mint etc.; timing-bar width = `ms/maxMs`; run-selector defaults to latest content run; honest-empty when `nodes:[]`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Port + wire.** Keep the prototype CSS by moving the `<style>` block into `console/css/trace.css` (new) and `<link>` it from `index.html` (matches how the console splits CSS).
- [ ] **Step 4: Run → PASS** (`npm run test`).
- [ ] **Step 5: Commit** — `feat(console): trace.jsx (board + deep-dive) ported to live data`.

### Task 3.3: `app.jsx` wiring — rail, board section, `mode='trace'`, hash routing

**Files:**

- Modify: `src/cofounder_agent/console/js/app.jsx` (RAIL entry; `sec-trace`; `mode==='trace'` branch; `traceTaskId` state; `usePolledResource`; `#trace/<id>` hash on load)
- Test: covered by the browser verify (Task 3.4) + a small app-state unit if the harness supports it.

**Interfaces:** Consumes `window.TraceBoard`, `window.TraceDeepDive`, `PX.api.trace*`.

- [ ] **Step 1:** Add `{ id: 'trace', icon: 'pulse', label: 'Trace' }` to `RAIL` (after `pipeline`).
- [ ] **Step 2:** Add live board polling near the other `usePolledResource` blocks:

```jsx
const traceR = window.PXR.usePolledResource(
  () =>
    PX.api.isLive()
      ? PX.api.traceActive()
      : Promise.resolve(PX.traceActive || { runs: [], recent: [] }),
  { intervalMs: 30_000, key: 'traceActive' }
);
```

- [ ] **Step 3:** Render `<TraceBoard fresh={traceR} data={traceR.data} onOpen={(taskId)=>{ setTraceTaskId(taskId); setMode('trace'); location.hash = 'trace/'+taskId; }} />` in a new `<div id="sec-trace">` in the console masonry.
- [ ] **Step 4:** Add the full-bleed branch (mirror the `mode==='map'` block): `{mode === 'trace' && <TraceDeepDive taskId={traceTaskId} onBack={()=>{ setMode('console'); location.hash=''; }} A={A} />}`. `A` gives it the existing approve/reject/publish/cancel actions.
- [ ] **Step 5:** On load, parse `location.hash` — if `#trace/<id>`, set `traceTaskId` + `mode='trace'` (R4 — the alert deeplink target).
- [ ] **Step 6: Commit** — `feat(console): wire Trace rail section + deep-dive mode + #trace/<id> routing`.

### Task 3.4: Browser verify (real console, live data)

- [ ] **Step 1:** Start a ThreadingHTTPServer no-cache server over `console/` (single-threaded `http.server` wedges under the preview browser — memory `reference_console_deploy_live`).
- [ ] **Step 2:** Playwright: load the console (live worker at :8002), open the Trace rail, click a running card → deep-dive; select nodes; select an alternate run; confirm honest-empty on a historical task. Screenshot each.
- [ ] **Step 3:** Fix any render errors (check console errors — the `text/babel` shared-scope + cross-realm `deepEqual` gotchas). Commit fixes.
- [ ] **Step 4: Commit** — `test(console): browser-verify Trace board + deep-dive`.

---

## Phase 4 — Forensics, alerts, cross-links

### Task 4.1: Open-on-halt (FE)

- [ ] **Step 1:** In `TraceDeepDive`, default the selected node to `trace.halt.seq` when the run is halted/rejected (data already provided by `get_trace`); surface the veto reason in the header. Test: a halted-run fixture opens pre-selected on the halting node.
- [ ] **Step 2: Commit** — `feat(console): deep-dive opens on the halting node for failed runs`.

### Task 4.2: Alert → trace deeplink

**Files:** Modify the brain/firefighter operator-notify path (`services/integrations/operator_notify.py` and/or the firefighter notify) to append a console deeplink for task-scoped alerts.

- [ ] **Step 1:** Where a stuck/failed-task alert is composed, append `\nTrace: {console_base}/#trace/{task_id}` using a new `console_base_url` setting (default `""` → omit when unset; no fabricated URL). Test: the notify payload contains the deeplink when the setting + a task_id are present, and omits it when unset.
- [ ] **Step 2:** Add `console_base_url` to `settings_defaults.py` (default `""`).
- [ ] **Step 3: Commit** — `feat(firefighter): task alerts deeplink to the console trace`.

### Task 4.3: Post ↔ trace cross-links (FE)

- [ ] **Step 1:** In the existing task/post drawer (`drawer.jsx`) add a "View trace" affordance that sets `#trace/<pipeline_task_id>` (from `metadata.pipeline_task_id`). In the deep-dive header, link out to the post/preview when `final_post.post_id`/`preview_token` is present.
- [ ] **Step 2: Commit** — `feat(console): post↔trace cross-links via pipeline_task_id`.

### Task 4.4: Cleanup — remove the prototype

- [ ] **Step 1:** `git rm -f --ignore-unmatch` is N/A (gitignored); just delete the on-disk file: `rm src/cofounder_agent/console/prototype-trace.html`. Confirm no `index.html`/route references it.
- [ ] **Step 2:** Delete the root screenshot PNGs (`trace-proto-*.png`) if present. Nothing to commit (both untracked).

---

## Self-Review

**Spec coverage** (each spec §11 v1 item → task):

1. Board + health strip → 3.2/3.3 (FE) + 2.1 `get_active`/`get_summary`. ✓
2. Deep-dive spine/node-detail/overview → 3.2 + 2.1 `get_trace`. ✓ (draft-diff + anomaly flag correctly deferred: rendered only if `.draft_diff` present; anomaly slot dormant — §11 fast-follow.)
3. Incremental `atom_runs` + `output_preview` → Phase 1 (1.1–1.5). ✓
4. Run/DAG switcher + honest-empty → 2.1 `runs_for_request` + 3.2 mapping + guards throughout. ✓
5. Inline actions + open-on-halt → 3.3 (`A`) + 4.1. ✓
6. Alert→trace + post↔trace → 4.2 + 4.3. ✓
7. Langfuse deeplinks → `get_trace.langfuse.run_url` (2.1) + per-node in `NodeDetail` (reuse `/api/traces?task_id=`). ✓

**Census guards:** services-doc regen (2.1 Step 5) + worker-manifest bump (2.2 Step 4) both scheduled in-commit. ✓
**Placeholder scan:** helper names (`_preview`, `persist_one_atom_run`, `_RecordingSink`, `runs_for_request`, `get_active/summary/trace`) are defined where first used and reused consistently. The one deliberately-open item is the exact `regen_services_doc` script name (2.1 Step 5) — the implementer reads it from the `docs/reference/services.md` header, which names its own generator.
**Type consistency:** `run_id == thread_id` throughout; `(task_id, run_id, template_slug)` is the run key in 1.1/1.3/2.1; `kind ∈ content|media|retry` set in 2.1 and consumed in 3.2. ✓
**Mirror:** FE (`trace.jsx`, `trace.css`) rides the stripped `console/`; backend (`trace_read`, `trace_routes`, migration) is OSS and identity-free. ✓

**Phasing note:** P1 is independently valuable + testable (partial-trace-on-kill) and can merge alone; P2 is testable via its unit/db tests; P3 needs P2; P4 needs P3. Safe to ship P1→P2→P3→P4 as separate PRs, or one branch.
