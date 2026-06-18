# QA Rescue Cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a critic-vetoed `canonical_blog` draft one bounded rewrite pass before it is hard-rejected, via a LangGraph rescue cycle gated by a durable attempt counter.

**Architecture:** When `qa.aggregate` produces a _rescuable_ reject (soft LLM-critic veto, or a below-threshold score with no hard veto — never a fabrication/gate veto), it emits `_goto="qa_rewrite"` instead of persisting + halting. A new `qa.rewrite` atom revises the draft, increments the durable `qa_rewrite_attempts` counter, and resets the QA review channel; a `loop`-flagged back-edge re-runs the whole QA block. The pipeline compiler (`pipeline_architect`) is taught to permit one designated cycle (edge `loop`/`branch` flags) while still rejecting accidental ones. The bound is the persisted counter: `qa.aggregate` only rescues while `attempts < qa_rewrite_max_attempts` (default 1), so the cycle terminates even across a kill-and-resume.

**Tech Stack:** Python 3.12, LangGraph 1.1.x (`StateGraph` + conditional edges + postgres checkpointer), asyncpg, pytest. Pipeline atoms under `modules/content/atoms/`; the compiler in `services/pipeline_architect.py`; state schema in `services/template_runner.py::PipelineState`.

---

## Background the engineer needs

**The graph_def path.** `canonical_blog` is a static spec (`services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF`) compiled by `services/pipeline_architect.py::build_graph_from_spec` and run by `TemplateRunner`. The compiler inserts a halt-aware conditional router after every node so a node setting `_halt=True` short-circuits to `END`. There is **no cycle mechanism today** — `_validate_spec` actively rejects any back-edge.

**The QA block.** 12 `qa.*` rail atoms (`qa_programmatic` … `qa_web_factcheck`) each append a review dict to the `qa_rail_reviews` channel (reducer: `operator.add`). `qa.aggregate` reads that list, computes a weighted score + veto decision (`_qa_rail_common.aggregate_rail_reviews`), and either approves (emit scores, continue to `seo_all_metadata`) or rejects (persist via `_qa_persist.persist_qa_reject`, set `_halt=True`).

**Critical data shapes:**

- A review dict is `{"reviewer", "approved", "score", "feedback", "provider", "advisory"}`. `aggregate_rail_reviews` returns `vetoed_by` = list of **reviewer names** (e.g. `"ollama_critic"`, `"programmatic_validator"`), NOT providers. To know if a veto is a soft critic veto you must join the reviewer name back to its review and read `provider`.
- `_qa_rail_common._CRITIC_PROVIDERS = ("anthropic", "google", "ollama")`. `provider="programmatic"` is the fabrication validator (never rescuable). Gate providers (`consistency_gate`, `vision_gate`, `web_factcheck`, `url_verifier`) are also non-critic → not rescuable.
- The vacuous-pass guard appends synthetic `"missing_required:<gate>"` strings to `vetoed_by` — these are infra vetoes, never rescuable.

**Pre-existing scaffolding (already in tree — do not re-add):**

- `PipelineState.qa_rewrite_attempts: int` (template_runner.py:430).
- `qa.aggregate` already emits `"qa_rewrite_attempts": 0` on every run (qa_aggregate.py:152) — this task changes it to a pass-through.
- Settings `content_router_qa_rewrite_max_tokens='8000'` and `content_router_qa_rewrite_timeout_seconds='240'` (settings_defaults.py:318-319) — orphaned from the deleted `cross_model_qa`; the timeout one is reused.

**Prompt convention.** No `prompts/*.yaml` is git-tracked. Atoms hold an inline `_FALLBACK` constant and a `_resolve_*_prompt()` helper that tries `get_prompt_manager().get_prompt(key, **fmt)` (the Langfuse/DB override surface) and falls back to the constant on any exception. Mirror `modules/content/atoms/review_with_critic.py`.

**Test invocation (worktree).** From repo root:

```bash
poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py -q
```

If `poetry run` is unavailable in the worktree, prefix with the repo-root venv + `PYTHONPATH=src/cofounder_agent` per the worktree-preflight note. All test commands below assume CWD = repo root.

---

## File structure

| File                                                                                | Responsibility                                                                      | New?    |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------- |
| `modules/content/atoms/_qa_rail_common.py`                                          | Add `is_rescuable_reject()` — the pure rescue-eligibility predicate                 | modify  |
| `services/template_runner.py`                                                       | Add `_merge_rail_reviews` reducer + `_goto` channel; swap `qa_rail_reviews` reducer | modify  |
| `services/pipeline_architect.py`                                                    | Exempt `loop` edges from cycle/topo checks; add `_branch_router` for `branch` edges | modify  |
| `modules/content/atoms/qa_rewrite.py`                                               | The `qa.rewrite` atom — one revision pass + counter increment + review reset        | **new** |
| `modules/content/atoms/qa_aggregate.py`                                             | Rescue dispatch: defer rescuable rejects, emit `_goto`                              | modify  |
| `services/settings_defaults.py`                                                     | Seed `qa_rewrite_max_attempts='1'`                                                  | modify  |
| `services/canonical_blog_spec.py`                                                   | Add `qa_rewrite` node + branch/loop edges (36→37 nodes)                             | modify  |
| `services/migrations/20260617_*_reseed_canonical_blog_graph_def_qa_rescue_cycle.py` | Re-seed `pipeline_templates.graph_def`                                              | **new** |
| `tests/unit/services/atoms/test_qa_rail_common.py`                                  | `is_rescuable_reject` tests                                                         | modify  |
| `tests/unit/services/test_merge_rail_reviews.py`                                    | reducer tests                                                                       | **new** |
| `tests/unit/services/test_pipeline_architect_validate.py`                           | loop-edge validation tests                                                          | modify  |
| `tests/unit/services/test_pipeline_architect_branch.py`                             | branch-router compile tests                                                         | **new** |
| `tests/unit/services/atoms/test_qa_rewrite_atom.py`                                 | `qa.rewrite` atom tests                                                             | **new** |
| `tests/unit/services/atoms/test_qa_aggregate_atom.py`                               | rescue/defer/exhaust/fabrication tests + fix 4 existing reject tests                | modify  |
| `tests/unit/services/test_canonical_blog_spec.py`                                   | rescue-wiring node/edge assertions                                                  | modify  |
| `tests/integration/test_graphdef_pipeline.py`                                       | end-to-end synthetic cycle tests                                                    | modify  |

---

## Task 1: `is_rescuable_reject()` predicate

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_qa_rail_common.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py`

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py`:

```python
from modules.content.atoms._qa_rail_common import is_rescuable_reject


@pytest.mark.unit
class TestIsRescuableReject:
    def _critic_veto(self):
        # A soft LLM-critic veto: reviewer ollama_critic, provider ollama, failed.
        return [
            {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
             "provider": "ollama", "advisory": False, "feedback": "weak intro"},
        ]

    def test_critic_only_veto_is_rescuable(self):
        reviews = self._critic_veto()
        assert is_rescuable_reject(
            reviews, ["ollama_critic"], final_score=55.0, threshold=70.0,
        ) is True

    def test_score_threshold_reject_is_rescuable(self):
        # Critic APPROVED (no veto) but the weighted score fell below the floor.
        reviews = [
            {"reviewer": "ollama_critic", "approved": True, "score": 62.0,
             "provider": "ollama", "advisory": False},
        ]
        assert is_rescuable_reject(
            reviews, [], final_score=62.0, threshold=70.0,
        ) is True

    def test_score_at_or_above_threshold_not_rescuable(self):
        # Empty veto + score >= threshold is an APPROVE, not a reject — guard
        # against calling this on an approve.
        assert is_rescuable_reject(
            [], [], final_score=90.0, threshold=70.0,
        ) is False

    def test_programmatic_veto_not_rescuable(self):
        # Fabrication veto from the programmatic validator — never rescue.
        reviews = [
            {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
             "provider": "programmatic", "advisory": False, "feedback": "fake_person"},
        ]
        assert is_rescuable_reject(
            reviews, ["programmatic_validator"], final_score=0.0, threshold=70.0,
        ) is False

    def test_gate_provider_veto_not_rescuable(self):
        # A consistency/vision/web gate veto is a hard correctness signal.
        reviews = [
            {"reviewer": "guardrails_brand", "approved": False, "score": 30.0,
             "provider": "consistency_gate", "advisory": False, "feedback": "off-brand"},
        ]
        assert is_rescuable_reject(
            reviews, ["guardrails_brand"], final_score=30.0, threshold=70.0,
        ) is False

    def test_missing_required_synthetic_veto_not_rescuable(self):
        # The vacuous-pass guard's synthetic veto — infra, not content.
        reviews = [
            {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
             "provider": "ollama", "advisory": False},
        ]
        assert is_rescuable_reject(
            reviews, ["ollama_critic", "missing_required:deepeval_g_eval"],
            final_score=55.0, threshold=70.0,
        ) is False

    def test_mixed_critic_plus_programmatic_not_rescuable(self):
        # If ANY veto is non-critic, the whole reject is non-rescuable.
        reviews = [
            {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
             "provider": "ollama", "advisory": False},
            {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
             "provider": "programmatic", "advisory": False},
        ]
        assert is_rescuable_reject(
            reviews, ["ollama_critic", "programmatic_validator"],
            final_score=27.0, threshold=70.0,
        ) is False

    def test_unknown_veto_name_not_rescuable(self):
        # A veto whose reviewer isn't in the reviews list — fail safe.
        assert is_rescuable_reject(
            [], ["ghost_reviewer"], final_score=10.0, threshold=70.0,
        ) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py::TestIsRescuableReject -q`
