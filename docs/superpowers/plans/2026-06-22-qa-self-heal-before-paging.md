# QA Gate: Self-Heal Before Paging (No Auto-Discard) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the QA gate auto-discarding written drafts: on a veto, regenerate (bounded) then flag-and-continue to `awaiting_approval` with findings attached; `rejected_final` becomes operator-only.

**Architecture:** All new terminal behavior in `qa.aggregate` is gated behind a master switch `qa_flag_instead_of_reject` (default `false` = today's discard behavior, byte-identical). When on, the terminal not-approved path skips `persist_qa_reject`/`_halt`/`status=rejected` and instead stamps `qa_flagged` + emits `qa_reviews`, riding the **existing** forward edge (`qa_aggregate → seo_all_metadata → … → persist_task`) to `awaiting_approval`. No graph-topology change. Three independent supporting changes (faithfulness→advisory, validator allowlist, surface findings) land alongside.

**Tech Stack:** Python 3.13, LangGraph graph_def pipeline, asyncpg, pytest, Click CLI, app_settings (DB config).

## Global Constraints

- **Branch:** `feat/qa-self-heal-gate` off `origin/main` (already created).
- **TDD:** failing test first, watch it fail, minimal code, watch it pass, commit. No production code without a failing test first.
- **Ship inert:** `qa_flag_instead_of_reject` default `'false'`. Switch-OFF behavior must stay byte-identical to today (regression-tested). Flip to `'true'` only after Docker e2e (Task 10).
- **`app_settings.value` is NOT NULL** — defaults are non-empty strings (`'false'`, `'true'`); `''` is the unset sentinel.
- **New settings defaults go in `services/settings_defaults.py`**, never in migration files. Schema/data DDL goes in `services/migrations/YYYYMMDD_HHMMSS_<slug>.py` (generate with `python scripts/new-migration.py "<desc>"`).
- **Adapter purity:** `routes/`, `poindexter/cli/`, `mcp-server/` hold no inline SQL — they delegate to service functions (`services/tasks_mcp.py` etc.).
- **No silent defaults / fail loud** for required config; best-effort telemetry writes never break the pipeline (match existing `# noqa: BLE001` patterns).
- **Commit message trailer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Run backend tests from** `src/cofounder_agent`: `poetry run pytest <path> -q`.

## Already-true (do NOT redo)

- `qa_rewrite_max_attempts` is already `'2'` in `settings_defaults.py:438`. No bump needed.
- The graph already has the `qa_rewrite` node + `qa_aggregate→qa_rewrite` (branch) + `qa_rewrite→qa_programmatic` (loop) edges, and the `qa_aggregate→seo_all_metadata` default forward edge. No `graph_def` reseed.
- `content.compile_meta` already formats `qa_feedback_formatted` from `qa_reviews` (via `format_qa_feedback_from_reviews`), and `content.persist_task` writes it to BOTH `pipeline_tasks.qa_feedback` and `pipeline_versions.qa_feedback`, and `pipeline_tasks_view` exposes `pv.qa_feedback` + `task_metadata`. So a flagged post that rides the forward path persists + surfaces its findings with no new persistence code.
- The operator-tunable `hallucination_whitelist_additions` (CSV) + static `_HALLUCINATION_WHITELIST_BASE` already exist — C3 extends the base set, no new setting.

---

## File Structure

| File                                                                                               | Responsibility                                                                          | Task |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ---- |
| `modules/content/atoms/_qa_rail_common.py`                                                         | `is_rescuable_reject` gains a `broaden` param widening regen-eligibility                | 1    |
| `services/template_runner.py`                                                                      | `PipelineState` gains `qa_flagged: bool` last-value channel                             | 2    |
| `modules/content/atoms/qa_aggregate.py`                                                            | Flag-and-continue terminal behind the switch; emit `qa_flagged` + `qa_flagged_surfaced` | 2    |
| `services/settings_defaults.py`                                                                    | Seed `qa_flag_instead_of_reject='false'` + metadata                                     | 2    |
| `modules/content/auto_publish_gate.py`                                                             | `evaluate(qa_flagged=...)` → `would_fire=False` when flagged                            | 3    |
| `modules/content/atoms/content_evaluate_auto_publish.py`                                           | Pass `qa_flagged` from state into `evaluate`                                            | 3    |
| `modules/content/task_metadata.py`                                                                 | `build_task_metadata` includes `qa_flagged` (durable, surfaced via view)                | 4    |
| `services/tasks_mcp.py`                                                                            | `list_tasks` selects `qa_flagged`+`qa_feedback`; new `get_task_qa_feedback` detail read | 5    |
| `poindexter/cli/pipeline.py`                                                                       | `pipeline list` flag marker; `pipeline qa <task>` findings view                         | 6    |
| `mcp-server/server.py`                                                                             | `list_tasks` tool mirrors the flag marker                                               | 6    |
| `services/migrations/<ts>_demote_deepeval_faithfulness_to_advisory.py` + `0000_baseline.seeds.sql` | Faithfulness → advisory (prod mutation + fresh-DB seed)                                 | 7    |
| `modules/content/content_validator.py`                                                             | `_HALLUCINATION_WHITELIST_BASE` += internal pipeline terms                              | 8    |
| `infrastructure/grafana/...` + `docs/architecture/anti-hallucination.md` + memory                  | Flag-rate panel + docs                                                                  | 9    |

---

### Task 1: Broaden `is_rescuable_reject` (regen eligibility)

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_qa_rail_common.py:84-122`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py`

**Interfaces:**

- Produces: `is_rescuable_reject(reviews, vetoed_by, *, final_score, threshold, broaden=False) -> bool`. When `broaden=False`, behavior is **identical to today** (critic-only + below-threshold). When `broaden=True`, also returns `True` for `programmatic_validator` / gate-provider vetoes a text revise can plausibly fix, but **still** `False` for `missing_required:*` (infra), `vision_gate`, and `url_verifier` (a text revise can't fix an image or a dead link).

- [ ] **Step 1: Write failing tests** — append to `test_qa_rail_common.py`:

```python
from modules.content.atoms._qa_rail_common import is_rescuable_reject

def _rev(reviewer, provider, approved):
    return {"reviewer": reviewer, "provider": provider, "approved": approved, "score": 50.0}

def test_broaden_false_keeps_programmatic_non_rescuable():
    reviews = [_rev("programmatic_validator", "programmatic", False)]
    assert is_rescuable_reject(reviews, ["programmatic_validator"], final_score=50, threshold=70) is False

def test_broaden_true_makes_programmatic_rescuable():
    reviews = [_rev("programmatic_validator", "programmatic", False)]
    assert is_rescuable_reject(reviews, ["programmatic_validator"], final_score=50, threshold=70, broaden=True) is True

def test_broaden_true_brand_rescuable():
    reviews = [_rev("deepeval_brand_fabrication", "deepeval", False)]
    assert is_rescuable_reject(reviews, ["deepeval_brand_fabrication"], final_score=50, threshold=70, broaden=True) is True

def test_broaden_true_missing_required_not_rescuable():
    assert is_rescuable_reject([], ["missing_required:llm_critic"], final_score=50, threshold=70, broaden=True) is False

def test_broaden_true_vision_gate_not_rescuable():
    reviews = [_rev("vision_gate", "vision_gate", False)]
    assert is_rescuable_reject(reviews, ["vision_gate"], final_score=50, threshold=70, broaden=True) is False

def test_broaden_true_below_threshold_still_rescuable():
    assert is_rescuable_reject([], [], final_score=60, threshold=70, broaden=True) is True
```

- [ ] **Step 2: Run, verify fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_qa_rail_common.py -q`
Expected: FAIL (`is_rescuable_reject() got an unexpected keyword argument 'broaden'`).

- [ ] **Step 3: Implement** — replace the `is_rescuable_reject` signature + body in `_qa_rail_common.py`. Add the module constant near the provider buckets:

```python
# Providers whose veto a text revision can NEVER fix (image / dead link).
_NON_TEXT_FIXABLE_PROVIDERS = ("vision_gate", "url_verifier")
```

New signature + the broaden branch (keep the existing `broaden=False` logic exactly as-is for the default path):

```python
def is_rescuable_reject(
    reviews: list[dict[str, Any]],
    vetoed_by: list[Any],
    *,
    final_score: float,
    threshold: float,
    broaden: bool = False,
) -> bool:
    """Decide whether a qa.aggregate REJECT is eligible for one rewrite pass.

    ``broaden=False`` (default, switch-off) keeps the original critic-only +
    below-threshold rule. ``broaden=True`` (self-heal switch on) also routes
    programmatic/brand/factcheck/consistency vetoes — anything a text revise
    could plausibly clear — to qa.rewrite, while STILL refusing infra vetoes
    (missing_required:*) and non-text-fixable gates (vision/url).
    """
    # (b) Score-threshold reject: nothing vetoed, just below the bar.
    if not vetoed_by:
        return final_score < threshold

    by_name = {r.get("reviewer"): r for r in reviews}
    for name in vetoed_by:
        if isinstance(name, str) and name.startswith("missing_required:"):
            return False  # vacuous-pass guard veto — infra, not content
        review = by_name.get(name)
        if review is None:
            return False  # unknown veto source — fail safe, don't rescue
        provider = review.get("provider")
        if broaden:
            if provider in _NON_TEXT_FIXABLE_PROVIDERS:
                return False  # a text revise can't fix a bad image / dead link
            continue  # every other veto is regen-eligible under self-heal
        if provider not in _CRITIC_PROVIDERS:
            return False  # switch-off: programmatic / gate veto — hard correctness
    return True
```

- [ ] **Step 4: Run, verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_qa_rail_common.py -q`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_qa_rail_common.py src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py
git commit -m "feat(qa): broaden is_rescuable_reject behind a broaden flag (no behavior change default)"
```

---

### Task 2: `qa.aggregate` flag-and-continue + `qa_flagged` channel + setting

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` (PipelineState, ~line 577 near `_goto`)
- Modify: `src/cofounder_agent/modules/content/atoms/qa_aggregate.py:176-306`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (~line 438 block + ~1260 metadata block)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py`

**Interfaces:**

- Consumes: `is_rescuable_reject(..., broaden=...)` (Task 1).
- Produces: on switch-ON terminal not-approved, `qa.aggregate` returns a dict with `qa_flagged=True`, `qa_reviews`, `quality_score`, `qa_final_verdict`, `_goto=""`, **no** `_halt`, **no** `status`. On switch-OFF, unchanged (`_halt=True`, `status="rejected"`, `persist_qa_reject`).

- [ ] **Step 1: Add the PipelineState channel** — in `template_runner.py`, directly after the `_goto: str` declaration (~line 577):

```python
    # qa_flagged (self-heal-before-paging): qa.aggregate sets this True when a
    # non-approvable draft is flag-and-continued (switch qa_flag_instead_of_reject)
    # instead of discarded. Read by content.evaluate_auto_publish (never auto-publish
    # a flagged post) and persisted into task_metadata for the operator surface.
    # Last-value channel so it survives the graph_def adapter's state merge.
    qa_flagged: bool
```

- [ ] **Step 2: Write failing tests** — add to `test_qa_aggregate_atom.py`. Use the file's existing harness for building `state` with a `platform` whose `.config.get` is controllable. Two new tests:

```python
async def test_flag_and_continue_when_switch_on(make_state):
    # a below-threshold reject with switch on → flag, no halt, no rejected
    state = make_state(reviews=[{"reviewer": "ollama_critic", "provider": "ollama",
                                 "approved": False, "score": 60.0, "feedback": "weak"}],
                       config={"qa_flag_instead_of_reject": "true",
                               "qa_rewrite_max_attempts": "0"})  # 0 → no rescue, straight to terminal
    out = await qa_aggregate.run(state)
    assert out.get("qa_flagged") is True
    assert "_halt" not in out
    assert out.get("status") != "rejected"
    assert out.get("_goto") == ""
    assert out.get("qa_reviews")  # surfaced downstream

async def test_switch_off_still_discards(make_state):
    state = make_state(reviews=[{"reviewer": "ollama_critic", "provider": "ollama",
                                 "approved": False, "score": 60.0, "feedback": "weak"}],
                       config={"qa_flag_instead_of_reject": "false",
                               "qa_rewrite_max_attempts": "0"})
    out = await qa_aggregate.run(state)
    assert out.get("_halt") is True
    assert out.get("status") == "rejected"
    assert out.get("qa_flagged") is not True
```

(If the existing test file lacks a `make_state` fixture with a configurable `platform.config`, add a small local fixture mirroring how other tests in that file construct `state` — reuse the file's existing `platform` double.)

- [ ] **Step 3: Run, verify fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_qa_aggregate_atom.py -q`
Expected: FAIL (`qa_flagged` not set; switch ignored).

- [ ] **Step 4: Implement** in `qa_aggregate.py`:

(a) Read the switch once, near the top of the rescue-dispatch block (after `max_attempts = _max_attempts(config)`):

```python
    flag_instead = str(
        (config.get("qa_flag_instead_of_reject", "false") if config else "false")
    ).strip().lower() in ("true", "1", "yes", "on")
```

(b) Pass `broaden=flag_instead` into the `is_rescuable_reject(...)` call in the rescue-dispatch `if`.

(c) Replace the terminal `if not approved:` persistence block (lines ~274-306) so it branches on `flag_instead`:

```python
    if not approved:
        if flag_instead:
            # Self-heal before paging: do NOT discard. Flag and ride the forward
            # edge to awaiting_approval; compile_meta/persist_task carry the
            # findings; evaluate_auto_publish refuses to auto-publish a flag.
            out["qa_flagged"] = True
            _platform = state.get("platform")
            if _platform is not None:
                try:
                    _platform.audit.write_bg(
                        "qa_flagged_surfaced",
                        source="qa.aggregate",
                        details={
                            "final_score": round(float(kept_score), 2),
                            "threshold": float(threshold),
                            "vetoed_by": list(result.get("vetoed_by", [])),
                            "attempts": attempts,
                        },
                        task_id=(str(state.get("task_id")) or None) if state.get("task_id") else None,
                        severity="warning",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[qa.aggregate] qa_flagged_surfaced audit skipped: %s", exc)
        else:
            from modules.content.atoms._qa_persist import (
                build_qa_feedback,
                build_reject_reason,
                persist_qa_reject,
            )
            reason = build_reject_reason(reviews, result["vetoed_by"], kept_score)
            qa_feedback = build_qa_feedback(reviews, kept_score, approved=False)
            if kept_best_applied:
                qa_feedback = (
                    f"[keep-best] Retained an earlier draft (score {kept_score:.0f}) "
                    f"that outscored the final rescue revision (score "
                    f"{float(final_score):.0f}); the per-rail breakdown below is from "
                    f"the final revision.\n" + qa_feedback
                )
            await persist_qa_reject(
                state.get("database_service"),
                task_id=str(state.get("task_id") or ""),
                reason=reason,
                final_score=kept_score,
                content=kept_content,
                title=str(state.get("title") or state.get("topic") or ""),
                qa_feedback=qa_feedback,
                models_used_by_phase=state.get("models_used_by_phase") or {},
            )
            out["_halt"] = True
            out["_halt_reason"] = f"qa.aggregate: reject (score={kept_score}, {reason[:120]})"
            out["status"] = "rejected"
```

Note: the `out` dict (lines ~251-268) already carries `qa_reviews=list(reviews)`, `quality_score=promoted`, `_goto=""`, `qa_final_verdict`. On the flag path those flow forward unchanged — `compile_meta` reads `qa_reviews` and formats `qa_feedback_formatted`.

- [ ] **Step 5: Seed the setting** — in `settings_defaults.py`, add to the QA block near line 438:

```python
    'qa_flag_instead_of_reject': 'false',
```

and to the metadata block near line 1260:

```python
    'qa_flag_instead_of_reject': {'owner': 'qa_aggregate', 'value_type': 'boolean'},
```

- [ ] **Step 6: Run, verify pass** (+ regression for existing qa_aggregate tests)

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_qa_aggregate_atom.py -q`
Expected: PASS (new + all pre-existing reject tests still green — they run with the default switch-off).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/modules/content/atoms/qa_aggregate.py src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py
git commit -m "feat(qa): flag-and-continue instead of discard behind qa_flag_instead_of_reject (default off)"
```

---

### Task 3: Auto-publish guard for flagged posts

**Files:**

- Modify: `src/cofounder_agent/modules/content/auto_publish_gate.py:151-189` (docstring gate_state list + `evaluate` signature/early-return)
- Modify: `src/cofounder_agent/modules/content/atoms/content_evaluate_auto_publish.py:131-144`
- Test: `src/cofounder_agent/tests/unit/services/test_auto_publish_gate.py` (create if absent) + existing `test_content_evaluate_auto_publish*.py`

**Interfaces:**

- Produces: `evaluate(..., qa_flagged: bool = False)`. When `qa_flagged=True`, returns `AutoPublishDecision(would_fire=False, dry_run=True, gate_state="block_qa_flagged", reason=...)` before reading any config. Backcompat default `False`.

- [ ] **Step 1: Write failing test**

```python
import pytest
from modules.content.auto_publish_gate import evaluate

@pytest.mark.asyncio
async def test_qa_flagged_blocks_would_fire():
    dec = await evaluate(None, task_id="t1", niche_slug="glad-labs",
                         category="ai", quality_score=95.0, platform=object(),
                         qa_flagged=True)
    assert dec.would_fire is False
    assert dec.gate_state == "block_qa_flagged"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_auto_publish_gate.py -q`
Expected: FAIL (`evaluate() got an unexpected keyword argument 'qa_flagged'`).

- [ ] **Step 3: Implement** — add `qa_flagged: bool = False` to `evaluate`'s signature, and as the FIRST check inside the body (before the `platform is None` check):

```python
    if qa_flagged:
        return AutoPublishDecision(
            would_fire=False, dry_run=True, gate_state="block_qa_flagged",
            reason="post flagged by QA — operator sign-off required, never auto-publish",
            quality_score=quality_score,
        )
```

Add `'block_qa_flagged'` to the `gate_state` docstring enumeration (line ~165). Then in `content_evaluate_auto_publish.py`, pass it through (line ~137):

```python
        gate_decision = await _gate_check(
            db_pool,
            task_id=str(task_id),
            niche_slug=niche_slug,
            category=category,
            quality_score=quality_score,
            platform=platform,
            qa_flagged=bool(state.get("qa_flagged")),
        )
```

- [ ] **Step 4: Run, verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_auto_publish_gate.py tests/unit/services/atoms/ -q -k "auto_publish"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/auto_publish_gate.py src/cofounder_agent/modules/content/atoms/content_evaluate_auto_publish.py src/cofounder_agent/tests/unit/services/test_auto_publish_gate.py
git commit -m "feat(qa): auto-publish gate refuses qa_flagged posts (auto path only)"
```

---

### Task 4: Persist `qa_flagged` into `task_metadata`

**Files:**

- Modify: `src/cofounder_agent/modules/content/task_metadata.py` (the `build_task_metadata` returned dict)
- Test: `src/cofounder_agent/tests/unit/services/test_task_metadata.py` (or the existing test for that module)

**Interfaces:**

- Consumes: `state["qa_flagged"]`.
- Produces: `build_task_metadata(state, ...)["qa_flagged"]` mirrors `bool(state.get("qa_flagged"))`. The view (`pipeline_tasks_view.task_metadata`) then exposes it to the operator surface (Task 5) — no DDL, no view rebuild.

- [ ] **Step 1: Read `build_task_metadata`** to find the returned dict and a passthrough-from-state example. Confirm it receives `state` as first arg (it does — `content_persist_task.py:115`).

- [ ] **Step 2: Write failing test**

```python
from modules.content.task_metadata import build_task_metadata

def test_build_task_metadata_carries_qa_flagged():
    state = {"qa_flagged": True}
    md = build_task_metadata(state, preview_token="", content_text="x", seo_title="",
                             seo_description="", seo_keywords_list=[],
                             final_quality_score=79, early_eval_score=70)
    assert md.get("qa_flagged") is True

def test_build_task_metadata_qa_flagged_defaults_false():
    md = build_task_metadata({}, preview_token="", content_text="x", seo_title="",
                             seo_description="", seo_keywords_list=[],
                             final_quality_score=90, early_eval_score=90)
    assert md.get("qa_flagged") is False
```

(Adjust the kwargs to match the real `build_task_metadata` signature seen in Step 1.)

- [ ] **Step 3: Run, verify fail** → FAIL (`qa_flagged` absent).

- [ ] **Step 4: Implement** — add `"qa_flagged": bool(state.get("qa_flagged"))` to the dict `build_task_metadata` returns.

- [ ] **Step 5: Run, verify pass.**

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/task_metadata.py src/cofounder_agent/tests/unit/services/test_task_metadata.py
git commit -m "feat(qa): carry qa_flagged into task_metadata (surfaced via pipeline_tasks_view)"
```

---

### Task 5: Surface findings — service layer (`tasks_mcp`)

**Files:**

- Modify: `src/cofounder_agent/services/tasks_mcp.py:14-45`
- Test: `src/cofounder_agent/tests/unit/services/test_tasks_mcp.py` (create if absent; use a fake pool returning rows)

**Interfaces:**

- Produces:
  - `list_tasks(pool, status, limit)` rows now include `qa_feedback` and a derived `qa_flagged` bool (from `task_metadata->>'qa_flagged'`).
  - New `get_task_qa_feedback(pool, task_id) -> str` returning the latest `qa_feedback` for a task (read via `pipeline_tasks_view`).

- [ ] **Step 1: Write failing tests** with a fake pool:

```python
import pytest
from services import tasks_mcp

class _FakePool:
    def __init__(self, rows): self._rows = rows
    async def fetch(self, sql, *args): return self._rows
    async def fetchrow(self, sql, *args): return self._rows[0] if self._rows else None

@pytest.mark.asyncio
async def test_list_tasks_includes_qa_flagged():
    rows = [{"task_id": "t1", "topic": "x", "status": "awaiting_approval",
             "quality_score": 79, "created_at": None,
             "qa_feedback": "Final score: 79/100 (REJECTED)\n- ...", "qa_flagged": True}]
    pool = _FakePool(rows)
    out = await tasks_mcp.list_tasks(pool, status="awaiting_approval", limit=10)
    assert out[0]["qa_flagged"] is True
    assert "qa_feedback" in out[0]

@pytest.mark.asyncio
async def test_get_task_qa_feedback():
    pool = _FakePool([{"qa_feedback": "Final score: 79/100 (REJECTED)"}])
    fb = await tasks_mcp.get_task_qa_feedback(pool, "t1")
    assert "79/100" in fb
```

- [ ] **Step 2: Run, verify fail** → FAIL.

- [ ] **Step 3: Implement** — update the SQL constants + add the detail fn. The view exposes `qa_feedback` and `task_metadata`; derive `qa_flagged` in SQL:

```python
_LIST_COLS = (
    "task_id, topic, status, quality_score, created_at, qa_feedback, "
    "COALESCE((task_metadata->>'qa_flagged')::boolean, false) AS qa_flagged"
)
_LIST_TASKS_FILTERED_SQL = (
    f"SELECT {_LIST_COLS} FROM pipeline_tasks_view WHERE status = $1 "
    "ORDER BY created_at DESC LIMIT $2"
)
_LIST_TASKS_ALL_SQL = (
    f"SELECT {_LIST_COLS} FROM pipeline_tasks_view ORDER BY created_at DESC LIMIT $1"
)
_TASK_QA_FEEDBACK_SQL = (
    "SELECT qa_feedback FROM pipeline_tasks_view WHERE task_id::text = $1 LIMIT 1"
)


async def get_task_qa_feedback(pool: Any, task_id: str) -> str:
    """Latest QA per-rail feedback for a task (operator findings view)."""
    row = await pool.fetchrow(_TASK_QA_FEEDBACK_SQL, str(task_id))
    return (row["qa_feedback"] if row and row["qa_feedback"] else "") or ""
```

Add `get_task_qa_feedback` to `__all__`.

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/tasks_mcp.py src/cofounder_agent/tests/unit/services/test_tasks_mcp.py
git commit -m "feat(qa): surface qa_flagged + qa_feedback through tasks_mcp service layer"
```

---

### Task 6: Surface findings — CLI + MCP adapters

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/pipeline.py` (the `list` command renderer + new `qa` command)
- Modify: `mcp-server/server.py` (the `list_tasks` tool — include `qa_flagged` in its serialized rows)
- Test: `src/cofounder_agent/tests/unit/cli/test_pipeline_qa_cli.py` (create)

**Interfaces:**

- Consumes: `tasks_mcp.list_tasks` (rows with `qa_flagged`), `tasks_mcp.get_task_qa_feedback` (Task 5).
- Produces: `poindexter pipeline list` prints a `⚑` next to flagged tasks; `poindexter pipeline qa <task>` prints the per-rail findings.

- [ ] **Step 1: Read `poindexter/cli/pipeline.py`** to learn the Click group object name, how `list` renders rows, and how commands resolve a task-id prefix (`tasks_mcp.resolve_task_prefix`). Match that style.

- [ ] **Step 2: Write failing test** (CliRunner, patch `tasks_mcp`):

```python
from click.testing import CliRunner
from unittest.mock import AsyncMock, patch
from poindexter.cli.pipeline import pipeline as pipeline_group  # adjust to real group name

def test_pipeline_qa_prints_feedback():
    runner = CliRunner()
    with patch("poindexter.cli.pipeline.tasks_mcp.get_task_qa_feedback",
               new=AsyncMock(return_value="Final score: 79/100 (REJECTED)\n- programmatic_validator ... FAIL: bad")), \
         patch("poindexter.cli.pipeline.tasks_mcp.resolve_task_prefix", new=AsyncMock(return_value="t1full")), \
         patch("poindexter.cli.pipeline._dsn", return_value="postgresql://x"), \
         patch("poindexter.cli.pipeline.asyncpg.create_pool", new=AsyncMock()):
        result = runner.invoke(pipeline_group, ["qa", "t1"])
    assert "79/100" in result.output
```

(Match the real pool-acquisition seam in `pipeline.py`; the patches above are illustrative — align to how `resume`/`regen` commands open their pool.)

- [ ] **Step 3: Run, verify fail** → FAIL (no `qa` command).

- [ ] **Step 4: Implement** the `qa` command (mirror the pool-open pattern used by `regen`/`resume`) and add a `⚑` marker in the `list` renderer when `row.get("qa_flagged")`. In `mcp-server/server.py`'s `list_tasks` tool, include `qa_flagged` in the dict it returns (it already calls `tasks_mcp.list_tasks`; just pass the field through).

- [ ] **Step 5: Run, verify pass.**

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/pipeline.py mcp-server/server.py src/cofounder_agent/tests/unit/cli/test_pipeline_qa_cli.py
git commit -m "feat(qa): poindexter pipeline qa <task> + flag marker; MCP list_tasks mirrors qa_flagged"
```

---

### Task 7: Demote `deepeval_faithfulness` to advisory (C2)

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_demote_deepeval_faithfulness_to_advisory.py`
- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` (the `qa_gates` seed row for `deepeval_faithfulness`)
- Test: `src/cofounder_agent/tests/unit/services/migrations/test_demote_faithfulness.py` (assert the SQL is the expected UPDATE) + `python scripts/ci/migrations_lint.py`

**Interfaces:** Produces: prod `qa_gates.deepeval_faithfulness.required_to_pass = false`; fresh DBs seed it advisory.

- [ ] **Step 1: Generate the migration**

```bash
cd src/cofounder_agent && python scripts/new-migration.py "demote deepeval_faithfulness to advisory"
```

- [ ] **Step 2: Write failing test** asserting the migration module exposes `up(pool)` and runs the expected UPDATE against a fake conn (mirror an existing migration unit test in `tests/unit/services/migrations/`). Assert the executed SQL contains `UPDATE` + `qa_gates` + `required_to_pass` + `deepeval_faithfulness`.

- [ ] **Step 3: Run, verify fail.**

- [ ] **Step 4: Implement** the migration body (stdlib-only, idempotent):

```python
async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE qa_gates SET required_to_pass = false "
            "WHERE name = 'deepeval_faithfulness';"
        )

async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE qa_gates SET required_to_pass = true "
            "WHERE name = 'deepeval_faithfulness';"
        )
