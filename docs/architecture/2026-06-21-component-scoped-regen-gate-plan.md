# Component-scoped regen gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `preview_gate` to `canonical_blog` that lets the operator approve, reject, or surgically regenerate just the images or just the text of a post under review.

**Architecture:** One `atoms.approval_gate` node after the persist/record finalize nodes pauses the graph via LangGraph `interrupt()`. The operator's decision routes through a generalized multi-target branch router to a bounded backward loop edge — back to the image block (`regen_images`) or the writer block (`regen_text`), or forward on approve. Reuses the existing `qa.rewrite` branch/loop-edge pattern; the only new graph-engine capability is multi-target `_goto` branching.

> **Design revision (2026-06-22, operator-approved): durable per-component consume-flag, not the resume value.**
> LangGraph re-executes the gate node top-to-bottom on resume, and `interrupt()` returns the resume value only _after_ `pause_at_gate`/notify already fired. So a decision that must _skip_ the pause (regen, like approve) has to be readable from durable DB state _before_ `pause_at_gate` — the same reason the existing approve path short-circuits on a `pipeline_gate_history` row rather than the resume value. Routing regen via the resume value would re-page the operator on every resume re-run; routing it via a durable gate*history row would re-fire on every loop-back (infinite loop). **Resolution:** per-component state on `pipeline_tasks` — `regen*<c>_pending BOOLEAN`(the one-shot signal the surface sets and the atom clears) +`regen_<c>_attempts INTEGER`(monotonic, for the cap + Grafana). The atom short-circuits on`pending`, clears it (consume), and `\_goto`s the block; loop-back finds `pending=false` and falls through to a single \_fresh_ review page. The cap lives in the surface (`regen_at_gate` refuses past the cap), so the atom stays cap-agnostic. This changes Tasks 3–6 below from the resume-value sketch; the resume value is still what un-pauses the graph (`Command(resume=...)`), it just doesn't carry the routing decision. Steering text is audited in `gate_history.feedback` now; threading it into the writer/image prompts is a noted follow-up, not part of the core loop.

**Tech Stack:** Python 3.13, LangGraph (Postgres checkpointer), Prefect dispatch, asyncpg, pytest. Spec: [`2026-06-21-component-scoped-regen-gate.md`](./2026-06-21-component-scoped-regen-gate.md).

## Global Constraints

- TDD: failing test first, watch it fail, minimal code to pass. (`feedback_iterate_with_qa_not_oneshot`)
- All changes via PR off `origin/main`; squash-merge on green CI; no merge commits.
- New `app_settings` defaults go in `services/settings_defaults.py` (seeded every boot, `ON CONFLICT DO NOTHING`) — NOT new migration files. Schema DDL (new columns) DOES go in a timestamped migration.
- `app_settings.value` is `NOT NULL`; `''` is the unset sentinel.
- Fail loud, no silent default (`feedback_no_silent_defaults`).
- CLI-first surface, then MCP (`feedback_cli_first`); adapters delegate to a service function, no inline SQL in `routes/`/`cli/`/`mcp-server/` (adapter-purity ratchet).
- Worktree test invocation: `poetry -C "<root>/src/cofounder_agent" run pytest "<abs path>" -q -p no:cacheprovider`.
- Develop behind `pipeline_gate_preview_gate=false`; the default flip to `true` is the LAST task, gated on end-to-end verification (Docker required).

## File Structure

| File                                                             | Responsibility  | Change                                                           |
| ---------------------------------------------------------------- | --------------- | ---------------------------------------------------------------- |
| `services/pipeline_architect.py`                                 | graph compiler  | generalize branch router to multi-target `_goto`                 |
| `modules/content/atoms/approval_gate.py`                         | the gate atom   | map resume decision → `_goto`/`_halt`; bound by attempt counters |
| `services/canonical_blog_spec.py`                                | graph_def       | insert `preview_gate` node + 3 edges                             |
| `services/migrations/<ts>_add_regen_counters.py`                 | schema          | `regen_images_attempts`/`regen_text_attempts` columns            |
| `services/migrations/<ts>_reseed_canonical_blog_preview_gate.py` | reseed          | re-seed graph_def with the gate                                  |
| `services/approval_service.py`                                   | gate decisions  | add `regen_at_gate(...)` service fn                              |
| `poindexter/cli/pipeline.py`                                     | CLI adapter     | `poindexter pipeline regen` subcommand                           |
| `mcp-server/<tools>`                                             | MCP adapter     | `regen_post` tool                                                |
| `services/settings_defaults.py`                                  | seeded defaults | `pipeline_gate_preview_gate`, `regen_*_max_attempts`             |