Expected: FAIL — `ImportError: cannot import name 'is_rescuable_reject'`.

- [ ] **Step 3: Implement `is_rescuable_reject`**

In `src/cofounder_agent/modules/content/atoms/_qa_rail_common.py`, add this function after `known_wrong_fact_rescued` (before `aggregate_rail_reviews`):

```python
def is_rescuable_reject(
    reviews: list[dict[str, Any]],
    vetoed_by: list[Any],
    *,
    final_score: float,
    threshold: float,
) -> bool:
    """Decide whether a qa.aggregate REJECT is eligible for one rewrite pass.

    Rescuable iff the reject came from a SOFT judgment a targeted revision
    could plausibly fix — never from a hard correctness gate:

    (a) Critic-only veto: ``vetoed_by`` is non-empty AND every vetoing
        reviewer resolves to a critic provider (anthropic/google/ollama). A
        ``programmatic_validator`` veto (fabrication/structure), a
        gate-provider veto (consistency/vision/web_factcheck/url), or a
        synthetic ``missing_required:*`` veto makes the reject NON-rescuable.
    (b) Score-threshold reject: ``vetoed_by == []`` (no hard veto — the
        critic approved) AND ``final_score < threshold`` (the weighted score
        fell below the floor). A rewrite can lift the score.

    Returns False for any veto that is not purely critic-sourced, and for an
    empty veto whose score already clears the threshold (i.e. an approve).
    """
    # (b) Score-threshold reject: nothing vetoed, just below the bar.
    if not vetoed_by:
        return final_score < threshold

    # (a) Critic-only veto: map each vetoing reviewer back to its provider.
    by_name = {r.get("reviewer"): r for r in reviews}
    for name in vetoed_by:
        if isinstance(name, str) and name.startswith("missing_required:"):
            return False  # vacuous-pass guard veto — infra, not content
        review = by_name.get(name)
        if review is None:
            return False  # unknown veto source — fail safe, don't rescue
        if review.get("provider") not in _CRITIC_PROVIDERS:
            return False  # programmatic / gate veto — hard correctness
    return True
```

Add `"is_rescuable_reject"` to the `__all__` list at the bottom of the file (keep it alphabetical-ish; place after `"known_wrong_fact_rescued"`):

```python
__all__ = [
    "aggregate_rail_reviews",
    "is_rescuable_reject",
    "known_wrong_fact_rescued",
    "missing_required_gates",
    "resolve_gate_states",
    "reviewer_to_dict",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py -q`
