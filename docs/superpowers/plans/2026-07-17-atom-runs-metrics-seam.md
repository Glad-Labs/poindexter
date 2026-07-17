# atom_runs Metrics Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use **superpowers:executing-plans** to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project override:** this repo runs with subagent delegation disabled (billing policy — see CLAUDE.md). Do **NOT** dispatch subagents for any task in this plan, and do not use superpowers:subagent-driven-development. Execute inline in the current session, sequentially.

**Goal:** Make `StageResult.metrics` reach `atom_runs.metrics` on the graph_def path, reviving the writer prompt-size panels (poindexter#868) and the lab-observability fields dead since the #355 atom-cutover.

**Architecture:** A reserved `_atom_metrics` key that an atom may return. `_wrap_atom` pops it (before `output_keys` is computed, so digests stay byte-identical) and merges it into the `TemplateRunRecord` with structural keys winning. The stage shim lifts each stage's `StageResult.metrics` onto that key by passing a local throwaway sink to `make_stage_node` instead of `None`. `_atom_metrics` is deliberately **not** a declared `PipelineState` channel.

**Tech Stack:** Python 3.13, LangGraph, asyncpg/Postgres, pytest, Grafana (Postgres datasource).

## Global Constraints

- Spec: [`docs/superpowers/specs/2026-07-17-atom-runs-metrics-seam-design.md`](../specs/2026-07-17-atom-runs-metrics-seam-design.md). Read it first.
- Branch: **`claude/atom-runs-metrics-seam`** (already checked out, off fresh `origin/main`). Do NOT create another branch.
- PR: **glad-labs-stack#2649** already exists as a **draft**. Push commits to the same branch; do NOT open a second PR.
- Every commit message ends with a blank line then `Glad-Labs/poindexter#873`.
- **No** new DB table, migration, `app_settings` key, or Prometheus metric.
- `_atom_metrics` MUST NOT be added to `PipelineState` — a declared channel is checkpointer-durable and would smear one node's metrics onto its successors.
- `_atom_metrics` MUST be popped **before** `output_keys` is computed, so atoms that don't use it produce byte-identical rows to today.
- Structural keys (`input_keys` / `output_keys` / `input_digest` / `output_digest`) MUST override atom-supplied values of the same name.
- **Test runner** — this worktree has no venv of its own; `poetry run` would create a wasteful empty one. Always use the main checkout's interpreter and disable the repo's default addopts:
  ```
  cd src/cofounder_agent
  "C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest <paths> -o addopts="" -q
  ```
- **Verified regression baseline (before this plan's 9 new tests):** `79 passed` across
  `tests/unit/services/test_approval_interrupt.py`, `test_pipeline_architect_halt.py`,
  `test_pipeline_architect_output_preview.py`, `test_atom_registry_manifest_driven.py`,
  and `tests/unit/services/stages/test_generate_content.py`.

## File Structure

| File                                                                                   | Responsibility                                                                                  | Change                                      |
| -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------- |
| `src/cofounder_agent/services/pipeline_architect.py`                                   | Graph compiler + `_wrap_atom` node wrapper — the single call site for every graph_def node      | Pop + merge `_atom_metrics` (Task 1)        |
| `src/cofounder_agent/services/atom_registry.py`                                        | Atom catalog + `_make_stage_runner` shim that surfaces Stage plugins as `stage.*` virtual atoms | Capture the stage record's metrics (Task 2) |
| `src/cofounder_agent/modules/content/atoms/content_generate_draft.py`                  | The one hand-written stage-wrapping atom                                                        | Attach `_atom_metrics` (Task 3)             |
| `docs/architecture/rag-retrieval-stack.md`                                             | Documents the poindexter#868 writer prompt-size fields                                          | Add the filter-by-atom rule (Task 4)        |
| `src/cofounder_agent/tests/unit/services/test_pipeline_architect_atom_metrics.py`      | **new** — `_wrap_atom` merge/strip/precedence contract                                          | Create (Task 1)                             |
| `src/cofounder_agent/tests/unit/services/test_atom_registry_stage_metrics.py`          | **new** — shim lifts `StageResult.metrics`                                                      | Create (Task 2)                             |
| `src/cofounder_agent/tests/unit/services/atoms/test_content_generate_draft_metrics.py` | **new** — draft atom forwards its metrics                                                       | Create (Task 3)                             |

---

### Task 1: `_wrap_atom` merges `_atom_metrics` into the record

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py` (two edits inside `_wrap_atom`: the `out` normalisation ~line 1213, and the success record's `metrics=` dict ~line 1238)
- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect_atom_metrics.py` (**create**)

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: the `_atom_metrics` protocol that Tasks 2 and 3 rely on — an atom's `run(state)` may include the key `"_atom_metrics"` whose value is a `dict[str, Any]`. `_wrap_atom` pops it from the returned dict and merges it into `TemplateRunRecord.metrics`. Consumers must not expect it to survive into LangGraph state.

**Context you need:** `_wrap_atom` signature is `_wrap_atom(run_fn, atom_name, node_id, record_sink, *, node_config=None, on_event=None, index=None, total=None, retry_policy=None)`. The node it returns is called as `await node(state_dict, config)` — `config=None` is fine in tests (proven at `tests/unit/services/test_approval_interrupt.py:417`).

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/test_pipeline_architect_atom_metrics.py`:

```python
"""_wrap_atom must merge an atom's reserved ``_atom_metrics`` into its
TemplateRunRecord — the seam carrying StageResult.metrics to atom_runs.metrics.

Glad-Labs/poindexter#873: _wrap_atom hardcoded its metrics dict, so 6,243
atom_runs rows carried only IO digests and every StageResult.metrics field
(content_length / model_used / prompt_template_key / variant_id, plus the
poindexter#868 writer_prompt_* fields) was silently discarded.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.pipeline_architect import _wrap_atom

pytestmark = pytest.mark.unit


async def test_atom_metrics_merged_into_record() -> None:
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "body",
            "_atom_metrics": {"content_length": 42, "model_used": "m:1b"},
        }

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert len(sink) == 1
    assert sink[0].metrics["content_length"] == 42
    assert sink[0].metrics["model_used"] == "m:1b"


async def test_atom_metrics_stripped_from_returned_state() -> None:
    """_atom_metrics must never reach LangGraph. A declared channel would be
    checkpointer-durable and smear one node's metrics onto its successors —
    the phantom-2093 failure from poindexter#868 Task 3."""
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {"content": "body", "_atom_metrics": {"content_length": 42}}

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    out = await node({"task_id": "t"}, None)

    assert "_atom_metrics" not in out
    assert out["content"] == "body"


async def test_output_keys_exclude_atom_metrics() -> None:
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {"content": "body", "_atom_metrics": {"x": 1}}

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert sink[0].metrics["output_keys"] == ["content"]


async def test_record_unchanged_when_no_atom_metrics() -> None:
    """Atoms that don't opt in must produce byte-identical rows to pre-#873."""
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {"content": "body"}

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert set(sink[0].metrics) == {
        "input_keys", "output_keys", "input_digest", "output_digest",
    }


async def test_structural_keys_win_over_atom_supplied() -> None:
    """An atom must not be able to corrupt input_keys/digests by returning
    its own values under those names."""
    async def run_fn(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "body",
            "_atom_metrics": {"input_keys": ["BOGUS"], "input_digest": "bogus"},
        }

    sink: list = []
    node = _wrap_atom(run_fn, "atoms.x", "n1", sink)
    await node({"task_id": "t"}, None)

    assert sink[0].metrics["input_keys"] == ["task_id"]
    assert sink[0].metrics["input_digest"] != "bogus"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/test_pipeline_architect_atom_metrics.py -o addopts="" -q
```

Expected: **3 failed, 2 passed**. `test_atom_metrics_merged_into_record` fails with `KeyError: 'content_length'`; `test_atom_metrics_stripped_from_returned_state` fails on `assert "_atom_metrics" not in out`; `test_output_keys_exclude_atom_metrics` fails because `output_keys == ["_atom_metrics", "content"]`. The other two pass vacuously today — they are regression guards that must stay green after Step 3.

- [ ] **Step 3: Implement the pop**

In `src/cofounder_agent/services/pipeline_architect.py`, inside `_wrap_atom`'s `node`, find:

```python
            elapsed_ms = int((_time.time() - t0) * 1000)
            out = result if isinstance(result, dict) else {}
            halted = bool(out.get("_halt"))
```

Replace with:

```python
            elapsed_ms = int((_time.time() - t0) * 1000)
            out = result if isinstance(result, dict) else {}
            # Observability seam (poindexter#873) — an atom may return a
            # reserved ``_atom_metrics`` dict carrying its StageResult.metrics.
            # Pop it BEFORE output_keys is computed so atoms that don't use it
            # produce byte-identical rows, and atoms that do never leak the key
            # into output_keys / the digests / output_preview. Popping also
            # keeps it off LangGraph state: unlike a declared channel it can
            # never be checkpointed and smear onto successor nodes.
            _raw_metrics = out.pop("_atom_metrics", None)
            atom_metrics: dict[str, Any] = (
                dict(_raw_metrics) if isinstance(_raw_metrics, dict) else {}
            )
            halted = bool(out.get("_halt"))
```

- [ ] **Step 4: Implement the merge**

In the same function, find the success-path record (the FIRST of the two `TemplateRunRecord(` blocks, the one with `ok=True`):

```python
                        metrics={
                            "input_keys": input_keys,
                            "output_keys": output_keys,
                            "input_digest": digest_keys(input_keys),
                            "output_digest": digest_keys(output_keys),
                        },
```

Replace with:

```python
                        metrics={
                            **atom_metrics,
                            "input_keys": input_keys,
                            "output_keys": output_keys,
                            "input_digest": digest_keys(input_keys),
                            "output_digest": digest_keys(output_keys),
                        },
```

Structural keys are spread last so the wrapper always wins. Leave the second (`ok=False`, exception-path) `TemplateRunRecord` untouched — a raising atom produced no `out`.

- [ ] **Step 5: Run the new tests**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/test_pipeline_architect_atom_metrics.py -o addopts="" -q
```

Expected: `5 passed`.

- [ ] **Step 6: Run the regression suites**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/test_approval_interrupt.py tests/unit/services/test_pipeline_architect_halt.py tests/unit/services/test_pipeline_architect_output_preview.py -o addopts="" -q
```

Expected: all pass, no regressions. `test_approval_interrupt.py` contains the existing `_wrap_atom` tests — if any fail, the pop broke the halt/GraphInterrupt paths; fix before continuing.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_pipeline_architect_atom_metrics.py
git commit -m "fix(obs): merge atom-supplied _atom_metrics into the atom_runs record

Glad-Labs/poindexter#873"
```

---

### Task 2: The stage shim lifts `StageResult.metrics` onto `_atom_metrics`

**Files:**

- Modify: `src/cofounder_agent/services/atom_registry.py` (`_make_stage_runner`'s inner `runner`, ~lines 303-313)
- Test: `src/cofounder_agent/tests/unit/services/test_atom_registry_stage_metrics.py` (**create**)

**Interfaces:**

- Consumes: Task 1's `_atom_metrics` protocol (a `dict[str, Any]` under key `"_atom_metrics"` in the atom's returned dict).
- Produces: every `stage.*` virtual atom now emits its `StageResult.metrics`. No later task depends on this.

**Context you need:** `make_stage_node(stage, pool, *, record_sink: list[TemplateRunRecord] | None = None, on_event=None)`. It is unit-testable with `pool=None` provided `plugins.config.PluginConfig.load` is stubbed (proven at `tests/unit/services/test_approval_interrupt.py:435-453`). It emits its record **before** the halt check, so even a halting stage yields metrics.

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/test_atom_registry_stage_metrics.py`:

```python
"""_make_stage_runner must lift the stage's StageResult.metrics onto the
reserved ``_atom_metrics`` key so _wrap_atom can record them.

Glad-Labs/poindexter#873: the shim passed ``record_sink=None`` to
make_stage_node, whose metrics-carrying record is guarded by
``if record_sink is not None`` — so every stage.* node's metrics were computed
and then thrown away.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.stage import StageResult
from services.atom_registry import _make_stage_runner

pytestmark = pytest.mark.unit


class _Stage:
    name = "demo_stage"
    timeout_seconds = 5
    halts_on_failure = True

    def __init__(self, metrics: dict[str, Any]) -> None:
        self._metrics = metrics

    async def execute(self, context: Any, config: Any) -> StageResult:
        return StageResult(
            ok=True,
            detail="done",
            context_updates={"content": "body"},
            metrics=self._metrics,
        )


def _stage_node_patches() -> tuple:
    """PluginConfig.load runs before execute(); the DB-touching helpers must be
    stubbed so the node runs with pool=None."""
    enabled_cfg = SimpleNamespace(enabled=True, config={}, get=lambda k, d=None: d)
    return (
        patch("plugins.config.PluginConfig.load", AsyncMock(return_value=enabled_cfg)),
        patch("services.template_runner._mark_stage_column", AsyncMock()),
        patch("services.template_runner._emit_progress", AsyncMock()),
    )


async def test_stage_metrics_lifted_onto_atom_metrics() -> None:
    runner = _make_stage_runner(
        _Stage({"content_length": 7, "model_used": "m:1b"}), fallback_pool=None,
    )
    p1, p2, p3 = _stage_node_patches()
    with p1, p2, p3:
        out = await runner({"task_id": "t"})

    assert out["_atom_metrics"] == {"content_length": 7, "model_used": "m:1b"}
    assert out["content"] == "body"


async def test_stage_without_metrics_attaches_no_key() -> None:
    """An empty metrics dict must not add the key — keeps non-emitting stages
    byte-identical to pre-#873."""
    runner = _make_stage_runner(_Stage({}), fallback_pool=None)
    p1, p2, p3 = _stage_node_patches()
    with p1, p2, p3:
        out = await runner({"task_id": "t"})

    assert "_atom_metrics" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/test_atom_registry_stage_metrics.py -o addopts="" -q
```

Expected: **1 failed, 1 passed**. `test_stage_metrics_lifted_onto_atom_metrics` fails with `KeyError: '_atom_metrics'`. The second passes vacuously today and is a regression guard.

- [ ] **Step 3: Implement**

In `src/cofounder_agent/services/atom_registry.py`, inside `_make_stage_runner`, find:

```python
    async def runner(state: dict[str, Any]) -> dict[str, Any]:
        from services.template_runner import make_stage_node

        db = state.get("database_service")
        pool = getattr(db, "pool", None) if db else None
        if pool is None:
            pool = fallback_pool
        node = make_stage_node(stage, pool, record_sink=None)
        return await node(state)  # type: ignore[arg-type]
```

Replace with:

```python
    async def runner(state: dict[str, Any]) -> dict[str, Any]:
        from services.template_runner import make_stage_node

        db = state.get("database_service")
        pool = getattr(db, "pool", None) if db else None
        if pool is None:
            pool = fallback_pool
        # Local throwaway sink (poindexter#873). make_stage_node already builds
        # a record carrying the stage's StageResult.metrics, but only when a
        # sink is passed — the old record_sink=None discarded it, which is why
        # atom_runs.metrics never carried content_length / model_used /
        # prompt_template_key. This sink is local: it never reaches the graph's
        # real record_sink, so no duplicate atom_runs row is written (_wrap_atom
        # stays the only writer). A plain list has no append_and_notify, so
        # _emit_record just appends — no DB write. We only lift the metrics onto
        # the reserved _atom_metrics key, which _wrap_atom pops and merges.
        sink: list = []
        node = make_stage_node(stage, pool, record_sink=sink)
        out = await node(state)  # type: ignore[arg-type]
        if sink and isinstance(out, dict):
            stage_metrics = getattr(sink[0], "metrics", None)
            if stage_metrics:
                out["_atom_metrics"] = dict(stage_metrics)
        return out
```

- [ ] **Step 4: Run the new tests**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/test_atom_registry_stage_metrics.py -o addopts="" -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run the regression suites**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/test_atom_registry_manifest_driven.py tests/unit/services/test_approval_interrupt.py -o addopts="" -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/atom_registry.py src/cofounder_agent/tests/unit/services/test_atom_registry_stage_metrics.py
git commit -m "fix(obs): stage shim lifts StageResult.metrics onto _atom_metrics

Glad-Labs/poindexter#873"
```

---

### Task 3: `content.generate_draft` forwards its metrics

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/content_generate_draft.py` (the `run()` return, lines 71-82)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_content_generate_draft_metrics.py` (**create**)

**Interfaces:**

- Consumes: Task 1's `_atom_metrics` protocol.
- Produces: the writer's `StageResult.metrics` — `content_length`, `model_used`, `prompt_template_key`, `prompt_template_version`, `niche_slug`, `variant_id`/`experiment_*`, and the eight poindexter#868 `writer_prompt_*` fields — now reach `atom_runs.metrics` for `atom = 'content.generate_draft'`, which is what the five "Writer Context Size" panels query.

**Context you need:** `run(state)` currently builds its return via chained `|` dict merges and never reads `result.metrics`. `StageResult` is a dataclass: `StageResult(ok, detail, context_updates={}, continue_workflow=True, metrics={})`.

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/atoms/test_content_generate_draft_metrics.py`:

```python
"""content.generate_draft must hand its StageResult.metrics to _wrap_atom via
the reserved ``_atom_metrics`` key.

Glad-Labs/poindexter#873: the atom read only ``result.context_updates``, so the
writer's metrics — including the poindexter#868 writer_prompt_* prompt-size
fields — never reached atom_runs.metrics, and their five Grafana panels would
have read "No data" forever.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.stage import StageResult

pytestmark = pytest.mark.unit


def _stage_result(metrics: dict[str, Any]) -> StageResult:
    return StageResult(
        ok=True,
        detail="ok",
        context_updates={
            "content": "body",
            "research_context": "",
            "model_used": "m:1b",
            "models_used_by_phase": {},
            "generate_metrics": {},
        },
        metrics=metrics,
    )


async def test_stage_metrics_exposed_as_atom_metrics() -> None:
    from modules.content.atoms import content_generate_draft

    metrics = {
        "content_length": 4,
        "model_used": "m:1b",
        "prompt_template_key": "writer/blog",
        "writer_prompt_draft_chars": 5000,
    }
    with patch(
        "modules.content.writer_core.GenerateContentStage.execute",
        new=AsyncMock(return_value=_stage_result(metrics)),
    ):
        out = await content_generate_draft.run({"task_id": "t"})

    assert out["_atom_metrics"] == metrics
    assert out["content"] == "body"


async def test_no_metrics_attaches_no_key() -> None:
    from modules.content.atoms import content_generate_draft

    with patch(
        "modules.content.writer_core.GenerateContentStage.execute",
        new=AsyncMock(return_value=_stage_result({})),
    ):
        out = await content_generate_draft.run({"task_id": "t"})

    assert "_atom_metrics" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_content_generate_draft_metrics.py -o addopts="" -q
```

Expected: **1 failed, 1 passed**. `test_stage_metrics_exposed_as_atom_metrics` fails with `KeyError: '_atom_metrics'`.

- [ ] **Step 3: Implement**

In `src/cofounder_agent/modules/content/atoms/content_generate_draft.py`, find:

```python
    updates = result.context_updates or {}
    return {k: updates[k] for k in (
        "content", "research_context", "model_used",
        "models_used_by_phase", "generate_metrics",
    ) if k in updates} | (
        {"niche_slug": updates["niche_slug"]} if "niche_slug" in updates else {}
    ) | (
        {"model_selection_log": updates["model_selection_log"]}
        if "model_selection_log" in updates else {}
    ) | (
        {"stages": updates["stages"]} if "stages" in updates else {}
    )
```

Replace with:

```python
    updates = result.context_updates or {}
    out = {k: updates[k] for k in (
        "content", "research_context", "model_used",
        "models_used_by_phase", "generate_metrics",
    ) if k in updates} | (
        {"niche_slug": updates["niche_slug"]} if "niche_slug" in updates else {}
    ) | (
        {"model_selection_log": updates["model_selection_log"]}
        if "model_selection_log" in updates else {}
    ) | (
        {"stages": updates["stages"]} if "stages" in updates else {}
    )
    # Observability seam (poindexter#873): hand StageResult.metrics to
    # _wrap_atom via the reserved key so content_length / model_used /
    # prompt_template_key / variant_id and the writer_prompt_* prompt-size
    # fields (poindexter#868) reach atom_runs.metrics. _wrap_atom pops it
    # before LangGraph sees the state, so it never becomes durable state.
    if result.metrics:
        out["_atom_metrics"] = dict(result.metrics)
    return out
```

- [ ] **Step 4: Run the new tests**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/atoms/test_content_generate_draft_metrics.py -o addopts="" -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run the regression suite**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/stages/test_generate_content.py tests/unit/services/atoms/test_writer_atom_variant_hook.py -o addopts="" -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/content_generate_draft.py src/cofounder_agent/tests/unit/services/atoms/test_content_generate_draft_metrics.py
git commit -m "fix(obs): content.generate_draft forwards StageResult.metrics

Glad-Labs/poindexter#873"
```

---

### Task 4: Docs, full verification, deploy, and live end-to-end proof

**Files:**

- Modify: `docs/architecture/rag-retrieval-stack.md` (the `## Writer prompt-size observability (poindexter#868)` section, near the end of the file)

**Interfaces:**

- Consumes: Tasks 1-3.
- Produces: a merged, deployed, and **empirically verified** fix.

- [ ] **Step 1: Add the filter-by-atom rule to the docs**

In `docs/architecture/rag-retrieval-stack.md`, find this line at the end of the poindexter#868 section:

```markdown
Visible on the **Pipeline** dashboard's "Writer Context Size" row. See
[the design doc](../superpowers/specs/2026-07-16-writer-prompt-size-metrics-design.md)
for the full rationale and the forks considered.
```

Replace with:

```markdown
Visible on the **Pipeline** dashboard's "Writer Context Size" row. See
[the design doc](../superpowers/specs/2026-07-16-writer-prompt-size-metrics-design.md)
for the full rationale and the forks considered.

> **Always filter `atom_runs.metrics` queries by `atom`.** Generic keys
> (`content_length`, `model_used`) are emitted by many nodes, so a query without
> an `atom = '<name>'` predicate blends unrelated nodes into a meaningless
> average. The `writer_prompt_*` fields come from `content.generate_draft`
> alone — one row per task.
>
> These fields only reach `atom_runs.metrics` as of
> [poindexter#873](https://github.com/Glad-Labs/poindexter/issues/873), which
> fixed the two points where `StageResult.metrics` was discarded on the
> graph_def path. Before that fix the panels read "No data".
```

- [ ] **Step 2: Commit the doc**

```bash
git add docs/architecture/rag-retrieval-stack.md
git commit -m "docs(rag): note the atom filter rule + the #873 seam dependency

Glad-Labs/poindexter#873"
```

- [ ] **Step 3: Run every touched + regression suite together**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m pytest tests/unit/services/test_pipeline_architect_atom_metrics.py tests/unit/services/test_atom_registry_stage_metrics.py tests/unit/services/atoms/test_content_generate_draft_metrics.py tests/unit/services/test_approval_interrupt.py tests/unit/services/test_pipeline_architect_halt.py tests/unit/services/test_pipeline_architect_output_preview.py tests/unit/services/test_atom_registry_manifest_driven.py tests/unit/services/stages/test_generate_content.py -o addopts="" -q
```

Verified regression baseline (before this plan): `79 passed`.
Expected now: **`88 passed`** (79 + 9 new: 5 from Task 1, 2 from Task 2, 2 from Task 3). 0 failures.

- [ ] **Step 4: Lint + type-check the touched files**

Run:

```
cd src/cofounder_agent
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m ruff check services/pipeline_architect.py services/atom_registry.py modules/content/atoms/content_generate_draft.py
"C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m mypy services/pipeline_architect.py services/atom_registry.py modules/content/atoms/content_generate_draft.py --explicit-package-bases
```

Expected: ruff `All checks passed!`. For mypy, these files carry pre-existing errors; confirm **no NEW** ones. If unsure, capture a baseline by running mypy against the pre-branch versions:

```bash
git stash && "C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe" -m mypy services/pipeline_architect.py services/atom_registry.py modules/content/atoms/content_generate_draft.py --explicit-package-bases; git stash pop
```

- [ ] **Step 5: Push and mark the PR ready**

```bash
git push origin claude/atom-runs-metrics-seam
gh pr ready 2649 --repo Glad-Labs/glad-labs-stack
gh pr checks 2649 --repo Glad-Labs/glad-labs-stack --watch --interval 30
```

Wait for green. Per this repo's "CI passing is the gate" convention, merge without asking once green:

```bash
gh pr merge 2649 --repo Glad-Labs/glad-labs-stack --squash --delete-branch
gh pr view 2649 --repo Glad-Labs/glad-labs-stack --json state,mergedAt
```

`gh pr merge` can exit non-zero in a worktree even on success — verify with `gh pr view`, not the exit code.

- [ ] **Step 6: Deploy**

Merging does NOT make worker code live — the running Python process caches modules. The deploy checkout auto-syncs from `origin/main` roughly every 10 minutes; confirm it picked up the merge, then restart the container that runs the pipeline:

```bash
DEPLOY=C:/Users/mattm/.poindexter/deploy/glad-labs-stack
git -C "$DEPLOY" fetch origin main --quiet
git -C "$DEPLOY" merge --ff-only origin/main
docker restart poindexter-prefect-worker
```

`poindexter-prefect-worker` is the content-pipeline dispatcher — the one that runs atoms. (`poindexter-worker` hosts the API + scheduler and is not needed for this change.)

- [ ] **Step 7: Prove it end-to-end on live data**

**This is the step whose absence let poindexter#868 ship broken.** Unit tests mock the seam and `grafana_panels_lint` only `EXPLAIN`s SQL — neither can detect a permanently-absent JSONB key. Only real data proves it.

A canonical_blog task must run first (the daily batch is ~06:00 UTC; `content.generate_draft` runs early in the graph). Once one has, run:

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c "
SELECT created_at,
       (metrics ? 'writer_prompt_draft_chars') AS has_writer_metrics,
       (metrics ? 'content_length')            AS has_content_length,
       (metrics ? 'prompt_template_key')       AS has_prompt_key,
       model
  FROM atom_runs
 WHERE atom = 'content.generate_draft'
 ORDER BY created_at DESC LIMIT 3;
"
```

Expected on the newest row (created after the Step 6 restart): `has_writer_metrics = t`, `has_content_length = t`, `has_prompt_key = t`, and `model` populated rather than NULL.

Also confirm the `stage.*` sweep landed:

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c "
SELECT atom, count(*) FILTER (WHERE metrics ? 'content_length') AS with_metrics, count(*) AS rows
  FROM atom_runs
 WHERE created_at > NOW() - INTERVAL '2 hours'
 GROUP BY atom ORDER BY atom;
"
```

Expected: `stage.*` rows created after the restart show `with_metrics > 0`.

If either query shows all-`f` on post-restart rows, the seam is still broken — do NOT claim success. Diagnose from the actual data (check the deploy checkout really has the merge commit, and that the container restarted after it).

- [ ] **Step 8: Report**

Report: the merged PR URL, the live-data verification result (quote the actual query output), and whether the five "Writer Context Size" panels now have data. Be explicit if the pipeline hasn't run a canonical_blog task yet — in that case the fix is deployed but unproven, and say exactly that rather than implying success.