---

### Task 1: Generalize the compiler branch router to multiple targets

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py:833-913`
- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect.py`

**Interfaces:**

- Consumes: the existing `build_graph_from_spec(spec, ...)` and `PipelineState` (a `dict`-like with `_halt`/`_goto` keys).
- Produces: a compiler where one source node may carry **multiple** `"branch": true` out-edges; the router returns `state["_goto"]` when it matches any branch target, else the default forward target, else END on `_halt`.

- [ ] **Step 1: Write the failing test** — a spec whose node `D` has two branch targets (`A`, `B`) plus a default forward (`C`) routes `_goto` correctly.

```python
# test_pipeline_architect.py
import pytest
from services.pipeline_architect import build_graph_from_spec

def _spec_two_branch():
    # D -> {A (branch+loop back), B (branch+loop back), C (default forward)}
    return {
        "name": "twobranch", "version": 1,
        "nodes": [{"id": n, "atom": "stage.noop"} for n in ("A", "B", "C", "D")],
        "edges": [
            {"from": "A", "to": "D"},
            {"from": "D", "to": "A", "branch": True, "loop": True},
            {"from": "D", "to": "B", "branch": True, "loop": True},
            {"from": "D", "to": "C"},
            {"from": "B", "to": "C"},
            {"from": "C", "to": "END"},
        ],
    }

def test_multi_branch_router_routes_goto_to_each_target():
    # build_graph_from_spec must compile a node with 2 branch targets and a
    # default, without raising — single-target branch_by_src cannot.
    graph = build_graph_from_spec(_spec_two_branch())
    assert graph is not None
```

- [ ] **Step 2: Run it, watch it fail.** `poetry -C "...\src\cofounder_agent" run pytest "...test_pipeline_architect.py::test_multi_branch_router_routes_goto_to_each_target" -q -p no:cacheprovider` → FAIL (the second `branch` edge overwrites the first in `branch_by_src`, and the default-target computation `defaults = [d for d in dsts if d != branch_by_src[src]]` mis-handles 2 branch targets).

- [ ] **Step 3: Implement.** Change `branch_by_src` to collect a list, and make `_branch_router` accept a set of targets:

```python
# replace branch_by_src build (lines ~840-843)
branch_by_src: dict[str, list[str]] = {}
for e in edges:
    if e.get("branch"):
        branch_by_src.setdefault(e["from"], []).append(e["to"])

# replace _branch_router (lines ~875-898)
def _branch_router(branch_targets, default_target):
    targets = set(branch_targets)
    def _route(state):
        if state.get("_halt"):
            return END
        goto = state.get("_goto")
        if goto in targets:
            return goto
        return default_target
    _route.__name__ = "branch_to_" + "_".join(map(str, sorted(targets))) + "_or_default"
    return _route

# replace the branch case in the wiring loop (lines ~904-913)
if src in branch_by_src:
    bts = branch_by_src[src]
    resolved_bts = [_resolve(b) for b in bts]
    defaults = [d for d in dsts if d not in bts]
    default_target = _resolve(defaults[0]) if defaults else END
    mapping = {t: t for t in resolved_bts}
    mapping[default_target] = default_target
    mapping[END] = END
    g.add_conditional_edges(src, _branch_router(resolved_bts, default_target), mapping)
    continue
```

- [ ] **Step 4: Run the test + the full file.** Expected PASS, and the existing single-branch `qa.rewrite` tests still pass (single-element list is a strict generalization).