Expected: PASS (the new `TestIsRescuableReject` class + the pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_qa_rail_common.py src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py
git commit -m "feat(qa): add is_rescuable_reject predicate for the QA rescue cycle"
```

---

## Task 2: `_merge_rail_reviews` reducer + `_goto` channel

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py:353-358` (insert reducer before `PipelineState`), `:422` (swap reducer), `:519` (add `_goto`)
- Test: `src/cofounder_agent/tests/unit/services/test_merge_rail_reviews.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/test_merge_rail_reviews.py`:

```python
"""Unit tests for the qa_rail_reviews merge reducer (QA rescue cycle).

The reducer behaves like operator.add (list concat) EXCEPT it honors a reset
sentinel {"__reset__": True} emitted by qa.rewrite, which clears stale
first-pass reviews before the second QA pass re-runs."""

from __future__ import annotations

import pytest

from services.template_runner import _merge_rail_reviews


@pytest.mark.unit
class TestMergeRailReviews:
    def test_normal_concat(self):
        existing = [{"reviewer": "a"}]
        incoming = [{"reviewer": "b"}]
        assert _merge_rail_reviews(existing, incoming) == [
            {"reviewer": "a"}, {"reviewer": "b"},
        ]

    def test_empty_incoming_returns_existing(self):
        existing = [{"reviewer": "a"}]
        assert _merge_rail_reviews(existing, []) == [{"reviewer": "a"}]

    def test_reset_sentinel_clears_existing(self):
        existing = [{"reviewer": "a"}, {"reviewer": "b"}]
        incoming = [{"__reset__": True}]
        # Sentinel clears the prior reviews AND is stripped from the result.
        assert _merge_rail_reviews(existing, incoming) == []

    def test_reset_sentinel_strips_only_sentinel_keeps_rest(self):
        existing = [{"reviewer": "old"}]
        incoming = [{"__reset__": True}, {"reviewer": "fresh"}]
        assert _merge_rail_reviews(existing, incoming) == [{"reviewer": "fresh"}]

    def test_append_after_reset_accumulates_fresh(self):
        # Simulates: qa.rewrite resets -> [], then a rail appends one review.
        after_reset = _merge_rail_reviews([{"reviewer": "stale"}], [{"__reset__": True}])
        assert after_reset == []
        assert _merge_rail_reviews(after_reset, [{"reviewer": "fresh"}]) == [
            {"reviewer": "fresh"},
        ]
```

Also create `src/cofounder_agent/tests/unit/services/test_pipeline_state_goto_channel.py`:

```python
"""Guard: _goto is a declared PipelineState channel (QA rescue cycle).

LangGraph silently drops state updates whose keys are not declared in the
TypedDict schema (poindexter#753). qa.aggregate emits _goto to drive the
rescue branch router, so it MUST be a declared channel."""

from __future__ import annotations

import pytest

from services.template_runner import PipelineState


@pytest.mark.unit
def test_goto_is_declared_channel():
    assert "_goto" in PipelineState.__annotations__
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_merge_rail_reviews.py src/cofounder_agent/tests/unit/services/test_pipeline_state_goto_channel.py -q`
Expected: FAIL — `ImportError: cannot import name '_merge_rail_reviews'` and the `_goto` assertion fails.

- [ ] **Step 3a: Add the `_merge_rail_reviews` reducer**

In `src/cofounder_agent/services/template_runner.py`, insert this function in the "State shape" section, immediately before `class PipelineState` (i.e. after the `# State shape` comment block at line ~355):

```python
def _merge_rail_reviews(existing: list, incoming: list) -> list:
    """Reducer for the ``qa_rail_reviews`` channel.

    Behaves like ``operator.add`` (list concat so parallel rail atoms can
    each append) EXCEPT it honors a reset sentinel ``{"__reset__": True}``.
    ``qa.rewrite`` emits the sentinel before the rescue cycle's second QA
    pass so stale first-pass reviews (which carry the now-addressed veto)
    don't accumulate and re-reject. When the sentinel is present in
    ``incoming``, the prior reviews are dropped and only the non-sentinel
    elements of ``incoming`` survive. Backward-compatible: nothing else
    emits a dict element with ``__reset__``.
    """
    for item in incoming:
        if isinstance(item, dict) and item.get("__reset__"):
            return [
                x for x in incoming
                if not (isinstance(x, dict) and x.get("__reset__"))
            ]
    return list(existing) + list(incoming)
```

- [ ] **Step 3b: Swap the `qa_rail_reviews` reducer**

In `src/cofounder_agent/services/template_runner.py`, change the `qa_rail_reviews` annotation (line ~422) from:

```python
    qa_rail_reviews: Annotated[list, operator.add]
```

to:

```python
    # qa_rail_reviews uses _merge_rail_reviews (not bare operator.add) so the
    # QA rescue cycle's qa.rewrite atom can emit a {"__reset__": True} sentinel
    # to clear stale first-pass reviews before the second QA pass. Parallel
    # rail atoms still append concurrently (the reducer concats when no
    # sentinel is present).
    qa_rail_reviews: Annotated[list, _merge_rail_reviews]
```

- [ ] **Step 3c: Add the `_goto` channel**

In `src/cofounder_agent/services/template_runner.py`, in the "Halt + approval-gate channels" block, after `_halt_reason: str` (line ~519), add:

```python
    # _goto (QA rescue cycle): qa.aggregate sets this to "qa_rewrite" on a
    # rescuable reject so build_graph_from_spec's branch router routes to the
    # qa.rewrite node instead of halting/continuing. Cleared to "" on approve
    # and on hard reject. Declared as a last-value channel so it survives the
    # graph_def adapter (undeclared keys are dropped on the graph_def path).
    _goto: str
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_merge_rail_reviews.py src/cofounder_agent/tests/unit/services/test_pipeline_state_goto_channel.py -q`
Expected: PASS.

- [ ] **Step 5: Run the existing QA atom suite (no regression from the reducer swap)**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_registry.py -q`
Expected: PASS (the reducer is concat-equivalent when no sentinel is present).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/tests/unit/services/test_merge_rail_reviews.py src/cofounder_agent/tests/unit/services/test_pipeline_state_goto_channel.py
git commit -m "feat(pipeline): qa_rail_reviews reset-aware reducer + _goto state channel"
```

---

## Task 3: `_validate_spec` — exempt `loop` edges

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py:482-486` (`_has_cycle` adjacency) and `:507-510` (Kahn topo-sort)
- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py`

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py`:

```python
def test_loop_flagged_back_edge_validates():
    # a -> b -> c, with c -> a flagged "loop": the designated rescue cycle.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c")}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na", "loop": True},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is True, errors


def test_unflagged_back_edge_still_errors():
    # Same shape but WITHOUT the loop flag — an accidental cycle must fail loud.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c")}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na"},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("cycle" in e.lower() for e in errors), errors


def test_loop_edge_does_not_drop_downstream_require_check():
    # The loop edge must not inflate the loopback target's indegree and silently
    # drop the whole chain from the requires-reachability pass. nc requires "k"
    # which nothing produces -> the check must still fire and error on nc.
    catalog = {"a": _meta("a"), "b": _meta("b"), "c": _meta("c", requires=("k",))}
    spec = _spec(
        [{"id": "na", "atom": "a"}, {"id": "nb", "atom": "b"}, {"id": "nc", "atom": "c"}],
        [
            {"from": "na", "to": "nb"},
            {"from": "nb", "to": "nc"},
            {"from": "nc", "to": "na", "loop": True},
            {"from": "nc", "to": "END"},
        ],
    )
    with patch.object(pipeline_architect, "get_atom_meta", _fake_get_atom_meta(catalog)):
        ok, errors = pipeline_architect._validate_spec(spec, seed_keys=set())
    assert ok is False
    assert any("nc" in e and "k" in e for e in errors), errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py -q -k "loop or back_edge"`
Expected: FAIL — `test_loop_flagged_back_edge_validates` fails (cycle error) and `test_loop_edge_does_not_drop_downstream_require_check` fails (no error raised because the chain dropped from `order`).

- [ ] **Step 3a: Exempt `loop` edges from `_has_cycle`**

In `src/cofounder_agent/services/pipeline_architect.py`, change the cycle-detection adjacency build (lines ~482-486) from:

```python
        adj: dict[str, list[str]] = {nid: [] for nid in seen_ids}
        for e in edges:
            src, dst = e["from"], e["to"]
            if dst != "END":
                adj.setdefault(src, []).append(dst)
        if _has_cycle(adj):
```

to:

```python
        adj: dict[str, list[str]] = {nid: [] for nid in seen_ids}
        for e in edges:
            # A "loop"-flagged edge is the one designated rescue back-edge
            # (qa.rewrite -> qa.programmatic). Skip it in cycle detection so
            # the deliberate cycle validates; unflagged back-edges still error.
            if e.get("loop"):
                continue
            src, dst = e["from"], e["to"]
            if dst != "END":
                adj.setdefault(src, []).append(dst)
        if _has_cycle(adj):
```

- [ ] **Step 3b: Exempt `loop` edges from the Kahn topo-sort**

In `src/cofounder_agent/services/pipeline_architect.py`, change the indegree build (lines ~507-510) from:

```python
        for e in edges:
            if e.get("to") != "END" and e.get("from") in seen_ids and e.get("to") in seen_ids:
                adj2[e["from"]].append(e["to"])
                indeg[e["to"]] += 1
```

to:

```python
        for e in edges:
            # Skip the designated rescue back-edge: counting it would inflate
            # the loopback target's indegree so it never reaches 0, silently
            # dropping it and its whole downstream chain from the requires
            # reachability check below. The cycle itself is permitted (3a).
            if e.get("loop"):
                continue
            if e.get("to") != "END" and e.get("from") in seen_ids and e.get("to") in seen_ids:
                adj2[e["from"]].append(e["to"])
                indeg[e["to"]] += 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py -q`
Expected: PASS (new loop tests + all pre-existing validate tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py
git commit -m "feat(pipeline): exempt loop-flagged edges from DAG validation"
```

---

## Task 4: `build_graph_from_spec` — branch router for `branch` edges

**Files:**

- Modify: `src/cofounder_agent/services/pipeline_architect.py:758-804` (edge-wiring loop)
- Test: `src/cofounder_agent/tests/unit/services/test_pipeline_architect_branch.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/test_pipeline_architect_branch.py`:

```python
"""Branch-router compile tests (QA rescue cycle).

A node with a "branch": true out-edge gets a _goto-aware conditional router:
- _halt=True   -> END (halt always wins)
- _goto==target -> the branch target (the rescue node)
- otherwise    -> the default forward target
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from plugins.atom import AtomMeta
from services import pipeline_architect
from services.template_runner import PipelineState


def _meta(name: str, *, requires: tuple = (), produces: tuple = ()) -> AtomMeta:
    return AtomMeta(
        name=name, type="atom", version="1.0.0", description=name,
        requires=requires, produces=produces,
    )


def _compile_branch_graph(log: list[str], *, gate_fn):
    """gate -> (branch:rescue | default:cont); rescue -> END; cont -> END."""

    async def rescue_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("rescue")
        return {}

    async def cont_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("cont")
        return {}

    catalog = {
        "t.gate": _meta("t.gate"), "t.rescue": _meta("t.rescue"), "t.cont": _meta("t.cont"),
    }
    callables = {"t.gate": gate_fn, "t.rescue": rescue_fn, "t.cont": cont_fn}
    spec = {
        "name": "branch_test",
        "entry": "gate",
        "nodes": [
            {"id": "gate", "atom": "t.gate"},
            {"id": "rescue", "atom": "t.rescue"},
            {"id": "cont", "atom": "t.cont"},
        ],
        "edges": [
            {"from": "gate", "to": "rescue", "branch": True},
            {"from": "gate", "to": "cont"},
            {"from": "rescue", "to": "END"},
            {"from": "cont", "to": "END"},
        ],
    }
    with (
        patch.object(pipeline_architect, "get_atom_meta", lambda n: catalog.get(n)),
        patch.object(pipeline_architect, "get_atom_callable", lambda n: callables.get(n)),
        patch("plugins.registry.get_core_samples", return_value={"stages": []}),
    ):
        return pipeline_architect.build_graph_from_spec(spec, pool=None).compile()


@pytest.mark.unit
class TestBranchRouter:
    async def test_goto_routes_to_branch_target(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            return {"_goto": "rescue"}

        compiled = _compile_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "t1"}})
        assert log == ["gate", "rescue"], log

    async def test_empty_goto_routes_to_default(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            return {"_goto": ""}

        compiled = _compile_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "t2"}})
        assert log == ["gate", "cont"], log

    async def test_halt_beats_goto(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            # Even with _goto set, _halt must win and route to END.
            return {"_halt": True, "_goto": "rescue"}

        compiled = _compile_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "t3"}})
        assert log == ["gate"], log
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_pipeline_architect_branch.py -q`
Expected: FAIL — the compiler currently treats `gate`'s two out-edges as a parallel fan-out (`_halt_router_multi`), so both `rescue` and `cont` run; `test_goto_routes_to_branch_target` and `test_empty_goto_routes_to_default` fail.

- [ ] **Step 3a: Add the `_branch_router` factory**

In `src/cofounder_agent/services/pipeline_architect.py`, inside `build_graph_from_spec`, add this router factory next to `_halt_router_single` / `_halt_router_multi` (after `_halt_router_multi`, ~line 790):

```python
    def _branch_router(
        branch_target: Any, default_target: Any,
    ) -> Callable[[PipelineState], Any]:
        """Conditional router for a node with a ``branch``-flagged out-edge.

        Priority: ``_halt`` (-> END) > ``_goto == branch_target`` (-> the
        branch/rescue node) > the default forward target. This is how
        qa.aggregate routes a deferred-rescue reject to qa.rewrite while a
        normal approve/exhausted-reject continues down the default edge (or
        halts).
        """

        def _route(state: PipelineState) -> Any:
            if state.get("_halt"):
                return END
            if state.get("_goto") == branch_target:
                return branch_target
            return default_target

        _route.__name__ = (
            f"branch_to_{branch_target}_or_"
            f"{'END' if default_target is END else default_target}"
        )
        return _route
```

- [ ] **Step 3b: Detect branch sources and wire them**

In `src/cofounder_agent/services/pipeline_architect.py`, the edge-grouping + wiring section currently reads (lines ~757-804):

```python
    # Group edges by source so we can attach conditional edges that
    # also respect ``_halt`` short-circuit.
    out_by_src: dict[str, list[str]] = {}
    for e in edges:
        out_by_src.setdefault(e["from"], []).append(e["to"])

    def _halt_router_single(target: Any) -> Callable[[PipelineState], Any]:
        ...

    def _halt_router_multi(targets: list[Any]) -> Callable[[PipelineState], Any]:
        ...

    for src, dsts in out_by_src.items():
        resolved = [_resolve(d) for d in dsts]
        # Single-target case: simple halt-aware edge.
        if len(resolved) == 1:
            target = resolved[0]
            mapping = {target: target} if target is END else {target: target, END: END}
            g.add_conditional_edges(src, _halt_router_single(target), mapping)
            continue
        # Multi-target case: parallel fan-out. Build a mapping that
        # includes every target plus END for the halt path.
        mapping = {t: t for t in resolved}
        mapping[END] = END
        g.add_conditional_edges(src, _halt_router_multi(resolved), mapping)
```

Make two edits.

First, add a branch pre-scan right after the `out_by_src` build (after the `for e in edges: out_by_src...` loop, before `def _halt_router_single`):

```python
    # Pre-scan for branch edges: a source with a "branch": true out-edge gets
    # a _goto-aware conditional router (see _branch_router) instead of the
    # default halt/fan-out routers. Maps source node id -> branch target id.
    branch_by_src: dict[str, str] = {}
    for e in edges:
        if e.get("branch"):
            branch_by_src[e["from"]] = e["to"]
```

Second, add a branch case at the TOP of the `for src, dsts in out_by_src.items():` loop body (before the `resolved = ...` line):

```python
    for src, dsts in out_by_src.items():
        # Branch case: a _goto-aware conditional router. Exactly one of this
        # source's out-edges carries "branch": true; the other (non-branch,
        # non-loop) edge is the default forward target.
        if src in branch_by_src:
            branch_target = _resolve(branch_by_src[src])
            defaults = [d for d in dsts if d != branch_by_src[src]]
            default_target = _resolve(defaults[0]) if defaults else END
            mapping = {branch_target: branch_target, END: END}
            mapping[default_target] = default_target
            g.add_conditional_edges(
                src, _branch_router(branch_target, default_target), mapping,
            )
            continue
        resolved = [_resolve(d) for d in dsts]
        # Single-target case: simple halt-aware edge.
        if len(resolved) == 1:
            ...
```

(Leave the existing single-target and multi-target branches unchanged below the `continue`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_pipeline_architect_branch.py src/cofounder_agent/tests/unit/services/test_pipeline_architect_halt.py -q`
Expected: PASS (new branch tests + the pre-existing halt tests — the halt path is unchanged for non-branch sources).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/pipeline_architect.py src/cofounder_agent/tests/unit/services/test_pipeline_architect_branch.py
git commit -m "feat(pipeline): _goto branch router for conditional rescue edges"
```

---

## Task 5: `qa.rewrite` atom

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/qa_rewrite.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_rewrite_atom.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/atoms/test_qa_rewrite_atom.py`:

```python
"""Unit tests for the qa.rewrite atom (QA rescue cycle)."""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_rewrite
from services.site_config import SiteConfig


def _site_config():
    # pipeline_writer_model lets resolve_local_model return without raising.
    return SiteConfig(initial_config={"pipeline_writer_model": "test-writer"})


@pytest.mark.unit
class TestQaRewriteAtom:
    def test_meta(self):
        m = qa_rewrite.ATOM_META
        assert m.name == "qa.rewrite"
        assert "content" in m.requires
        assert "qa_rewrite_attempts" in m.requires
        assert set(m.produces) >= {"content", "qa_rewrite_attempts", "qa_rail_reviews"}

    async def test_successful_revision(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            # The prompt must carry the critic feedback + the draft.
            assert "weak intro" in prompt
            assert "ORIGINAL DRAFT" in prompt or "CURRENT DRAFT" in prompt
            return "# Revised\n\nMuch better body now.\n"

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)

        state = {
            "task_id": "t1",
            "content": "# Draft\n\nweak body.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak intro"},
                {"reviewer": "ragas_eval", "approved": True, "score": 88.0,
                 "provider": "ollama", "advisory": True, "feedback": "fine"},
            ],
        }
        out = await qa_rewrite.run(state)
        assert out["content"] == "# Revised\n\nMuch better body now.\n"
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]
        assert out["qa_known_wrong_fact_only"] is False

    async def test_only_failing_nonadvisory_feedback_used(self, monkeypatch):
        seen = {}

        async def _fake_chat(prompt, **kw):
            seen["prompt"] = prompt
            return "revised body"

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t2",
            "content": "draft",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "FIX_THIS"},
                {"reviewer": "ragas_eval", "approved": False, "score": 40.0,
                 "provider": "ollama", "advisory": True, "feedback": "ADVISORY_NOISE"},
                {"reviewer": "deepeval_g_eval", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False, "feedback": "PASSED_NOISE"},
            ],
        }
        await qa_rewrite.run(state)
        assert "FIX_THIS" in seen["prompt"]
        assert "ADVISORY_NOISE" not in seen["prompt"]   # advisory excluded
        assert "PASSED_NOISE" not in seen["prompt"]      # passing excluded

    async def test_empty_writer_output_degrades_to_reject(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            return "   "  # whitespace -> treated as empty

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t3",
            "content": "# Original\n\nkeep me.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        out = await qa_rewrite.run(state)
        # Degrade-to-reject: no new content (prior draft kept), counter still
        # burned so the loop terminates, reviews reset so the re-run is clean.
        assert "content" not in out
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]

    async def test_writer_exception_degrades_to_reject(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            raise RuntimeError("dispatch boom")

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t4",
            "content": "# Original\n\nkeep me.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        out = await qa_rewrite.run(state)
        assert "content" not in out
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]

    async def test_no_content_or_site_config_burns_attempt(self):
        out = await qa_rewrite.run({"qa_rewrite_attempts": 0, "content": ""})
        assert "content" not in out
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_rewrite_atom.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.content.atoms.qa_rewrite'`.

