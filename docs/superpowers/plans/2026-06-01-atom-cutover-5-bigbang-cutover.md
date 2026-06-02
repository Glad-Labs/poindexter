# Atom Cutover — Plan 5: big-bang cutover (parity + flip + delete legacy)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the graph_def `canonical_blog` path a FAITHFUL drop-in for the deleted `cross_model_qa` stage, then flip it live and delete the legacy path. Specifically: port the QA-decision parity into `qa.aggregate` (reject-path DB writes + `quality_score` promotion + `qa_reviews` population), flip `app_settings.pipeline_use_graph_def='true'` globally, and DELETE the hand-coded `canonical_blog` factory (+ `_CANONICAL_BLOG_ORDER` + its `TEMPLATES` entry) and the `cross_model_qa` stage (+ entry-point + registry row + its now-broken tests).

**Why parity first (operator-authorized big-bang):** the graph_def path was only compile-tested. The Plan-3 `qa.aggregate` only set `_halt` on reject — it did NOT replicate what the legacy stage did on reject (write `pipeline_tasks.status='rejected'` + a `pipeline_versions` draft + a `pipeline_gate_history` row + `model_performance.human_approved=False`). Flipping live without that would leave every QA-rejected task stuck in `in_progress` until the stale sweep. The legacy reject correctness comes from those **DB writes** (`status` is NOT a `PipelineState` channel, and the caller does no DB re-read), so `qa.aggregate` must do the same DB writes. It must also promote `quality_score = max(early, qa)` and set `qa_reviews` (which `finalize_task` reads for the approval-UI `qa_feedback`).

**Architecture:** `qa.aggregate` becomes the QA-decision atom — same responsibilities the stage had. On APPROVE it sets `qa_final_score`/`qa_final_verdict`/`quality_score`(promoted)/`qa_reviews`(=`qa_rail_reviews`)/`qa_rewrite_attempts=0`. On REJECT it calls a best-effort persistence helper (`services/atoms/_qa_persist.py`) that does the four legacy DB writes, then returns `_halt`/`_halt_reason`/`status="rejected"` plus the score keys. A migration flips the flag. The legacy `canonical_blog` factory and the `cross_model_qa` stage are deleted; `multi_model_qa.py` (the `MultiModelQA` rail library the qa.\* atoms delegate to) and the `dev_diary` factory STAY.

**Tech Stack:** Python 3.13, asyncpg (stub `database_service`/`pool` in unit tests — no live DB), `services/pipeline_db.py::PipelineDB` (lazy import on reject), pytest (`asyncio_mode="auto"`; `@pytest.mark.unit`). The flip migration is self-contained (only `app_settings`).

**Spec:** `docs/superpowers/specs/2026-06-01-canonical-blog-atom-cutover-design.md` (§ "Cutover mechanism" step 3 + D1) — flip the default, then delete the hand-coded factory + superseded stages. Operator chose the big-bang variant (no per-niche canary); the human-approval gate (`finalize_task → awaiting_approval`, no auto-publish) is the content backstop, and this plan closes the pipeline-state parity gaps so a reject can't leave a task stuck.