- [ ] **Step 5: Commit.** `git commit -m "feat(pipeline): multi-target _goto branch router in graph compiler"`

---

### Task 2: Backward branch+loop edges pass DAG validation

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py:535-575` (the loop-skip / indegree validation)
- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect.py`

**Interfaces:**

- Consumes: Task 1's multi-branch compiler.
- Produces: a compiler that accepts an edge carrying BOTH `"branch": true` and `"loop": true` (a conditional backward edge) — `loop` exempts it from indegree/DAG checks, `branch` wires it via `_goto`.

- [ ] **Step 1: Failing test** — the `_spec_two_branch()` from Task 1 (its branch edges are also `loop`) must pass reachability/DAG validation, asserting the backward branch targets don't trip the "edges that loop back" guard.

```python
def test_backward_branch_loop_edge_is_dag_exempt():
    from services.pipeline_architect import build_graph_from_spec
    # D->A and D->B are branch+loop backward edges; must not raise a DAG/
    # unreachable error at build/validate time.
    graph = build_graph_from_spec(_spec_two_branch())
    assert graph is not None
```

- [ ] **Step 2: Run, watch fail** (if Task 1 didn't already cover it — the validation at 535-575 may count the backward branch target's indegree or flag it). Expected: a validation error naming the loop/back edge.

- [ ] **Step 3: Implement.** In the indegree/reachability scan, skip any edge where `e.get("loop")` is truthy (it already does for the rescue back-edge at ~543/573 — extend the same guard so a `branch`+`loop` edge is treated as loop for validation). Confirm `e.get("loop")` is checked before both the indegree decrement and the reachability assertion.

- [ ] **Step 4: Run** the test + full file → PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(pipeline): treat branch+loop edges as DAG-exempt back-edges"`

---