- [ ] **Step 3: Implement the atom**

Create `src/cofounder_agent/modules/content/atoms/qa_rewrite.py`:

```python
"""qa.rewrite — one bounded revision pass for a critic-vetoed draft.

Part of the canonical_blog QA rescue cycle. When qa.aggregate defers a
RESCUABLE reject (a soft LLM-critic veto, or a below-threshold score with no
hard veto — see _qa_rail_common.is_rescuable_reject; NEVER a fabrication or
gate veto), it emits ``_goto="qa_rewrite"`` and the branch router routes here
instead of halting.

This atom:
  1. Reads ``content`` + the failing critic feedback from ``qa_rail_reviews``.
  2. Calls the writer model with a targeted "revise to fix these issues" prompt.
  3. Returns the revised ``content``, increments ``qa_rewrite_attempts``, and
     emits the ``qa_rail_reviews`` reset sentinel ``[{"__reset__": True}]`` so
     the second QA pass starts from an empty review list (the _merge_rail_reviews
     reducer honors the sentinel). Without the reset, the stale first-pass veto
     would carry over and guarantee a re-reject.

A ``loop``-flagged edge (qa_rewrite -> qa_programmatic) re-runs the whole QA
block. The bound is the durable ``qa_rewrite_attempts`` counter: qa.aggregate
only rescues while ``attempts < qa_rewrite_max_attempts`` (default 1), so the
cycle runs at most N times — even across a kill-and-resume, because the counter
lives in the LangGraph postgres checkpoint.

Degrade-to-reject: if the writer errors or returns empty, keep the prior
content unchanged (omit the ``content`` key) and STILL increment the counter —
the next qa.aggregate pass sees ``attempts == max``, declines to rescue, and
the original reject stands. A finding is emitted for observability. The loop
always terminates.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.rewrite",
    type="atom",
    version="1.0.0",
    description=(
        "One bounded revision pass for a critic-vetoed draft; resets the QA "
        "review channel and increments the durable rescue-attempt counter."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="the vetoed draft"),
        FieldSpec(name="qa_rail_reviews", type="list[dict]",
                  description="failing reviews — source of the critic feedback"),
        FieldSpec(name="qa_rewrite_attempts", type="int",
                  description="prior rescue attempts"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="revised draft"),
        FieldSpec(name="qa_rewrite_attempts", type="int",
                  description="incremented attempt counter"),
        FieldSpec(name="qa_rail_reviews", type="list[dict]",
                  description="reset sentinel clearing stale first-pass reviews"),
    ),
    requires=("content", "qa_rail_reviews", "qa_rewrite_attempts"),
    produces=("content", "qa_rewrite_attempts", "qa_rail_reviews"),
    capability_tier="standard",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    parallelizable=False,
)

# Reset sentinel for the qa_rail_reviews reducer (services.template_runner.
# _merge_rail_reviews). Emitting this clears the stale first-pass reviews so
# the second QA pass scores the revised draft from scratch.
_REVIEW_RESET = [{"__reset__": True}]

_REVISE_PROMPT_KEY = "atoms.qa_rewrite.revise_prompt"

_REVISE_PROMPT_FALLBACK = """\
You are revising a draft article that an editorial critic flagged for specific, \
fixable issues. Apply ONLY the fixes the critic asked for. Preserve the \
article's structure, headings, length, links, citations, and voice. Do not add \
new sections or remove existing ones unless a fix requires it. Return the \
COMPLETE revised article in Markdown — body only, no preamble, no commentary, \
no JSON envelope.

CRITIC FEEDBACK TO ADDRESS:
{feedback}

ORIGINAL DRAFT:
{content}
"""


def _resolve_revise_prompt(*, content: str, feedback: str) -> str:
    """Pull the revise prompt via UnifiedPromptManager (Langfuse/DB override
    surface), falling back to the inline constant. Mirrors review_with_critic.
    Per feedback_prompts_must_be_db_configurable."""
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _REVISE_PROMPT_KEY, content=content, feedback=feedback,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[qa.rewrite] prompt lookup for %r failed (%s) — inline fallback",
            _REVISE_PROMPT_KEY, exc,
        )
        return _REVISE_PROMPT_FALLBACK.format(content=content, feedback=feedback)


def _failing_critic_feedback(reviews: list[dict[str, Any]]) -> str:
    """Collect the actionable feedback: non-advisory FAILING reviews only.
    Advisory rails and passing reviews carry no veto to fix."""
    notes = [
        str(r.get("feedback") or "").strip()
        for r in reviews
        if not r.get("approved")
        and not r.get("advisory")
        and str(r.get("feedback") or "").strip()
    ]
    if not notes:
        return "- (no specific feedback; tighten weak claims and improve clarity)"
    return "\n".join(f"- {n}" for n in notes)


def _emit_empty_finding(model: str) -> None:
    """Best-effort observability when the revise call yields nothing usable."""
    try:
        from utils.findings import emit_finding
        emit_finding(
            source="modules.content.atoms.qa_rewrite",
            kind="qa_rewrite_empty_revision",
            title=f"QA rescue revise model {model!r} returned empty — reject stands",
            body=(
                f"qa.rewrite called the writer ({model!r}) to revise a "
                f"critic-vetoed draft but got empty/failed output. The prior "
                f"draft was kept and the attempt counter burned, so the next "
                f"qa.aggregate pass declines to rescue and the original reject "
                f"stands. Verify writer-model health if this recurs."
            ),
            severity="warn",
            dedup_key=f"qa_rewrite_empty_revision:{model}",
            extra={"model": model},
        )
    except Exception:  # noqa: BLE001 — finding emission must never raise here
        pass


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from services.llm_text import ollama_chat_text, resolve_local_model

    content = (state.get("content") or "").strip()
    attempts = int(state.get("qa_rewrite_attempts") or 0)
    site_config = state.get("site_config")

    # Degrade-to-reject guard: nothing to work with → burn the attempt so the
    # loop terminates and the original reject stands. Reset reviews so the
    # re-run (it won't rescue again, but a re-run path stays clean) is empty.
    if not content or site_config is None:
        return {
            "qa_rewrite_attempts": attempts + 1,
            "qa_rail_reviews": list(_REVIEW_RESET),
        }

    reviews = state.get("qa_rail_reviews") or []
    feedback = _failing_critic_feedback(reviews)
    pool = getattr(state.get("database_service"), "pool", None)
    task_id = state.get("task_id")
    # model=None chains pipeline_writer_model → cost_tier.standard.model.
    model = resolve_local_model(model=None, site_config=site_config)
    revise_prompt = _resolve_revise_prompt(content=content, feedback=feedback)

    revised = ""
    try:
        raw = await ollama_chat_text(
            revise_prompt,
            model=model,
            site_config=site_config,
            pool=pool,
            # Reuse the orphaned cross_model_qa timeout setting (240s default).
            timeout_setting="content_router_qa_rewrite_timeout_seconds",
            timeout_default=240.0,
            task_id=task_id,
            phase="qa_rewrite",
        )
        revised = (raw or "").strip()
    except Exception as exc:  # noqa: BLE001 — a failed revise must not crash the graph
        logger.warning(
            "[qa.rewrite] revise call failed (%s) — keeping prior draft", exc,
        )
        revised = ""

    if not revised:
        _emit_empty_finding(model)
        return {
            "qa_rewrite_attempts": attempts + 1,
            "qa_rail_reviews": list(_REVIEW_RESET),
        }

    logger.info(
        "[qa.rewrite] revised draft for task=%s (attempt %d) — %d chars",
        str(task_id or "?")[:8], attempts + 1, len(revised),
    )
    return {
        "content": revised,
        "qa_rewrite_attempts": attempts + 1,
        "qa_rail_reviews": list(_REVIEW_RESET),
        # The revised draft is fresh — clear the #661 known_wrong_fact flag so
        # the second qa.programmatic pass re-derives it from the new content.
        "qa_known_wrong_fact_only": False,
    }


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_rewrite_atom.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the atom auto-registers**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_registry.py -q`
Expected: PASS. (The registry walks `modules/content/atoms/*.py`; `qa_rewrite.py` with `ATOM_META.name="qa.rewrite"` + `run` is discovered automatically.)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/qa_rewrite.py src/cofounder_agent/tests/unit/services/atoms/test_qa_rewrite_atom.py
git commit -m "feat(qa): qa.rewrite atom — one bounded revision pass + review reset"
```

---

## Task 6: `qa.aggregate` rescue dispatch

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/qa_aggregate.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py`