```

Then update the `deepeval_faithfulness` row in `0000_baseline.seeds.sql` so a fresh DB seeds `required_to_pass = false` (find the `INSERT INTO qa_gates ... deepeval_faithfulness ...` row).

- [ ] **Step 5: Run tests + lint + smoke**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/migrations/test_demote_faithfulness.py -q && python scripts/ci/migrations_lint.py && python scripts/ci/migrations_smoke.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/migrations/ src/cofounder_agent/tests/unit/services/migrations/test_demote_faithfulness.py
git commit -m "feat(qa): demote deepeval_faithfulness to advisory (prod mutation + baseline seed)"
```

---

### Task 8: Validator internal-term allowlist (C3)

**Files:**

- Modify: `src/cofounder_agent/modules/content/content_validator.py:900-979` (`_HALLUCINATION_WHITELIST_BASE`)
- Test: `src/cofounder_agent/tests/unit/services/test_content_validator.py` (or the validator's existing test file)

**Interfaces:** Produces: the validator's hallucination detector no longer flags the seeded internal pipeline terms.

- [ ] **Step 1: Confirm the detection consults the whitelist** — grep `content_validator.py` for `_get_hallucination_whitelist(` and confirm `_detect_hallucinated_references` / `_is_known_reference` checks it. If the call site only checks stdlib/pypi/ollama, add a `norm_name in _get_hallucination_whitelist()` short-circuit there (that is itself the fix; write the test below against it).

- [ ] **Step 2: Write failing test**

```python
def test_validator_does_not_flag_internal_pipeline_term():
    from modules.content.content_validator import _detect_hallucinated_references
    issues = _detect_hallucinated_references("We call `generate_content` to run the pipeline.")
    assert not any("generate_content" in str(i) for i in issues)

def test_validator_still_flags_unknown_lib():
    from modules.content.content_validator import _detect_hallucinated_references
    issues = _detect_hallucinated_references("Install `flibbertigibbet_xyzlib` from PyPI.")
    assert any("flibbertigibbet" in str(i) for i in issues)
```

(Adjust to the real `_detect_hallucinated_references` return shape seen in Step 1.)

- [ ] **Step 3: Run, verify fail** → the first test FAILs.

- [ ] **Step 4: Implement** — add an internal-terms block to `_HALLUCINATION_WHITELIST_BASE` (lowercase, normalized), e.g.:

```python
    # ---- Poindexter-internal pipeline vocabulary (recurs in dev-facing posts;
    #      not external libraries — see the fa07bfbf false-positive 2026-06-22)
    "generate_content", "qa_aggregate", "qa_rewrite", "canonical_blog",
    "pipeline_tasks", "pipeline_versions", "graph_def", "template_runner",
    "auto_publish_gate", "multi_model_qa", "content_validator",
```

- [ ] **Step 5: Run, verify pass.**

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/content_validator.py src/cofounder_agent/tests/unit/services/test_content_validator.py
git commit -m "fix(qa): stop the validator flagging poindexter-internal function names as fabrication"
```

---

### Task 9: Grafana flag-rate panel + docs

**Files:**

- Modify: a Grafana dashboard JSON under `infrastructure/grafana/...` (Pipeline or QA Rails board)
- Modify: `docs/architecture/anti-hallucination.md` (note the never-discard terminal + flag state)
- Modify: memory `project_qa_rails_state_2026_06.md` + `MEMORY.md` (record the self-heal-before-paging change)

- [ ] **Step 1:** Add a time-series panel "QA flagged vs discarded" sourced from `audit_log WHERE event_type='qa_flagged_surfaced'` (flag rate) alongside the existing `qa_pass_completed` panels. Match an existing panel's datasource + query shape on that board.
- [ ] **Step 2:** Validate the dashboard JSON parses: `python -c "import json,sys; json.load(open(sys.argv[1]))" <dashboard.json>`.
- [ ] **Step 3:** Update `anti-hallucination.md`: the QA gate flag-and-continues behind `qa_flag_instead_of_reject` rather than hard-rejecting; `rejected_final` is operator-only.
- [ ] **Step 4:** Update the memory note + MEMORY.md index line.
- [ ] **Step 5: Commit**

```bash
git add infrastructure/grafana docs/architecture/anti-hallucination.md
git commit -m "docs(qa): grafana flag-rate panel + anti-hallucination never-discard note"
```

---

### Task 10: PR, e2e in Docker, flip the switch (execution phase)

> Not a code task — the rollout. Do AFTER Tasks 1–9 merge to `main` and the deploy clone pulls.

- [ ] **Step 1: Open the PR** off `feat/qa-self-heal-gate` → `Glad-Labs/glad-labs-stack main`. Body summarizes the spec + links it. Wait for required CI (test-backend, migrations-smoke, mcp-server-tests) green; merge (squash).
- [ ] **Step 2: Deploy** — `git -C ~/.poindexter/deploy/glad-labs-stack pull` (CLI subprocess + worker bind-mount pick up the new code; restart worker if a settings_defaults reseed is needed: `docker compose up -d poindexter-prefect-worker`).
- [ ] **Step 3: Apply the faithfulness migration** on prod (worker boot runs migrations) and confirm `qa_gates.deepeval_faithfulness.required_to_pass=false`.
- [ ] **Step 4: e2e with switch still OFF** — drive a fresh draft; confirm behavior byte-identical to today (a QA-failing draft still rejects). Then `set_setting qa_flag_instead_of_reject true` (runtime, no deploy).
- [ ] **Step 5: e2e with switch ON** — drive a draft that fails QA; confirm it (a) regenerates (bounded), (b) lands at `awaiting_approval` with `qa_flagged=true` + `qa_feedback` visible via `poindexter pipeline qa <task>`, (c) is NOT auto-published, (d) with `pipeline_gate_preview_gate` on, reaches preview_gate and a regen choice re-QAs without discarding (spec §10/§11).
- [ ] **Step 6: Flip the default** — once e2e is clean, set `qa_flag_instead_of_reject='true'` in `settings_defaults.py` (a follow-up commit/PR) so fresh installs get the self-heal behavior, mirroring the preview_gate T12 playbook.

---

## Self-Review

**Spec coverage:**

- §1 flag-and-continue → Task 2. §2 broaden regen → Task 1. §3 auto-publish guard → Task 3. §4 durable signal → Task 4 (task_metadata, refined from a column to avoid the view rebuild). §5 surface → Tasks 5–6. §6 faithfulness advisory → Task 7. §7 validator allowlist → Task 8 (refined to extend the existing base set, no new setting). §8 telemetry/Grafana → Tasks 2 (audit event) + 9 (panel). §9 master switch + rollout → Task 2 (seed) + Task 10 (flip). §10 preview_gate interaction → Task 3 (guard scoping) + Task 10 Step 5 (e2e). Testing-plan items 1–12 → covered across Tasks 1–8 + Task 10. ✓ No gaps.

**Placeholder scan:** Steps that touch unread files (Task 4 `build_task_metadata` kwargs, Task 6 CLI group name, Task 8 `_detect_hallucinated_references` shape) carry an explicit "read first in Step 1, match the real signature" instruction rather than a guessed final form — deliberate, since the exact kwarg list/return shape is confirmed at execution. No "TODO/TBD/handle errors" placeholders.

**Type consistency:** `qa_flagged` is a `bool` everywhere (PipelineState channel, `state.get`, `task_metadata` value, `evaluate(qa_flagged=...)`, SQL `::boolean`). `is_rescuable_reject(..., broaden=bool)` matches its single caller in Task 2. `get_task_qa_feedback(pool, task_id) -> str` matches its Task 6 consumer. `qa_flag_instead_of_reject` string `'false'`/`'true'` parsed consistently. ✓