**Conventions:** run tests from `src/cofounder_agent` with the main venv python (worktrees have no poetry env):
`"<main-venv-python>" -m pytest <relative/test/path> -p no:cacheprovider > test_out.txt 2>&1` then **Read `test_out.txt` back** (Windows stdout buffers; never treat empty as success; delete `test_out.txt`, don't commit it). cwd = the worktree's `src/cofounder_agent`. Linear commits, commit after each green task, end every message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Do NOT push/PR/merge — the controller integrates. **This whole plan ships as ONE PR** (the migration flip + the deletions land together atomically on merge).

### Reference facts (verified — don't re-derive)

- The legacy reject block to port: `services/stages/cross_model_qa.py:460-556`. It does, in order: `database_service.update_task(task_id, {"status":"rejected","error_message":reason,"quality_score":float(final_score)})`; `PipelineDB(database_service.pool).upsert_version(task_id, {"title","content","quality_score":int(round(final_score)),"qa_feedback","models_used_by_phase"})`; `database_service.mark_model_performance_outcome(task_id, human_approved=False)`; `database_service.pool.execute("INSERT INTO pipeline_gate_history (task_id, gate_name, event_kind, feedback, metadata) VALUES ($1,$2,$3,$4,$5::jsonb)", task_id, "multi_model_qa", "rejected", reason[:2000], json.dumps({"reviewer":"multi_model_qa","decision":"rejected"}))`. Each legacy DB call after `update_task` is wrapped in its own try/except (best-effort).
- The legacy approve promotion: `services/stages/cross_model_qa.py:388-407`. `qa_reviews_dicts = [{"reviewer","score","approved","feedback","provider"} for r in reviews]`; `promoted = max(float(context.get("quality_score",0) or 0), float(final_score))`; updates set `qa_final_score`, `quality_score=promoted`, `qa_reviews=qa_reviews_dicts`, `qa_rewrite_attempts`, `content`.
- `qa_rail_reviews` items are already `{"reviewer","approved","score","feedback","provider","advisory"}` (Plan 3) — a superset of the legacy `qa_reviews` shape, so `qa_reviews = qa_rail_reviews` is downstream-compatible.
- `quality_score`, `qa_reviews` (Annotated[list, operator.add]), `qa_final_score`, `qa_rewrite_attempts` ARE declared `PipelineState` channels (propagate). `status` is NOT — so the reject `status` is carried by the DB write, not state (matching legacy); returning it in state too is harmless belt-and-suspenders.
- `services/atoms/qa_aggregate.py` currently emits only `qa_final_score`/`qa_final_verdict` + `_halt` on reject (no DB writes). `aggregate_rail_reviews(...)` (in `services/atoms/_qa_rail_common.py`) returns `{qa_final_score, qa_final_verdict, approved, vetoed_by}`.
- `database_service` / `database_service.pool` / `task_id` / `content` / `title` / `topic` / `models_used_by_phase` are all available in the atom's `state` (services merged in by `_wrap_atom`).
- `cross_model_qa` consumers to remove on deletion: `pyproject.toml` entry-point (`cross_model_qa = "...:CrossModelQAStage"`), `plugins/registry.py` `_SAMPLES` row `("stages", "services.stages.cross_model_qa", "CrossModelQAStage")`. `multi_model_qa.py` and the `qa_pass_completed` audit (emitted inside `MultiModelQA.review()`) STAY.
- Test files that import the deleted stage and will break at collection: `tests/unit/services/stages/test_cross_model_qa.py`, `tests/unit/services/test_cross_model_qa_prompts.py` (delete both — they only test the stage), and PARTIAL imports in `tests/unit/services/test_pipeline_versions_persistence_473.py` + `tests/unit/services/test_lane_b_qa_critic_migration.py` (remove only the cross_model_qa-importing tests, keep the rest).
- Legacy factory deletion: `services/pipeline_templates/__init__.py` — remove `_CANONICAL_BLOG_ORDER` (only used by the factory), the `canonical_blog()` function, and the `"canonical_blog": canonical_blog` entry from `TEMPLATES`. KEEP `_registered_stages`, `dev_diary`, and the `template_runner` imports (shared with dev_diary).

---

### Task 1: `qa.aggregate` parity — approve-path keys + reject-path DB writes

The correctness core. A new best-effort persistence helper does the four legacy reject DB writes; `qa.aggregate` calls it on reject and adds the downstream-standard state keys on both paths.

**Files:**

- Create: `src/cofounder_agent/services/atoms/_qa_persist.py`
- Modify: `src/cofounder_agent/services/atoms/qa_aggregate.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_persist.py` (create)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py` (extend — the Plan-3 file)

- [ ] **Step 1: Write the failing tests for the persistence helper**

`test_qa_persist.py`:

```python
"""Unit tests for the qa reject-persistence helper (atom-cutover Plan 5, #355).
Stub database_service — no live DB."""

from __future__ import annotations

from typing import Any

import pytest

from services.atoms._qa_persist import build_qa_feedback, build_reject_reason, persist_qa_reject


class _Pool:
    def __init__(self):
        self.execs: list[tuple] = []

    async def execute(self, sql: str, *args: Any):
        self.execs.append((sql, args))


class _DB:
    def __init__(self):
        self.pool = _Pool()
        self.update_task_calls: list[tuple] = []
        self.mark_calls: list[tuple] = []

    async def update_task(self, task_id, fields):
        self.update_task_calls.append((task_id, fields))

    async def mark_model_performance_outcome(self, task_id, human_approved):
        self.mark_calls.append((task_id, human_approved))


@pytest.mark.unit
class TestBuildHelpers:
    def test_reject_reason_names_failing_rails(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": False, "score": 40.0, "feedback": "weak intro", "provider": "ollama"},
            {"reviewer": "deepeval_g_eval", "approved": True, "score": 85.0, "feedback": "ok", "provider": "ollama"},
        ]
        reason = build_reject_reason(reviews, vetoed_by=["ollama_qa"], final_score=55.0)
        assert "ollama_qa" in reason
        assert "55" in reason

    def test_qa_feedback_lists_reviews(self):
        reviews = [{"reviewer": "ollama_qa", "approved": False, "score": 40.0, "feedback": "weak intro", "provider": "ollama"}]
        fb = build_qa_feedback(reviews, final_score=55.0, approved=False)
        assert "ollama_qa" in fb and "weak intro" in fb


@pytest.mark.unit
class TestPersistQaReject:
    async def test_does_all_four_writes(self, monkeypatch):
        db = _DB()
        captured = {}

        class _FakePipelineDB:
            def __init__(self, pool):
                captured["pool"] = pool

            async def upsert_version(self, task_id, fields):
                captured["upsert"] = (task_id, fields)

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        await persist_qa_reject(
            db, task_id="t1", reason="bad", final_score=55.0,
            content="body", title="A Title", qa_feedback="fb",
            models_used_by_phase={"writer": "m"},
        )
        # 1. update_task(status=rejected, quality_score)
        assert db.update_task_calls[0][0] == "t1"
        assert db.update_task_calls[0][1]["status"] == "rejected"
        assert db.update_task_calls[0][1]["quality_score"] == 55.0
        # 2. upsert_version
        assert captured["upsert"][0] == "t1"
        assert captured["upsert"][1]["quality_score"] == 55  # int(round(55.0))
        # 3. mark_model_performance_outcome(human_approved=False)
        assert db.mark_calls[0] == ("t1", False)
        # 4. pipeline_gate_history INSERT
        assert any("pipeline_gate_history" in sql for sql, _ in db.pool.execs)

    async def test_best_effort_swallows_pool_error(self, monkeypatch):
        db = _DB()

        async def boom(sql, *args):
            raise RuntimeError("db down")

        db.pool.execute = boom  # gate_history write fails

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        # Must not raise — update_task still happened.
        await persist_qa_reject(
            db, task_id="t1", reason="bad", final_score=55.0,
            content="b", title="t", qa_feedback="fb", models_used_by_phase={},
        )
        assert db.update_task_calls[0][1]["status"] == "rejected"

    async def test_none_db_noop(self):
        await persist_qa_reject(None, task_id="t1", reason="r", final_score=1.0,
                                content="c", title="t", qa_feedback="f", models_used_by_phase={})
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_persist.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `services.atoms._qa_persist` does not exist.

- [ ] **Step 3: Create `services/atoms/_qa_persist.py`**

```python
"""Reject-path persistence for qa.aggregate (atom-cutover Plan 5, #355).

When qa.aggregate rejects a draft it must replicate the DB writes the
legacy cross_model_qa stage did (services/stages/cross_model_qa.py:460-556),
because `status` is not a PipelineState channel and the caller does no DB
re-read — the rejected state lives ONLY in the DB. Underscore-prefixed so
the atom registry skips it (helper, not an atom).

Every write is best-effort: a telemetry/version hiccup must not crash the
pipeline. update_task (the load-bearing status write) runs first; the rest
are wrapped individually like the legacy stage.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_reject_reason(reviews: list[dict[str, Any]], vetoed_by: list[Any], final_score: float) -> str:
    """Human-readable rejection reason from the failing rails."""
    failing = [r for r in reviews if not r.get("approved")]
    parts = [
        f"{r.get('reviewer')}: {str(r.get('feedback') or '').strip()[:200]}"
        for r in failing
    ] or [f"vetoed_by={list(vetoed_by)}"]
    return f"QA rejected (score {final_score:.0f}/100). " + "; ".join(parts)


def build_qa_feedback(reviews: list[dict[str, Any]], final_score: float, approved: bool) -> str:
    """Operator-facing QA feedback text (mirrors MultiModelResult.format_feedback_text)."""
    header = f"Final score: {final_score:.0f}/100 ({'APPROVED' if approved else 'REJECTED'})"
    lines = [header]
    for r in reviews:
        status = "pass" if r.get("approved") else "FAIL"
        fb = str(r.get("feedback") or "").strip() or "(no feedback)"
        lines.append(
            f"- {r.get('reviewer')} [{r.get('provider')}] "
            f"{float(r.get('score') or 0):.0f}/100 {status}: {fb}"
        )
    return "\n".join(lines)


async def persist_qa_reject(
    database_service: Any,
    *,
    task_id: str,
    reason: str,
    final_score: float,
    content: str,
    title: str,
    qa_feedback: str,
    models_used_by_phase: dict[str, Any],
) -> None:
    """Replicate the legacy cross_model_qa reject DB writes. Best-effort.

    1. pipeline_tasks: status=rejected + quality_score (load-bearing).
    2. pipeline_versions: persist the rejected draft (#473).
    3. model_performance.human_approved=False (learning signal).
    4. pipeline_gate_history: 'rejected' row (Grafana approval_status).
    """
    if database_service is None or not task_id:
        return

    # 1. Status write — the load-bearing one. If THIS fails, log loud.
    try:
        await database_service.update_task(task_id, {
            "status": "rejected",
            "error_message": reason,
            "quality_score": float(final_score),
        })
    except Exception as exc:  # noqa: BLE001
        logger.error("[qa.aggregate] reject status write failed for %s: %s", task_id[:8], exc)

    # 2. Rejected draft.
    try:
        from services.pipeline_db import PipelineDB
        await PipelineDB(database_service.pool).upsert_version(task_id, {
            "title": title,
            "content": content,
            "quality_score": int(round(float(final_score))),
            "qa_feedback": qa_feedback,
            "models_used_by_phase": models_used_by_phase or {},
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[qa.aggregate] pipeline_versions write failed for %s: %s", task_id[:8], exc)

    # 3. Learning signal.
    try:
        await database_service.mark_model_performance_outcome(task_id, human_approved=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[qa.aggregate] mark_model_performance_outcome failed: %s", exc)

    # 4. Gate-history row.
    try:
        await database_service.pool.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            task_id, "multi_model_qa", "rejected", reason[:2000],
            json.dumps({"reviewer": "multi_model_qa", "decision": "rejected"}, default=str),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[qa.aggregate] pipeline_gate_history write failed for %s: %s", task_id[:8], exc)


__all__ = ["build_qa_feedback", "build_reject_reason", "persist_qa_reject"]
```

- [ ] **Step 4: Run, verify the persist tests pass**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_persist.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass.

- [ ] **Step 5: Write the failing tests for the extended qa.aggregate** (append to `test_qa_aggregate_atom.py`)

```python
class _Pool2:
    def __init__(self):
        self.execs = []

    async def execute(self, sql, *args):
        self.execs.append((sql, args))


class _DB2:
    def __init__(self):
        self.pool = _Pool2()
        self.update_task_calls = []
        self.mark_calls = []

    async def update_task(self, task_id, fields):
        self.update_task_calls.append((task_id, fields))

    async def mark_model_performance_outcome(self, task_id, human_approved):
        self.mark_calls.append((task_id, human_approved))


@pytest.mark.unit
class TestQaAggregateParity:
    async def test_approve_sets_downstream_keys(self):
        state = {
            "site_config": _Cfg(),
            "quality_score": 60.0,
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False, "feedback": "good"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert out["quality_score"] == 90.0          # promoted = max(60, 90)
        assert out["qa_reviews"] == state["qa_rail_reviews"]  # populated for finalize_task
        assert out["qa_rewrite_attempts"] == 0
        assert "_halt" not in out

    async def test_approve_keeps_higher_early_score(self):
        state = {"site_config": _Cfg(), "quality_score": 95.0,
                 "qa_rail_reviews": [{"reviewer": "x", "approved": True, "score": 80.0, "provider": "ollama", "advisory": False, "feedback": ""}]}
        out = await qa_aggregate.run(state)
        assert out["quality_score"] == 95.0  # max(95, 80)

    async def test_reject_does_db_writes_and_halts(self, monkeypatch):
        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            "site_config": _Cfg(),
            "task_id": "task-9",
            "content": "the body",
            "title": "A Title",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": False, "score": 40.0, "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert out["status"] == "rejected"
        # DB writes happened.
        assert db.update_task_calls[0][1]["status"] == "rejected"
        assert db.mark_calls[0] == ("task-9", False)
        assert any("pipeline_gate_history" in sql for sql, _ in db.pool.execs)

    async def test_reject_without_db_service_still_halts(self):
        state = {"site_config": _Cfg(), "task_id": "t",
                 "qa_rail_reviews": [{"reviewer": "x", "approved": False, "score": 10.0, "provider": "ollama", "advisory": False, "feedback": "no"}]}
        out = await qa_aggregate.run(state)  # no database_service
        assert out["_halt"] is True
        assert out["qa_final_verdict"] == "reject"
```

(The `_Cfg` stub already exists at the top of this test file from Plan 3.)

- [ ] **Step 6: Run, verify the new qa.aggregate tests fail**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_aggregate_atom.py::TestQaAggregateParity -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: FAIL — current `qa.aggregate` doesn't set `quality_score`/`qa_reviews` and does no DB writes.

- [ ] **Step 7: Rewrite `services/atoms/qa_aggregate.py`**

```python
"""qa.aggregate — combine the qa.* rail reviews into the QA gate decision.

Atom-cutover #355. Reads the ``qa_rail_reviews`` channel, applies the
DB-configurable weighted-score + non-advisory-veto + threshold aggregation
(_qa_rail_common.aggregate_rail_reviews), and acts as the QA-decision point
the cross_model_qa stage used to be:

- APPROVE: emit qa_final_score / qa_final_verdict, promote
  quality_score = max(early, qa) and populate qa_reviews (read by
  finalize_task for the approval-UI feedback).
- REJECT: do the same DB writes the legacy stage did (via _qa_persist) —
  status=rejected + rejected-draft + model_performance + gate_history —
  then set _halt so build_graph_from_spec's halt-aware router short-circuits
  the graph (skipping the rest of the pipeline), mirroring the legacy
  continue_workflow=False.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import aggregate_rail_reviews

ATOM_META = AtomMeta(
    name="qa.aggregate",
    type="atom",
    version="2.0.0",
    description="Combine qa.* rail reviews into the QA gate decision (+ reject persistence).",
    inputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="per-rail reviews"),),
    outputs=(
        FieldSpec(name="qa_final_score", type="float", description="weighted QA score"),
        FieldSpec(name="qa_final_verdict", type="str", description="approve|reject"),
        FieldSpec(name="quality_score", type="float", description="promoted max(early, qa)"),
        FieldSpec(name="qa_reviews", type="list[dict]", description="reviews for the approval UI"),
    ),
    requires=("qa_rail_reviews",),
    produces=("qa_final_score", "qa_final_verdict", "quality_score", "qa_reviews"),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("writes pipeline_tasks/pipeline_versions/pipeline_gate_history on reject",),
    parallelizable=False,
)


def _weight(site_config: Any, key: str, default: float) -> float:
    if site_config is None:
        return default
    try:
        return float(site_config.get(key, default))
    except (TypeError, ValueError):
        return default


async def run(state: dict[str, Any]) -> dict[str, Any]:
    site_config = state.get("site_config")
    reviews = state.get("qa_rail_reviews") or []
    result = aggregate_rail_reviews(
        reviews,
        validator_weight=_weight(site_config, "qa_validator_weight", 0.4),
        critic_weight=_weight(site_config, "qa_critic_weight", 0.6),
        gate_weight=_weight(site_config, "qa_gate_weight", 0.3),
        threshold=_weight(site_config, "qa_final_score_threshold", 70.0),
    )
    final_score = result["qa_final_score"]

    # Promote the canonical quality_score (max of early-eval + QA), mirroring
    # the legacy stage so downstream finalize_task / auto-publish use the QA score.
    early = 0.0
    try:
        early = float(state.get("quality_score") or 0.0)
    except (TypeError, ValueError):
        early = 0.0
    promoted = max(early, float(final_score))

    out: dict[str, Any] = {
        "qa_final_score": final_score,
        "qa_final_verdict": result["qa_final_verdict"],
        "quality_score": promoted,
        # qa_reviews uses an operator.add reducer; it's empty before this node
        # in canonical_blog (rails write qa_rail_reviews), so this populates it
        # for finalize_task's qa_feedback.
        "qa_reviews": list(reviews),
        "qa_rewrite_attempts": 0,
    }

    if not result["approved"]:
        from services.atoms._qa_persist import (
            build_qa_feedback,
            build_reject_reason,
            persist_qa_reject,
        )
        reason = build_reject_reason(reviews, result["vetoed_by"], float(final_score))
        await persist_qa_reject(
            state.get("database_service"),
            task_id=str(state.get("task_id") or ""),
            reason=reason,
            final_score=float(final_score),
            content=str(state.get("content") or ""),
            title=str(state.get("title") or state.get("topic") or ""),
            qa_feedback=build_qa_feedback(reviews, float(final_score), approved=False),
            models_used_by_phase=state.get("models_used_by_phase") or {},
        )
        out["_halt"] = True
        out["_halt_reason"] = f"qa.aggregate: reject (score={final_score}, {reason[:120]})"
        # Belt-and-suspenders: the DB write above is load-bearing (status is
        # not a PipelineState channel), but set it in state too in case a
        # caller reads final_state.
        out["status"] = "rejected"

    return out


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 8: Run, verify all qa.aggregate + persist tests pass**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_aggregate_atom.py tests/unit/services/atoms/test_qa_persist.py tests/unit/services/atoms/test_qa_rail_registry.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass (including the Plan-3 `TestQaAggregateAtom` tests — the `version`/`produces` additions don't break them; if `test_meta` asserts an exact `produces` tuple, update it to include `quality_score`/`qa_reviews`).

- [ ] **Step 9: Commit**

```bash
git add src/cofounder_agent/services/atoms/_qa_persist.py src/cofounder_agent/services/atoms/qa_aggregate.py src/cofounder_agent/tests/unit/services/atoms/test_qa_persist.py src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py
git commit -m "feat(qa): qa.aggregate parity — reject DB writes + score/review promotion (#355)"
```

---

### Task 2: flip migration — `pipeline_use_graph_def='true'`

Makes the graph_def path live for `canonical_blog` on prod. Idempotent; self-contained (only `app_settings`).

**Files:**

- Create: `src/cofounder_agent/services/migrations/<generated-timestamp>_flip_pipeline_use_graph_def.py` (generate with the helper)

- [ ] **Step 1: Generate** (cwd = worktree root): `"<venv-python>" scripts/new-migration.py "flip pipeline use graph def to true"`. Note the printed path.

- [ ] **Step 2: Replace the generated body**

```python
"""Migration: flip pipeline_use_graph_def to true (#355 atom-cutover Plan 5)

Big-bang cutover: make canonical_blog run as the seeded graph_def (the qa.*
rail atoms replacing cross_model_qa) instead of the legacy Python factory,
which this PR deletes. The Plan-4 migration seeded this key 'false'; flip it
'true' here. Operators can still toggle it (this is the last migration that
sets it). dev_diary has no graph_def row, so it falls back to its (retained)
legacy factory even with the flag true.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "pipeline_use_graph_def", "true",
        )
    logger.info("Migration flip_pipeline_use_graph_def: set true")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'false' WHERE key = 'pipeline_use_graph_def'"
        )
    logger.info("Migration flip_pipeline_use_graph_def down: set false")
```

- [ ] **Step 3: Lint** (cwd = worktree root): `"<venv-python>" scripts/ci/migrations_lint.py` → expect 0 errors.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/migrations/<generated-timestamp>_flip_pipeline_use_graph_def.py
git commit -m "feat(pipeline): flip pipeline_use_graph_def=true (big-bang cutover) (#355)"
```

---

### Task 3: delete the legacy `canonical_blog` factory

Remove the hand-coded factory + its order tuple + its `TEMPLATES` entry. KEEP `dev_diary`, `_registered_stages`, and the shared `template_runner` imports.

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_templates/__init__.py`

- [ ] **Step 1: Remove the three pieces**

In `services/pipeline_templates/__init__.py`:

1. Delete the `_CANONICAL_BLOG_ORDER` tuple (the `verify_task … finalize_task` list, ~lines 63-91).
2. Delete the entire `def canonical_blog(...) -> StateGraph:` factory function (~lines 94-138).
3. In the `TEMPLATES` dict, remove the `"canonical_blog": canonical_blog,` line, leaving `TEMPLATES = {"dev_diary": dev_diary}`.

Do NOT touch `_registered_stages`, `dev_diary`, `load_active_graph_def`, or the `from services.template_runner import (...)` block.

- [ ] **Step 2: Verify the module imports + dev_diary still builds**

Run (cwd = `src/cofounder_agent`):
`"<venv-python>" -c "from services.pipeline_templates import TEMPLATES; print(sorted(TEMPLATES)); assert 'canonical_blog' not in TEMPLATES; assert 'dev_diary' in TEMPLATES; g = TEMPLATES['dev_diary'](pool=None, record_sink=[]); g.compile(); print('dev_diary OK')" > test_out.txt 2>&1` then read `test_out.txt`.
Expected: prints `['dev_diary']` then `dev_diary OK`.

- [ ] **Step 3: Run the pipeline_templates-related tests**

Run: `"<venv-python>" -m pytest tests/unit/services/test_load_active_graph_def.py tests/unit/services/test_canonical_blog_spec.py tests/unit/services/test_template_runner_state_partition.py tests/unit/services/test_template_runner_graphdef_routing.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass. If a test references the deleted `canonical_blog` factory directly (e.g. imports it or asserts it's in `TEMPLATES`), update that test to use `dev_diary` or the graph_def path, or remove the stale assertion. (The Plan-4 routing test uses `monkeypatch.setitem(TEMPLATES, "canonical_blog", ...)`, which works whether or not the key pre-exists — no change needed there.)

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/pipeline_templates/__init__.py
git commit -m "refactor(pipeline): delete legacy canonical_blog factory (#355)"
```

---

### Task 4: delete the `cross_model_qa` stage

Remove the stage file, its plugin registration (entry-point + `_SAMPLES`), and the now-broken tests. KEEP `multi_model_qa.py` (the qa.\* atoms depend on it).

**Files:**

- Delete: `src/cofounder_agent/services/stages/cross_model_qa.py`
- Modify: `src/cofounder_agent/pyproject.toml` (remove the `cross_model_qa` entry-point)
- Modify: `src/cofounder_agent/plugins/registry.py` (remove the `_SAMPLES` row)
- Delete: `src/cofounder_agent/tests/unit/services/stages/test_cross_model_qa.py`
- Delete: `src/cofounder_agent/tests/unit/services/test_cross_model_qa_prompts.py`
- Modify: `src/cofounder_agent/tests/unit/services/test_pipeline_versions_persistence_473.py` (remove only the cross_model_qa-importing test(s))
- Modify: `src/cofounder_agent/tests/unit/services/test_lane_b_qa_critic_migration.py` (remove only the cross_model_qa-importing test(s))

- [ ] **Step 1: Delete the stage + its dedicated tests**

```bash
git rm src/cofounder_agent/services/stages/cross_model_qa.py
git rm src/cofounder_agent/tests/unit/services/stages/test_cross_model_qa.py
git rm src/cofounder_agent/tests/unit/services/test_cross_model_qa_prompts.py
```

- [ ] **Step 2: Remove the plugin registration**

- In `src/cofounder_agent/pyproject.toml`, delete the entry-point line:
  `cross_model_qa = "cofounder_agent.services.stages.cross_model_qa:CrossModelQAStage"`
- In `src/cofounder_agent/plugins/registry.py`, delete the `_SAMPLES` tuple row:
  `("stages", "services.stages.cross_model_qa", "CrossModelQAStage"),`

- [ ] **Step 3: Prune the partial-import tests**

`test_pipeline_versions_persistence_473.py` and `test_lane_b_qa_critic_migration.py` import `services.stages.cross_model_qa`. Read each file; DELETE only the test functions/classes that import or exercise the deleted stage (e.g. the ones doing `from services.stages import cross_model_qa` / `cross_model_qa._resolve_writer_model`). KEEP every test that doesn't touch the stage. If a whole file is entirely about the stage, delete the file. After editing, the files must import cleanly with no reference to `services.stages.cross_model_qa`.

Verify no references remain:
Run (cwd = `src/cofounder_agent`): `"<venv-python>" -c "print('ok')"` is not enough — instead grep:
`grep -rn "services.stages.cross_model_qa\|services.stages import cross_model_qa\|CrossModelQAStage" tests/ services/ plugins/ pyproject.toml` should return NOTHING (run via the Grep tool or `grep`). Comment-only mentions in unrelated docstrings are fine to leave.

- [ ] **Step 4: Confirm the qa.\* atoms + registry still work without the stage**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/ tests/unit/services/test_canonical_blog_spec.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass. Crucially `test_canonical_blog_spec.py::test_compiles_via_build_graph_from_spec` must still pass — it proves the canonical_blog graph_def (which has NO `stage.cross_model_qa` node) compiles with all nodes resolving after the stage is gone.

- [ ] **Step 5: Run the pruned partial-import test files to confirm they're green**

Run: `"<venv-python>" -m pytest tests/unit/services/test_pipeline_versions_persistence_473.py tests/unit/services/test_lane_b_qa_critic_migration.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: pass (collection succeeds; remaining tests green). If a remaining test depended on stage behavior now covered by `_qa_persist`, either re-point it at `_qa_persist` or remove it with a note.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(qa): delete cross_model_qa stage (superseded by qa.* atoms) (#355)"
```

---

## Self-review notes

- **Spec coverage:** completes the spec's "Cutover mechanism" step 3 (flip the default, delete the hand-coded factory + superseded stages) — the operator-chosen big-bang variant. The pipeline-state parity work (Task 1) is what makes the big-bang safe: a QA reject now writes the same four DB rows the legacy stage did, so no task is left stuck; `quality_score` is promoted and `qa_reviews` populated so `finalize_task` / auto-publish / the approval UI behave as before. The human-approval gate remains the content backstop (no auto-publish).
- **Faithful drop-in:** `qa.aggregate` becomes the QA-decision atom with the same responsibilities the stage had (approve state-keys + reject DB-writes + halt). The reject writes go through `_qa_persist` (best-effort, `update_task` first as the load-bearing status write), mirroring `cross_model_qa.py:460-556` exactly. `multi_model_qa.py` (and its `qa_pass_completed` audit) and `dev_diary` are untouched.
- **Type consistency:** `persist_qa_reject(database_service, *, task_id, reason, final_score, content, title, qa_feedback, models_used_by_phase) -> None`; `build_reject_reason(reviews, vetoed_by, final_score) -> str`; `build_qa_feedback(reviews, final_score, approved) -> str`; `qa.aggregate.run` returns `qa_final_score`/`qa_final_verdict`/`quality_score`/`qa_reviews`/`qa_rewrite_attempts` always, plus `_halt`/`_halt_reason`/`status` on reject. `quality_score`/`qa_reviews`/`qa_final_score` are declared PipelineState channels; `status` rides the DB write.
- **No placeholders:** every step has concrete code/commands/expected output. The two runtime-variable bits are the migration timestamp (Task 2) and the partial-test pruning (Task 4 Step 3, which names the exact import to remove and the grep that must come back empty).
- **Blast radius (intentional, operator-authorized):** this PR makes the graph_def path LIVE on prod (flag flip) and deletes the legacy factory + stage. With Task 1's parity, the reject/finalize/auto-publish behavior matches the legacy at the DB level. Rollback is the migration `down()` (flag→false) — but note the legacy factory is deleted, so a rollback of the flag alone would break `canonical_blog` (revert the whole PR to fully roll back). This is the accepted cost of the big-bang.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