### 6a — Fix the 4 existing reject tests that now rescue-by-default

With `qa_rewrite_max_attempts` defaulting to 1, four existing tests that assert a _rescuable_ reject (critic veto / score-threshold) → `_halt` would now see a _defer_ instead. Disable rescue in exactly those four by giving the platform `config={"qa_rewrite_max_attempts": "0"}`. (The other reject tests use a `programmatic`/`consistency_gate` provider or `missing_required:*` veto, which is non-rescuable → they stay green unchanged.)

- [ ] **Step 1: Patch the 4 tests**

In `src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py`:

1. `test_reject_halts` — change `FakePlatform()` to `FakePlatform(config={"qa_rewrite_max_attempts": "0"})`.
2. `test_missing_reviews_key_rejects_at_zero` — change `FakePlatform()` to `FakePlatform(config={"qa_rewrite_max_attempts": "0"})`.
3. `test_reject_does_db_writes_and_halts` — change `"platform": FakePlatform(),` to `"platform": FakePlatform(config={"qa_rewrite_max_attempts": "0"}),`.
4. `test_reject_without_db_service_still_halts` — change `FakePlatform()` to `FakePlatform(config={"qa_rewrite_max_attempts": "0"})`.

Add a one-line comment above each: `# rescue disabled (max_attempts=0) — this test pins the hard-reject/halt path`.

### 6b — Implement the rescue dispatch (TDD)

- [ ] **Step 2: Write the failing tests**

Append a new test class to `src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py`:

```python
@pytest.mark.unit
class TestQaAggregateRescueDispatch:
    """QA rescue cycle: a rescuable reject defers to qa.rewrite (emits _goto,
    no _halt, no DB persist) while attempts remain; a non-rescuable or
    exhausted reject hard-rejects as before."""

    def _critic_reject_state(self, **extra):
        # ollama_critic (provider ollama) fails non-advisory, score below 70.
        state = {
            "platform": FakePlatform(),  # default max_attempts -> 1 (rescue on)
            "task_id": "task-r",
            "content": "the body",
            "title": "T",
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak intro"},
            ],
        }
        state.update(extra)
        return state

    async def test_critic_veto_defers_to_rewrite(self):
        out = await qa_aggregate.run(self._critic_reject_state())
        assert out["_goto"] == "qa_rewrite"
        assert "_halt" not in out
        assert "status" not in out           # no reject persistence
        # The attempt counter is passed through unchanged (qa.rewrite bumps it).
        assert out["qa_rewrite_attempts"] == 0

    async def test_rescue_dispatch_does_no_db_writes(self, monkeypatch):
        # persist_qa_reject must NOT be called on a deferred rescue.
        called = {"persist": False}

        async def _spy_persist(*a, **kw):
            called["persist"] = True

        monkeypatch.setattr(
            "modules.content.atoms._qa_persist.persist_qa_reject", _spy_persist,
        )
        out = await qa_aggregate.run(self._critic_reject_state())
        assert out["_goto"] == "qa_rewrite"
        assert called["persist"] is False

    async def test_rescue_dispatch_omits_qa_reviews(self):
        # qa_reviews uses operator.add; emitting it on the deferred pass would
        # concat stale+fresh on the terminal pass. The rescue path must omit it.
        out = await qa_aggregate.run(self._critic_reject_state())
        assert "qa_reviews" not in out

    async def test_rescue_emits_qa_rescue_scheduled_audit(self):
        fake = FakePlatform()
        state = self._critic_reject_state(platform=fake)
        await qa_aggregate.run(state)
        events = [w for w in fake.audit.writes_bg if w["event_type"] == "qa_rescue_scheduled"]
        assert len(events) == 1
        assert events[0]["details"]["attempt"] == 1
        # The terminal qa_pass_completed is NOT emitted on a deferred rescue.
        passes = [w for w in fake.audit.writes_bg if w["event_type"] == "qa_pass_completed"]
        assert passes == []

    async def test_score_threshold_reject_defers(self):
        # Critic APPROVED, but the weighted score (62) is below the 70 floor.
        state = {
            "platform": FakePlatform(),
            "task_id": "task-s",
            "content": "body", "title": "T",
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 62.0,
                 "provider": "ollama", "advisory": False, "feedback": "ok-ish"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_goto"] == "qa_rewrite"
        assert "_halt" not in out

    async def test_exhausted_attempts_hard_rejects(self, monkeypatch):
        # attempts already == max(1): no more rescue → hard reject + halt + persist.
        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = self._critic_reject_state(
            qa_rewrite_attempts=1, database_service=db, models_used_by_phase={},
        )
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert out["_goto"] == ""
        assert out["status"] == "rejected"
        assert db.update_task_calls[0][1]["status"] == "rejected"

    async def test_fabrication_veto_never_rescues(self, monkeypatch):
        # programmatic_validator veto (fabrication) is NON-rescuable even with
        # attempts available → hard reject immediately, no _goto to rewrite.
        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, fields): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)
        db = _DB2()
        state = {
            "platform": FakePlatform(),       # rescue ON (max 1)
            "task_id": "task-fab",
            "content": "body", "title": "T",
            "models_used_by_phase": {},
            "database_service": db,
            "qa_rail_reviews": [
                {"reviewer": "programmatic_validator", "approved": False, "score": 0.0,
                 "provider": "programmatic", "advisory": False, "feedback": "fake_person"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert out["_goto"] == ""
        assert out.get("status") == "rejected"

    async def test_approve_clears_goto(self):
        state = {
            "platform": FakePlatform(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False, "feedback": "great"},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert out["_goto"] == ""
        assert "_halt" not in out
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py::TestQaAggregateRescueDispatch -q`
Expected: FAIL — `KeyError: '_goto'` / rescue path not implemented.