### Task 3: pending-regen short-circuit → `_goto` (consume + re-pause)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/approval_gate.py:122-253`
- Test: `src/cofounder_agent/tests/unit/services/test_approval_interrupt.py` (the atom's existing test home — `_state_with_pool`, `FakeConn`, `FakePool` fakes live in `tests/unit/services/_gate_fakes.py`).

**Interfaces:**

- Consumes: a durable pending flag on `pipeline_tasks` (Task 4). Node config carries `regen_targets: dict[str,str]` mapping `"images"`/`"text"` to node ids, surfaced into state as `gate_regen_targets` (Task 9 wiring).
- Produces: a NEW short-circuit between the `rejected` check and the `approved` check. A pending image regen → `{"_goto": regen_targets["images"]}` (and clears the flag); a pending text regen → `{"_goto": regen_targets["text"]}`; neither pending → existing behaviour (approved passthrough / pause+interrupt). On a pending regen the atom does NOT pause/notify (no redundant page); the loop-back finds `pending=false` and falls through to a single fresh review page.

- [ ] **Step 1: Failing test** — a gate with `regen_images_pending=true` (and gate enabled, no approved/rejected row) routes `_goto` to the image block, does NOT pause, and clears the flag.

```python
# test_approval_interrupt.py — mirrors the existing _state_with_pool tests
@pytest.mark.unit
async def test_pending_image_regen_routes_goto_and_consumes(monkeypatch):
    pause_calls: list = []

    async def _fake_pause(**kw):
        pause_calls.append(kw)

    monkeypatch.setattr("services.approval_service.is_gate_enabled", lambda *a, **k: True)
    monkeypatch.setattr("services.approval_service.pause_at_gate", _fake_pause)

    conn = FakeConn(fetchrow_results=[
        None,  # _gate_decision: no approved/rejected row
        {"regen_images_pending": True, "regen_text_pending": False},  # _pending_regen
    ])
    state = _state_with_pool(
        conn,
        gate_name="preview_gate",
        gate_regen_targets={"images": "plan_image_markers", "text": "generate_draft"},
        title="x", topic="t",
    )
    out = await approval_gate.run(state)
    assert out.get("_goto") == "plan_image_markers"
    assert "_halt" not in out
    assert pause_calls == []  # consumed at short-circuit → no pause/page
    assert any("regen_images_pending = false" in q.lower() for q in conn.executed)  # flag cleared
```

(`FakeConn` gains an `executed: list[str]` capture and `fetchrow_results` queue if not already present — extend the fake, it is test-only.)

- [ ] **Step 2: Run, watch fail** → today the atom has no `_pending_regen` read; with no approved/rejected row it falls straight through to `pause_at_gate`, so `pause_calls` is non-empty and `_goto` is absent.

- [ ] **Step 3: Implement.** Add the short-circuit + two helpers, and a tiny decision-shape helper:

```python
def _regen_output(component: str, regen_targets: dict[str, str]) -> dict[str, Any]:
    target = (regen_targets or {}).get(component)
    if not target:
        return {"_halt": True,
                "_halt_reason": f"preview_gate: no regen target configured for {component!r}"}
    return {"_goto": target}

async def _pending_regen(pool: Any, task_id: str) -> str | None:
    """Return 'images'/'text' for an unconsumed regen request, else None."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT regen_images_pending, regen_text_pending "
                "FROM pipeline_tasks WHERE task_id = $1::uuid", str(task_id))
        if row is None:
            return None
        if row["regen_images_pending"]:
            return "images"
        if row["regen_text_pending"]:
            return "text"
        return None
    except Exception as exc:  # noqa: BLE001 — missing columns / bad id → no regen
        logger.debug("[atoms.approval_gate] pending-regen check failed: %s", exc)
        return None

async def _consume_regen(pool: Any, task_id: str, component: str) -> None:
    col = "regen_images_pending" if component == "images" else "regen_text_pending"
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE pipeline_tasks SET {col} = false WHERE task_id = $1::uuid",
            str(task_id))
```

Wire the short-circuit in `run()` immediately after the `decision == "rejected"` block and BEFORE the `decision == "approved"` block (regen outranks a stale approval; reject still outranks regen):

```python
        if decision == "rejected":
            return {"_halt": True, "_halt_reason": f"gate {gate_name!r} rejected by operator"}
        # NEW: one-shot pending-regen short-circuit (consume + reroute, no page)
        pending = await _pending_regen(pool, str(task_id))
        if pending is not None:
            out = _regen_output(pending, state.get("gate_regen_targets") or {})
            if "_goto" in out:
                await _consume_regen(pool, str(task_id), pending)
                logger.info("[atoms.approval_gate:%s] regen_%s consumed → _goto %s",
                            gate_name, pending, out["_goto"])
            return out
        if decision == "approved":
            return {}
```

The `interrupt()` resume value (line ~240) stays passthrough — it no longer carries routing (the decision was consumed pre-pause). Leave that block returning `{}`.

- [ ] **Step 4: Run** the new test + the existing approval_gate interrupt tests (approve passthrough, reject halt, disabled passthrough, no-pool halt) → all PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(gate): approval_gate consumes pending regen → _goto (one-shot)"`

---

### Task 4: Durable regen counters + consume flags (schema) — DO FIRST

**Files:**

- Create: `src/cofounder_agent/services/migrations/<UTCstamp>_add_regen_counters.py` (generate via `python scripts/new-migration.py "add regen counters and pending flags to pipeline_tasks"`)
- Test: `src/cofounder_agent/tests/unit/services/migrations/test_regen_counter_columns.py` (string-level contract) + `python scripts/ci/migrations_smoke.py`.

**Interfaces:**

- Produces 4 columns on `pipeline_tasks`: `regen_images_attempts INTEGER NOT NULL DEFAULT 0`, `regen_images_pending BOOLEAN NOT NULL DEFAULT false`, `regen_text_attempts INTEGER NOT NULL DEFAULT 0`, `regen_text_pending BOOLEAN NOT NULL DEFAULT false`. `attempts` is monotonic (cap + observability); `pending` is the one-shot consume flag.

Ordered first because Tasks 3/5/6 read and write these columns.

- [ ] **Step 1: Failing test** — assert the migration file adds all four columns (string-level contract so it runs without a DB).

```python
# tests/unit/services/migrations/test_regen_counter_columns.py
from pathlib import Path
import glob
def test_regen_counter_migration_adds_all_columns():
    root = Path(__file__).resolve().parents[4] / "services" / "migrations"
    f = sorted(glob.glob(str(root / "*_add_regen_counters.py")))[-1]
    src = Path(f).read_text(encoding="utf-8")
    for col in ("regen_images_attempts", "regen_images_pending",
                "regen_text_attempts", "regen_text_pending"):
        assert col in src, col
    assert "ADD COLUMN IF NOT EXISTS" in src
```

- [ ] **Step 2: Run, watch fail** (file doesn't exist yet).

- [ ] **Step 3: Implement** the migration with four `ALTER TABLE pipeline_tasks ADD COLUMN IF NOT EXISTS ...` statements (two `INTEGER NOT NULL DEFAULT 0`, two `BOOLEAN NOT NULL DEFAULT false`), following the runner interface of a recent migration (e.g. `20260617_*_add_media_pipeline_redispatch_count.py`).

- [ ] **Step 4: Run** the unit test + `python scripts/ci/migrations_lint.py` → PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(db): regen counters + pending flags on pipeline_tasks"`

---

### Task 5: Bound regen by the attempt counters

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/approval_gate.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_approval_gate_atom.py`

**Interfaces:**

- Consumes: Task 3's decision mapping; Task 4's columns; `app_settings.regen_images_max_attempts`/`regen_text_max_attempts` via `site_config`.
- Produces: when a component's attempt count ≥ its cap, the regen decision is refused — the gate re-pauses (or `_halt`s with a clear reason) instead of looping.

- [ ] **Step 1: Failing test** — a `regen_images` decision with `regen_images_attempts >= regen_images_max_attempts` does NOT set `_goto` to the image block.

```python
@pytest.mark.asyncio
async def test_regen_images_refused_at_cap(monkeypatch):
    monkeypatch.setattr(approval_gate, "interrupt",
                        lambda payload: {"decision": "regen_images", "steering": None})
    state = {... "task_regen_images_attempts": 3,
             "site_config": _SiteConfigGateOn({"regen_images_max_attempts": "3"})}
    out = await approval_gate.run(state)
    assert out.get("_goto") != "plan_image_markers"  # capped → no loop
```

- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** the cap check in `_decision_to_output` (read the current attempt from state / a small DB read, compare to the `site_config.get_int(...)` cap, refuse on `>=`). Increment the counter when a regen IS taken (in `approval_service.regen_at_gate`, Task 6 — keep the atom read-only on the counter; the surface writes it).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(gate): bound preview_gate regen by attempt caps"`

---

### Task 6: `regen_at_gate` service function

**Files:**

- Modify: `src/cofounder_agent/services/approval_service.py` (mirror the existing approve/reject path — the `pipeline_gate_history` insert at ~452/694 and the resume dispatch).
- Test: `src/cofounder_agent/tests/unit/services/test_approval_service_regen.py`

**Interfaces:**

- Produces: `async def regen_at_gate(*, task_id: str, component: Literal["images","text"], steering: str | None, pool, site_config) -> dict` — writes a `pipeline_gate_history` row (`event_kind="regen_images"`/`"regen_text"`, `feedback=steering`), bumps the matching `pipeline_tasks.regen_*_attempts`, stamps `metadata.approved_at_retry_count` semantics for freshness, and resumes the graph with `Command(resume={"decision": ..., "steering": ...})`.

- [ ] **Step 1: Failing test** — `regen_at_gate(component="images", ...)` writes a `regen_images` gate-history row and increments the counter (assert against a fake pool capturing the SQL/params).
- [ ] **Step 2: Run, watch fail** (function undefined).
- [ ] **Step 3: Implement** by mirroring `reject_at_gate`/`approve` in the same file: same `pipeline_gate_history` insert columns `(task_id, gate_name, event_kind, feedback, actor, metadata)`, same resume-dispatch helper, plus the counter `UPDATE`. Unknown `component` → raise loudly.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(approval): regen_at_gate service fn"`

---

### Task 7: CLI `poindexter pipeline regen`

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/pipeline.py` (mirror the existing `pipeline resume`/`approve` subcommands).
- Test: `src/cofounder_agent/tests/unit/cli/test_pipeline_regen_cli.py`

**Interfaces:**

- Consumes: `regen_at_gate` (Task 6).
- Produces: `poindexter pipeline regen <task_id> --images|--text [--steering "..."]` — adapter only; delegates to `regen_at_gate`, no inline SQL.

- [ ] **Step 1: Failing test** — invoking the subcommand with `--images` calls `regen_at_gate(component="images", ...)` (patch the service, assert the call). Mutually-exclusive `--images/--text`; missing both → loud error.
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** the argparse wiring mirroring the sibling `resume` subcommand; `--images`/`--text` as a required mutually-exclusive group.
- [ ] **Step 4: Run** → PASS; `python scripts/ci/adapter_purity_lint.py` clean (no new inline SQL).
- [ ] **Step 5: Commit.** `git commit -m "feat(cli): poindexter pipeline regen --images/--text"`

---

### Task 8: MCP `regen_post` tool — DEFERRED (no HTTP resume surface to adapt)

**Status:** Deferred (2026-06-22), with rationale, per `feedback_cli_first`
(CLI primary, MCP/REST secondary).

**Why deferred, not built now.** The MCP `reject_post` tool delegates through the
**HTTP API** (`POST /api/tasks/{id}/reject`), not the service directly — MCP is a
thin HTTP adapter. But the interrupt()-gate **resume** has no HTTP route at all:
`runner.run(resume=True)` appears nowhere in `routes/` (verified). Resuming an
interrupt()-paused graph needs an in-process `TemplateRunner` + Postgres
checkpointer and runs for minutes (image regen → QA → SEO → re-pause) — the wrong
shape for a blocking FastAPI request handler. So `pipeline resume` is **CLI-only**
today, and `regen` follows it (Task 7, shipped). A `regen_post` MCP tool would
have to either (a) embed the multi-minute in-process resume in an HTTP handler, or
(b) be the first consumer of a brand-new async HTTP resume endpoint for ALL
interrupt() gates. Both are out of scope for the regen feature.

**Follow-up (separate work item):** an async HTTP resume surface for interrupt()
gates — `POST /api/pipeline/{task_id}/resume` and `.../regen` that enqueue the
resume (background task / Prefect) and return immediately — then MCP `resume_post`

- `regen_post` become trivial thin adapters over it. Until then the phone surface
  for preview_gate is: review on the existing `awaiting_approval` UI, act via the
  CLI. Filed as the natural next step; the core regen loop ships CLI-first.

---

### Task 9: Wire preview_gate into the graph_def + reseed

**Files:**

- Modify: `src/cofounder_agent/services/canonical_blog_spec.py:83-84,135-188`
- Create: `src/cofounder_agent/services/migrations/<UTCstamp>_reseed_canonical_blog_preview_gate.py`
- Test: `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py`

**Interfaces:**

- Consumes: Tasks 1-3 (multi-branch compiler + the gate atom).
- Produces: the gate node `{"id": "preview_gate", "atom": "atoms.approval_gate", "config": {"gate_name": "preview_gate", "regen_targets": {"images": "plan_image_markers", "text": "generate_draft"}}}` and the rerouted finalize edges.

- [ ] **Step 1: Failing test** — the spec compiles and `preview_gate` sits between `record_pipeline_version` and `evaluate_auto_publish`, with backward branch+loop edges to `plan_image_markers` and `generate_draft`.

```python
def test_preview_gate_in_graph_with_regen_edges():
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as G
    ids = {n["id"] for n in G["nodes"]}
    assert "preview_gate" in ids
    edges = G["edges"]
    assert {"from": "record_pipeline_version", "to": "preview_gate"} in edges
    assert any(e == {"from": "preview_gate", "to": "evaluate_auto_publish"} for e in edges)
    assert {"from": "preview_gate", "to": "plan_image_markers", "branch": True, "loop": True} in edges
    assert {"from": "preview_gate", "to": "generate_draft", "branch": True, "loop": True} in edges
    from services.pipeline_architect import build_graph_from_spec
    assert build_graph_from_spec(G) is not None
```

- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** the node insert + replace `{"from": "record_pipeline_version", "to": "evaluate_auto_publish"}` with the gate edges above. Then author the reseed migration mirroring `20260619_*_reseed_canonical_blog_graph_def_v6_director_review.py` (re-`INSERT ... ON CONFLICT` the active `pipeline_templates.graph_def` row from `CANONICAL_BLOG_GRAPH_DEF`).
- [ ] **Step 4: Run** the spec test + `python scripts/ci/migrations_smoke.py` → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(pipeline): wire preview_gate into canonical_blog + reseed"`

---

### Task 10: Seed settings defaults (develop behind false)

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: `pipeline_gate_preview_gate` (seed `"false"` for now — flipped in Task 12), `regen_images_max_attempts="3"`, `regen_text_max_attempts="2"`.

- [ ] **Step 1: Failing test** — assert the three keys are present in `DEFAULTS` with those values.
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** the three `DEFAULTS` entries.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(settings): preview_gate + regen-cap defaults (gate seeded off)"`

---

### Task 11: End-to-end verification (Docker required)

**Files:** none (operational).

**Interfaces:** Consumes everything above.

- [ ] **Step 1:** Bring the stack up; set `pipeline_gate_preview_gate=on` on the live DB (MCP `set_setting` / `poindexter settings set`).
- [ ] **Step 2:** Run one `canonical_blog` task to completion; confirm it pauses at `preview_gate` (status `awaiting_approval`, `pipeline_gate_history` pause row, Telegram page).
- [ ] **Step 3:** `poindexter pipeline regen <task> --images` → confirm the graph loops back through the image block ONLY (text byte-identical), re-QAs, and re-pauses at the gate. Capture the rendered image diff.
- [ ] **Step 4:** `poindexter pipeline regen <task> --text` → confirm the writer block re-runs AND images refresh (cascade).
- [ ] **Step 5:** `poindexter approve <task>` → confirm it proceeds to publish-stage.
- [ ] **Step 6:** Confirm the attempt caps refuse a 4th image regen / 3rd text regen.

---

### Task 12: Flip the default on

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `test_settings_defaults.py`

- [ ] **Step 1:** Only after Task 11 passes: change `pipeline_gate_preview_gate` default to `"true"`; update the test.
- [ ] **Step 2:** Run → PASS. Commit `feat(settings): enable preview_gate by default`.
- [ ] **Step 3:** Set the live prod value to `true` (it persists; the seed only covers fresh installs).

---

## Self-Review

**Spec coverage:** ✅ gate placement (T9), 4 actions (T3), text→image cascade (T9 edges + T3), CLI+MCP surface (T7/T8), steering (T6), bounding (T4/T5), enabled-by-default + verify-first rollout (T10→T11→T12), `pipeline_gate_history` decision carry (T6), stale-approval freshness (T6). The vision gate is correctly out of scope (separate task #58).

**Placeholder scan:** the CLI/MCP tasks (T7/T8) reference "mirror the existing `resume`/`reject_post`" rather than reproducing argparse/registry boilerplate verbatim — acceptable because the pattern file is named and the deliverable + test are concrete. All code-bearing steps for the novel logic (compiler router, decision mapping, graph edges) carry exact code.

**Type consistency:** decision vocabulary `approve|regen_images|regen_text|reject` is identical across T3 (atom), T6 (service `event_kind`), T7 (CLI), T8 (MCP). `regen_targets` keys `images|text` consistent T3↔T9. Counter columns `regen_images_attempts`/`regen_text_attempts` consistent T4↔T5↔T6.