- [ ] **Step 4a: Add imports + the `_max_attempts` helper**

In `src/cofounder_agent/modules/content/atoms/qa_aggregate.py`, extend the `_qa_rail_common` import (line 23-27) to include `is_rescuable_reject`:

```python
from modules.content.atoms._qa_rail_common import (
    aggregate_rail_reviews,
    is_rescuable_reject,
    missing_required_gates,
    resolve_gate_states,
)
```

Add the helper after `_weight` (after line 60):

```python
def _max_attempts(config: Any, default: int = 1) -> int:
    """Read qa_rewrite_max_attempts (clamped to [0,3]). 0 disables the rescue
    cycle; None-tolerant for the no-platform path (returns the default)."""
    if config is None:
        return default
    try:
        raw = int(float(config.get("qa_rewrite_max_attempts", default)))
    except (TypeError, ValueError):
        return default
    return max(0, min(3, raw))
```

- [ ] **Step 4b: Insert the rescue dispatch + pass-through counter + `_goto`**

In `qa_aggregate.py::run`, the code computes `promoted` (line ~142) then builds `out` (line ~144). Insert the rescue check **between** `promoted = max(early, float(final_score))` and the `out: dict[str, Any] = {` line:

```python
    promoted = max(early, float(final_score))

    # --- QA rescue cycle dispatch (deferred reject) ---------------------------
    # A rescuable reject (soft critic veto, or below-threshold score with no
    # hard veto — never fabrication/gate/missing_required) gets ONE revision
    # pass before it is persisted. Defer to qa.rewrite via _goto while the
    # durable attempt counter is under budget. The branch router
    # (build_graph_from_spec._branch_router) routes _goto=="qa_rewrite" to the
    # rewrite node; qa.rewrite increments qa_rewrite_attempts and resets the
    # review channel, then the loop edge re-runs the QA block.
    attempts = int(state.get("qa_rewrite_attempts") or 0)
    max_attempts = _max_attempts(config)
    if (
        not approved
        and attempts < max_attempts
        and is_rescuable_reject(
            reviews, result.get("vetoed_by", []),
            final_score=float(final_score), threshold=float(threshold),
        )
    ):
        _platform = state.get("platform")
        if _platform is not None:
            try:
                _platform.audit.write_bg(
                    "qa_rescue_scheduled",
                    source="qa.aggregate",
                    details={
                        "final_score": round(float(final_score), 2),
                        "threshold": float(threshold),
                        "attempt": attempts + 1,
                        "max_attempts": max_attempts,
                        "vetoed_by": list(result.get("vetoed_by", [])),
                    },
                    task_id=(str(state.get("task_id")) or None) if state.get("task_id") else None,
                    severity="info",
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[qa.aggregate] qa_rescue_scheduled audit skipped: %s", exc)
        # Defer: route to qa.rewrite. NO persist, NO _halt, NO qa_pass_completed
        # (the terminal pass emits that). Omit qa_reviews — its operator.add
        # reducer would concat stale+fresh on the terminal pass; the terminal
        # pass populates it once. Pass the counter through unchanged (qa.rewrite
        # bumps it).
        return {
            "qa_final_score": final_score,
            "qa_final_verdict": result["qa_final_verdict"],
            "qa_rewrite_attempts": attempts,
            "vetoed_by": result.get("vetoed_by", []),
            "_goto": "qa_rewrite",
        }
    # --- end rescue dispatch --------------------------------------------------

    out: dict[str, Any] = {
```

- [ ] **Step 4c: Make the counter a pass-through + clear `_goto` on terminal paths**

In the same `out` dict, change line 152 from `"qa_rewrite_attempts": 0,` to the pass-through value and add `_goto`:

```python
        "qa_reviews": list(reviews),
        "qa_rewrite_attempts": attempts,
        # Clear any rescue routing on the terminal (approve / hard-reject) pass
        # so the branch router continues to seo_all_metadata (or halts on
        # reject — _halt is checked first in _branch_router).
        "_goto": "",
        # Surface veto reasons for callers and tests; empty list on approve.
        "vetoed_by": result.get("vetoed_by", []),
```

(The hard-reject path below still adds `out["_halt"] = True` / `out["status"] = "rejected"`; `_branch_router` checks `_halt` before `_goto`, so `_goto=""` + `_halt=True` routes to `END`.)

- [ ] **Step 5: Run the full qa.aggregate suite**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py -q`
Expected: PASS — the new `TestQaAggregateRescueDispatch` class, the 4 patched reject tests, and all unchanged tests (approve, fabrication-without-flag, vacuous-pass guard, gate-counter, audit).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/qa_aggregate.py src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py
git commit -m "feat(qa): qa.aggregate defers rescuable rejects to the rewrite cycle"
```

---

## Task 7: seed `qa_rewrite_max_attempts='1'`

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py:319`

- [ ] **Step 1: Add the default**

In `src/cofounder_agent/services/settings_defaults.py`, in the "Content router / writer / self-review" section, immediately after the existing `content_router_qa_rewrite_timeout_seconds` line (line 319), add:

```python
    'content_router_qa_rewrite_timeout_seconds': '240',
    # QA rescue cycle (canonical_blog): max bounded rewrite passes for a
    # critic-vetoed/below-threshold draft before it is hard-rejected. Default 1
    # (one rescue attempt). 0 disables the cycle; clamped to [0,3] at read time
    # in qa.aggregate. NEVER rescues a fabrication / gate / missing_required
    # veto — only soft critic vetoes + below-threshold scores.
    'qa_rewrite_max_attempts': '1',
```

- [ ] **Step 2: Verify the default loads**

Run: `poetry run python -c "from services.settings_defaults import DEFAULTS; print(DEFAULTS['qa_rewrite_max_attempts'])"`
(From repo root with the worktree venv active / `PYTHONPATH=src/cofounder_agent`.)
Expected: prints `1`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py
git commit -m "feat(qa): seed qa_rewrite_max_attempts=1 (rescue cycle default on)"
```

---

## Task 8: wire the rescue cycle into `canonical_blog` spec

**Files:**

- Modify: `src/cofounder_agent/services/canonical_blog_spec.py:116` (node), `:163-164` (edges)
- Test: `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py`

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py` (inside the existing test module; mirror the existing `edges = {(e["from"], e["to"]) ...}` style):

```python
    def test_qa_rescue_cycle_wired(self):
        from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as spec

        node_atoms = {n["atom"] for n in spec["nodes"]}
        assert "qa.rewrite" in node_atoms

        edges = spec["edges"]
        pair = lambda: {(e["from"], e["to"]) for e in edges}
        # Branch edge: qa_aggregate -> qa_rewrite, flagged branch.
        assert ("qa_aggregate", "qa_rewrite") in pair()
        branch_edge = next(
            e for e in edges if e["from"] == "qa_aggregate" and e["to"] == "qa_rewrite"
        )
        assert branch_edge.get("branch") is True
        # Loop edge: qa_rewrite -> qa_programmatic, flagged loop.
        loop_edge = next(
            e for e in edges if e["from"] == "qa_rewrite" and e["to"] == "qa_programmatic"
        )
        assert loop_edge.get("loop") is True
        # The default forward edge from qa_aggregate is unchanged.
        assert ("qa_aggregate", "seo_all_metadata") in pair()

    def test_node_count_is_37(self):
        from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as spec
        assert len(spec["nodes"]) == 37
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py -q -k "rescue or node_count_is_37"`
Expected: FAIL — `qa.rewrite` node absent; node count 36.

- [ ] **Step 3a: Add the `qa_rewrite` node**

In `src/cofounder_agent/services/canonical_blog_spec.py`, in the `nodes` list, immediately after the `qa_aggregate` node (line 116) and before the `seo_all_metadata` node (line 122), add:

```python
        {"id": "qa_aggregate", "atom": "qa.aggregate"},
        # QA rescue cycle: qa.aggregate emits _goto="qa_rewrite" on a rescuable
        # reject; the branch router (build_graph_from_spec) routes here for one
        # bounded revision pass, then the loop edge re-runs the QA block.
        {"id": "qa_rewrite", "atom": "qa.rewrite"},
```

- [ ] **Step 3b: Add the branch + loop edges**

In the `edges` list, the line `{"from": "qa_aggregate", "to": "seo_all_metadata"},` (line 164) is the default forward edge — keep it. Add the two new edges right after it:

```python
        # seo.* collapsed (#734) — single structured call
        {"from": "qa_aggregate", "to": "seo_all_metadata"},
        # QA rescue cycle (default-on, qa_rewrite_max_attempts=1):
        # qa_aggregate -> qa_rewrite is the conditional branch (taken when
        # qa.aggregate sets _goto="qa_rewrite"); qa_rewrite -> qa_programmatic
        # is the bounded back-edge (loop-flagged so DAG validation permits it).
        {"from": "qa_aggregate", "to": "qa_rewrite", "branch": True},
        {"from": "qa_rewrite", "to": "qa_programmatic", "loop": True},
```

- [ ] **Step 4: Run the spec tests + the real-spec compile guard**

Run: `poetry run pytest src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py src/cofounder_agent/tests/integration/test_graphdef_pipeline.py::test_canonical_blog_spec_compiles -q`
Expected: PASS — the spec wiring tests AND the existing real-spec compile guard (`test_canonical_blog_spec_compiles`), which now exercises the cycle through the real `_validate_spec` + `build_graph_from_spec`.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/canonical_blog_spec.py src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py
git commit -m "feat(qa): wire qa.rewrite rescue cycle into canonical_blog spec (36->37 nodes)"
```

---

## Task 9: re-seed migration

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260617_HHMMSS_reseed_canonical_blog_graph_def_qa_rescue_cycle.py`

- [ ] **Step 1: Generate the timestamped migration file**

Run: `poetry run python scripts/new-migration.py "reseed canonical_blog graph_def qa rescue cycle"`
This creates `src/cofounder_agent/services/migrations/20260617_<HHMMSS>_reseed_canonical_blog_graph_def_qa_rescue_cycle.py` with the runner stub. Note the exact filename it prints.

- [ ] **Step 2: Replace the generated file's contents**

Overwrite the generated file with (mirrors `20260611_155929_reseed_canonical_blog_graph_def_v5_seo_collapsed.py`):

```python
"""Migration: reseed canonical_blog graph_def — add the QA rescue cycle.

Adds the qa.rewrite node + the branch edge (qa_aggregate -> qa_rewrite) and the
loop edge (qa_rewrite -> qa_programmatic) so a critic-vetoed / below-threshold
draft gets one bounded revision pass before it is hard-rejected. The graph_def
source of truth is services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF (now
37 nodes); this migration writes json.dumps(that) into the active
canonical_blog pipeline_templates row.

The rescue is gated by app_settings.qa_rewrite_max_attempts (default 1, seeded
in settings_defaults.py) and qa.aggregate's is_rescuable_reject predicate — it
never rescues a fabrication / gate / missing_required veto.

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Import only the pure-data spec dict — no heavy deps so this runs cleanly in
# the migrations-smoke CI environment.
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF  # noqa: E402


async def up(pool) -> None:
    graph_def_json = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def  = $1::jsonb,
                   updated_at = NOW()
             WHERE slug   = 'canonical_blog'
               AND active = true
            """,
            graph_def_json,
        )
    logger.info(
        "Migration reseed_canonical_blog_graph_def_qa_rescue_cycle up: "
        "added qa.rewrite node + branch/loop edges (37 nodes). result=%s",
        result,
    )


async def down(pool) -> None:
    # Reverting requires re-applying the previous canonical_blog seed migration
    # (20260611_155929_reseed_canonical_blog_graph_def_v5_seo_collapsed.py) or
    # restoring the pipeline_templates row from backup. No-op here.
    logger.warning(
        "Migration reseed_canonical_blog_graph_def_qa_rescue_cycle down: "
        "no-op — re-apply the previous graph_def seed migration to revert."
    )
```

- [ ] **Step 3: Lint the migration**

Run: `poetry run python scripts/ci/migrations_lint.py`
Expected: exits 0 (no collisions, runner interface `up`/`down` present).

- [ ] **Step 4: Smoke-test migrations against a fresh DB**

Run: `poetry run python scripts/ci/migrations_smoke.py`
Expected: exits 0 (the reseed applies cleanly; the `UPDATE` no-ops on a fresh DB where the row already has the baseline graph_def, then is overwritten with the 37-node spec).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260617_*_reseed_canonical_blog_graph_def_qa_rescue_cycle.py
git commit -m "feat(qa): reseed canonical_blog graph_def with the QA rescue cycle"
```

---

## Task 10: end-to-end cycle integration tests

**Files:**

- Modify: `src/cofounder_agent/tests/integration/test_graphdef_pipeline.py`

These exercise the REAL `build_graph_from_spec` compiler + a real `StateGraph.compile().ainvoke()` with synthetic atoms (no Ollama, no DB, no TemplateRunner) — a focused guard that the cycle fires exactly once and that the durable counter terminates it. This mirrors the compile-direct synthetic-graph approach in `test_pipeline_architect_halt.py` (cleaner + faster than stubbing all 12 prod rails or threading the runner).

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/tests/integration/test_graphdef_pipeline.py`:

```python
# ---------------------------------------------------------------------------
# Test 4 — the QA rescue cycle fires once then terminates (QA rescue cycle)
# ---------------------------------------------------------------------------

from unittest.mock import patch as _patch

from plugins.atom import AtomMeta as _AtomMeta
from services import pipeline_architect as _pa


def _rescue_meta(name: str) -> _AtomMeta:
    return _AtomMeta(name=name, type="atom", version="1.0.0", description=name)


def _compile_rescue_graph(gate_fn, rewrite_fn):
    """gate --branch--> qa_rewrite --loop--> gate; gate --default--> END.

    Compiles the REAL spec shape through build_graph_from_spec with synthetic
    atoms, exercising the branch router + the loop back-edge + the
    _merge_rail_reviews reducer end-to-end."""
    catalog = {"t.gate": _rescue_meta("t.gate"), "t.rewrite": _rescue_meta("t.rewrite")}
    callables = {"t.gate": gate_fn, "t.rewrite": rewrite_fn}
    spec = {
        "name": "canonical_blog",
        "entry": "gate",
        "nodes": [
            {"id": "gate", "atom": "t.gate"},
            {"id": "qa_rewrite", "atom": "t.rewrite"},
        ],
        "edges": [
            {"from": "gate", "to": "qa_rewrite", "branch": True},
            {"from": "gate", "to": "END"},
            {"from": "qa_rewrite", "to": "gate", "loop": True},
        ],
    }
    with (
        _patch.object(_pa, "get_atom_meta", lambda n: catalog.get(n)),
        _patch.object(_pa, "get_atom_callable", lambda n: callables.get(n)),
        _patch("plugins.registry.get_core_samples", return_value={"stages": []}),
    ):
        return _pa.build_graph_from_spec(spec, pool=None).compile()


@pytest.mark.asyncio
async def test_graphdef_rescue_cycle_runs_once_then_approves():
    """The gate defers (emits _goto) on the first pass; qa_rewrite revises the
    content + increments the durable counter + emits the review-reset sentinel;
    on the second pass the gate approves. Proves branch+loop+reducer compose so
    the cycle runs exactly once — through the REAL compiler."""
    log: list[str] = []

    async def _gate(state):
        log.append("gate")
        # Second pass (rewrite set content="REVISED") -> approve via default edge.
        if state.get("content") == "REVISED":
            return {"_goto": "", "status": "awaiting_approval"}
        return {"_goto": "qa_rewrite"}

    async def _rewrite(state):
        log.append("rewrite")
        attempts = int(state.get("qa_rewrite_attempts") or 0)
        return {
            "content": "REVISED",
            "qa_rewrite_attempts": attempts + 1,
            "qa_rail_reviews": [{"__reset__": True}],
        }

    compiled = _compile_rescue_graph(_gate, _rewrite)
    final = await compiled.ainvoke(
        {"task_id": "t-rescue", "content": "ORIG"},
        config={"configurable": {"thread_id": "t-rescue"}},
    )

    # gate(defer) -> rewrite -> gate(approve). Exactly one rescue.
    assert log == ["gate", "rewrite", "gate"], log
    assert final.get("content") == "REVISED"
    assert final.get("qa_rewrite_attempts") == 1


@pytest.mark.asyncio
async def test_graphdef_rescue_cycle_terminates_when_rewrite_keeps_failing():
    """When the rewrite doesn't fix the draft, the durable counter still
    terminates the loop: the gate only defers while attempts < max(1); the
    second pass halts. Guards against an unbounded rescue loop."""
    log: list[str] = []

    async def _gate(state):
        log.append("gate")
        attempts = int(state.get("qa_rewrite_attempts") or 0)
        if attempts < 1:
            return {"_goto": "qa_rewrite"}
        # Exhausted — hard halt (mirrors qa.aggregate's terminal reject).
        return {"_halt": True, "_halt_reason": "exhausted", "_goto": ""}

    async def _rewrite(state):
        log.append("rewrite")
        attempts = int(state.get("qa_rewrite_attempts") or 0)
        # Writer "fails" — content unchanged, but the counter still burns.
        return {
            "qa_rewrite_attempts": attempts + 1,
            "qa_rail_reviews": [{"__reset__": True}],
        }

    compiled = _compile_rescue_graph(_gate, _rewrite)
    final = await compiled.ainvoke(
        {"task_id": "t-exhaust", "content": "ORIG"},
        config={"configurable": {"thread_id": "t-exhaust"}},
    )

    # gate(defer) -> rewrite -> gate(halt). The loop ran exactly once.
    assert log == ["gate", "rewrite", "gate"], log
    assert final.get("qa_rewrite_attempts") == 1
    assert final.get("_halt") is True
```

- [ ] **Step 2: Run the integration tests to verify they fail (run BEFORE Tasks 3-4 to see the failure)**

Run: `poetry run pytest src/cofounder_agent/tests/integration/test_graphdef_pipeline.py -q -k "rescue"`
Expected (without the Task 3-4 compiler changes): FAIL — `build_graph_from_spec` treats `gate`'s two out-edges as a parallel fan-out (`_halt_router_multi` runs both `qa_rewrite` and `END`), so `_goto` is never honored and the log is not `["gate", "rewrite", "gate"]`. (Running the plan top-to-bottom, Tasks 3-4 are already committed, so these PASS — they still serve as the end-to-end composition guard.)

- [ ] **Step 3: Run the tests to verify they pass**

Run: `poetry run pytest src/cofounder_agent/tests/integration/test_graphdef_pipeline.py -q`
Expected: PASS (the two new rescue tests + the 3 pre-existing graph_def tests).

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/tests/integration/test_graphdef_pipeline.py
git commit -m "test(qa): end-to-end QA rescue cycle integration guards"
```

---

## Task 11: full-suite verification + docs

**Files:**

- Modify: `CLAUDE.md` (pipeline-stage count narrative), `docs/architecture/anti-hallucination.md` (rescue cycle note)

- [ ] **Step 1: Run the full affected test surface**

Run:

```bash
poetry run pytest \
  src/cofounder_agent/tests/unit/services/atoms/ \
  src/cofounder_agent/tests/unit/services/test_pipeline_architect_validate.py \
  src/cofounder_agent/tests/unit/services/test_pipeline_architect_branch.py \
  src/cofounder_agent/tests/unit/services/test_pipeline_architect_halt.py \
  src/cofounder_agent/tests/unit/services/test_pipeline_architect_schema.py \
  src/cofounder_agent/tests/unit/services/test_merge_rail_reviews.py \
  src/cofounder_agent/tests/unit/services/test_canonical_blog_spec.py \
  src/cofounder_agent/tests/integration/test_graphdef_pipeline.py \
  -q
```

Expected: all PASS, 0 errors.

- [ ] **Step 2: Run the multi_model_qa rail-library tests (no regression)**

Run: `poetry run pytest src/cofounder_agent/tests/unit -q -k "multi_model_qa or qa_rail or qa_aggregate"`
Expected: PASS.

- [ ] **Step 3: Update CLAUDE.md pipeline narrative**

In `CLAUDE.md`, the "Content pipeline stages" section says the graph_def is **36 nodes**. Update the node count to **37** and add `qa_rewrite` to the QA-block description. Find the line:

> The graph_def is **36 nodes** — 10 `stage.*` + 12 `content.*` + 12 `qa.*` + 1 `seo.*` + 1 `atoms.approval_gate` — run as a linear chain.

Change `**36 nodes**` → `**37 nodes**`, change `12 qa.*` → `12 qa.* + 1 qa.rewrite`, and append a sentence: "A bounded QA rescue cycle (#deferred-from-1668) adds the `qa.rewrite` node + a conditional `qa_aggregate → qa_rewrite` branch edge and a `qa_rewrite → qa_programmatic` loop edge: a rescuable reject (soft critic veto / below-threshold score, never fabrication) gets one revision pass before hard-reject, bounded by `qa_rewrite_max_attempts` (default 1)."

(The repo-derivable counts auto-sync via the GitHub Actions `sync-claude-md.yml` workflow, but the narrative sentence is hand-authored — add it.)

- [ ] **Step 4: Add a rescue-cycle note to anti-hallucination.md**

In `docs/architecture/anti-hallucination.md`, add a short subsection after the QA aggregation description noting that a rescuable reject (critic-only veto or below-threshold score) is deferred to `qa.rewrite` for one bounded revision pass before the hard reject, that `is_rescuable_reject` excludes `programmatic_validator` / gate / `missing_required:*` vetoes, and that the bound is the durable `qa_rewrite_attempts` counter (`qa_rewrite_max_attempts`, default 1).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/architecture/anti-hallucination.md
git commit -m "docs(qa): document the canonical_blog QA rescue cycle"
```

---

## Task 12: open the PR

- [ ] **Step 1: Re-verify the branch is rebased on latest main (merge-ref drift guard)**

Run: `git fetch origin && git rebase origin/main`
Resolve any conflicts (the most likely is `canonical_blog_spec.py` / `settings_defaults.py` if main re-seeded the graph_def — if so, re-apply the rescue node/edges + re-run Task 8 tests).

- [ ] **Step 2: Run the public-mirror leak guard locally (it is not a required CI check)**

Run: `poetry run python scripts/ci/public_mirror_safety.py` (or the documented local invocation). Expected: no operator-identity leaks introduced (this change is all in `src/cofounder_agent/` substrate — public-safe).

- [ ] **Step 3: Push + open the PR against `Glad-Labs/glad-labs-stack main`**

```bash
git push -u origin HEAD
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "feat(qa): bounded QA rescue cycle for canonical_blog" \
  --body "$(cat <<'EOF'
## What

Adds a bounded (default 1-shot) rewrite/rescue loop to the canonical_blog QA
gate. A draft the LLM critic vetoes — or one that scores below the threshold
with no hard veto — now gets ONE targeted revision pass before it is
hard-rejected, instead of being dropped.

Deferred from #1668 (QA scoring recalibration) because it touches the shared
pipeline engine.

## How

- `qa.aggregate` defers a **rescuable** reject (soft critic veto OR
  below-threshold score) via `_goto="qa_rewrite"` while
  `qa_rewrite_attempts < qa_rewrite_max_attempts` (default 1). It NEVER rescues
  a `programmatic_validator` (fabrication), gate-provider, or
  `missing_required:*` veto — see `is_rescuable_reject`.
- New `qa.rewrite` atom revises the draft, increments the durable counter, and
  emits a review-reset sentinel so the second QA pass starts clean.
- The compiler (`pipeline_architect`) is taught to permit ONE designated cycle:
  `loop`-flagged edges are exempt from DAG validation; a `branch`-flagged edge
  gets a `_goto`-aware router (`_halt` > `_goto` > default). Accidental
  back-edges still fail validation loud.
- The bound is the durable `qa_rewrite_attempts` counter in the LangGraph
  postgres checkpoint — it survives kill-and-resume, so the cycle can't
  re-rescue past the max.

## Safety

- Fabrication/gate/missing_required vetoes are never rescued (regression test:
  `test_fabrication_veto_never_rescues`).
- Writer error/empty on the rewrite → degrade-to-reject (counter still burns,
  original reject stands, finding emitted).
- Default-on at 1 pass; operators set `qa_rewrite_max_attempts=0` to disable.

## Tests

TDD throughout: `is_rescuable_reject`, `_merge_rail_reviews`, loop/branch
compiler validation, the `qa.rewrite` atom, `qa.aggregate` rescue dispatch, and
two end-to-end synthetic-cycle integration tests. Full QA atom + architect +
multi_model_qa suites green.

Spec: `docs/superpowers/specs/2026-06-17-qa-rescue-cycle-design.md`
Plan: `docs/superpowers/plans/2026-06-17-qa-rescue-cycle.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Manage the PR to green**

Watch CI (`test-backend`, `migrations-smoke` are the required checks). Fix any failures. Per `feedback_ci_is_the_review_gate` + `feedback_manage_prs_yourself`: when CI is green, merge (squash) — don't wait to ask.

---

## Self-review notes (resolved during planning)

- **`qa_reviews` operator.add concat bug:** the rescue-dispatch early-return MUST omit `qa_reviews` (Task 6, step 4b) — otherwise the terminal pass concats stale+fresh. Covered by `test_rescue_dispatch_omits_qa_reviews`.
- **Counter pass-through:** `qa.aggregate` changed from emitting `qa_rewrite_attempts: 0` to `attempts` (the read value) so the durable counter is never reset mid-cycle. Existing `test_approve_sets_downstream_keys` still sees `0` on a fresh run.
- **Existing reject tests:** four critic/score-threshold reject tests are rescuable-by-default now; Task 6a disables rescue (`max_attempts=0`) in exactly those four, leaving their hard-reject assertions intact. Tests using programmatic/gate/missing_required vetoes stay green unchanged.
- **`ollama_chat_text` has no `max_tokens` param** — only the timeout setting is wired; output length is governed by the dispatcher per-model. The `content_router_qa_rewrite_max_tokens` setting stays orphaned (documented, not plumbed).
- **No `prompts/*.yaml`** — the inline `_FALLBACK` + `_resolve_revise_prompt()` IS the DB-configurable pattern (Langfuse override surface via the key). No YAML file is created.
- **`_branch_router` priority** — `_halt` is checked before `_goto`, so a terminal hard-reject (`_halt=True`, `_goto=""`) routes to END even though the node has a branch edge.

```

```
