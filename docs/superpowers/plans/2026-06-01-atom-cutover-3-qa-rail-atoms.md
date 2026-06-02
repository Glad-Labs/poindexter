# Atom Cutover — Plan 3: split `cross_model_qa` into rail atoms

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose the coarse `cross_model_qa` stage into independent, individually-composable rail atoms — `qa.deepeval`, `qa.guardrails`, `qa.ragas`, `qa.critic` (each wraps the EXISTING rail logic in `multi_model_qa.py`) plus `qa.aggregate` (combines the per-rail verdicts into the gate decision) — each declaring an `AtomMeta` (requires/produces/capability_tier). **Additive + dormant:** the registry auto-discovers the new atoms; the live `canonical_blog` pipeline keeps running the legacy `cross_model_qa` stage until Plan 4 repoints the graph_def.

**Architecture:** Each rail atom is a flat module under `services/atoms/` whose `ATOM_META.name` is the dotted `qa.<rail>` slug (the registry keys on `ATOM_META.name`, not the filename, and `pkgutil.iter_modules` discovers flat files). The rail atoms read `content`/`topic`/`research_context` + services from the LangGraph state, construct a `MultiModelQA(pool, settings_service, site_config=...)`, call the relevant `_check_*` method(s) (which already encode each rail's gate-flag check + `ReviewerResult` construction), serialize the non-`None` results, and append them to a NEW `qa_rail_reviews` state channel (an `operator.add`-reduced list, so Plan 4 can fan the rails out in parallel without `InvalidUpdateError`). `qa.aggregate` reads `qa_rail_reviews`, computes a weighted score + non-advisory veto + threshold decision (DB-configurable weights), and emits `qa_final_score` / `qa_final_verdict` (+ `_halt` on reject). The OSS rails (deepeval/guardrails/ragas) mark their reviews `advisory=True` (matching prod, where they're advisory); `qa.critic` is the non-advisory hard gate.

**No parity expected (by spec):** the spec states "No parity check is possible (the granularity refactor changes behavior), so validation is quality-based" (Plan 5 canary + human review). So `qa.aggregate` reproduces the stage's _core_ decision (weighted-score + non-advisory-veto + threshold) but NOT the validator-warning penalty or the web-factcheck override (those depend on rails outside this OSS-rail set). This is intentional and documented.

**Tech Stack:** Python 3.13, the existing `plugins/atom.py` (`AtomMeta`/`FieldSpec`/`RetryPolicy`), `services/atom_registry.py` (flat `pkgutil.iter_modules` discovery; keys on `ATOM_META.name`; underscore-prefixed modules are skipped — so `_qa_rail_common.py` is a safe shared-helper home), `services/multi_model_qa.py` (`MultiModelQA` + `ReviewerResult`), `services/template_runner.py::PipelineState`, `services/site_config.py::SiteConfig`. pytest (`asyncio_mode="auto"`; mark `@pytest.mark.unit`).

**Spec:** `docs/superpowers/specs/2026-06-01-canonical-blog-atom-cutover-design.md` (§ "Granularity refactor (#362 content)" + granularity principle).

**Conventions:** run tests from `src/cofounder_agent` with the main venv python (worktrees have no poetry env):
`"<main-venv-python>" -m pytest <relative/test/path> -p no:cacheprovider > test_out.txt 2>&1` then **Read `test_out.txt` back** (Windows stdout buffers; never treat empty as success; delete `test_out.txt`, don't commit it). cwd = the worktree's `src/cofounder_agent`. Linear commits, commit after each green task, end every message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Do NOT push/PR/merge — the controller integrates.

### Reference facts (verified — don't re-derive)

- `ReviewerResult` is a **mutable** `@dataclass` at `services/multi_model_qa.py:57`: fields `reviewer: str`, `approved: bool`, `score: float`, `feedback: str`, `provider: str`, `advisory: bool = False`. (Mutable → you can set `r.advisory = True`.)
- `MultiModelQA.__init__(self, pool=None, settings_service=None, *, site_config: SiteConfig)` (`multi_model_qa.py:273`) — cheap, stores attrs only, no I/O.
- Rail seams on `MultiModelQA` (each returns `ReviewerResult | None`; `None` = rail disabled or inapplicable; all are error-swallowing internally):
  - `_check_deepeval_brand(content, topic)` — **sync** (`:1286`)
  - `async _check_deepeval_g_eval(content, topic)` (`:1348`)
  - `async _check_deepeval_faithfulness(content, research_sources)` (`:1459`) — `None` when research empty
  - `async _check_guardrails_brand(content)` (`:1579`)
  - `async _check_guardrails_competitor(content)` (`:1740`) — `None` when no competitors configured
  - `async _check_ragas_eval(content, topic, research_sources)` (`:1635`) — `None` when research empty
  - `async _review_with_cloud_model(title, content, topic, model_override=None, research_sources=None)` (`:917`) → `tuple[ReviewerResult, dict] | None` (the critic; dict is a cost_log)
- The stage passes `seo_title` as the title and `research_context` as `research_sources` (`stages/cross_model_qa.py:261,264`). Mirror that.
- Atom-authoring convention: module-level `ATOM_META: AtomMeta` (discovery trigger) + module-level `async def run(state: dict) -> dict` (the registry binds `run`); read services from `state` (`state.get("site_config")`, `getattr(state.get("database_service"), "pool", None)`, `state.get("settings_service")`); return ONLY the state delta; lazy-import heavy deps INSIDE `run`. Export `__all__ = ["ATOM_META", "run"]`.
- Aggregation weights/threshold (DB-config, read via `site_config.get`): `qa_validator_weight` (0.4), `qa_critic_weight` (0.6), `qa_gate_weight` (0.3), `qa_final_score_threshold` (70). Provider→weight map + non-advisory veto + `approved = all_passed and final_score >= threshold` mirrors `multi_model_qa.py:762-861`.

---

### Task 1: `qa_rail_reviews` state channel + shared `_qa_rail_common` (serialize + aggregate)

The shared foundation: a new `operator.add`-reduced `qa_rail_reviews` channel on `PipelineState` (so parallel rails can append), and an underscore-prefixed helper module (NOT discovered as an atom) holding the `ReviewerResult`→dict serializer and the pure aggregation function (unit-tested in isolation — no DB, no mocks).

**Files:**

- Modify: `src/cofounder_agent/services/template_runner.py` — `PipelineState` (~line 294, beside the existing `qa_reviews` channel)
- Create: `src/cofounder_agent/services/atoms/_qa_rail_common.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the pure rail-aggregation helper (atom-cutover Plan 3, #355).
No DB, no mocks — exercises reviewer_to_dict + aggregate_rail_reviews."""

from __future__ import annotations

import pytest

from services.atoms._qa_rail_common import aggregate_rail_reviews, reviewer_to_dict


class _R:
    def __init__(self, reviewer, approved, score, provider, advisory=False, feedback="fb"):
        self.reviewer = reviewer
        self.approved = approved
        self.score = score
        self.provider = provider
        self.advisory = advisory
        self.feedback = feedback


@pytest.mark.unit
class TestReviewerToDict:
    def test_serializes_all_fields(self):
        d = reviewer_to_dict(_R("ollama_qa", True, 88.0, "ollama", advisory=False))
        assert d == {
            "reviewer": "ollama_qa", "approved": True, "score": 88.0,
            "feedback": "fb", "provider": "ollama", "advisory": False,
        }


@pytest.mark.unit
class TestAggregate:
    def test_all_pass_above_threshold_approves(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            {"reviewer": "deepeval_g_eval", "approved": True, "score": 80.0, "provider": "ollama", "advisory": True},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["approved"] is True
        assert out["qa_final_verdict"] == "approve"
        assert out["qa_final_score"] == 85.0  # equal weights (both ollama=0.6) → mean
        assert out["vetoed_by"] == []

    def test_nonadvisory_failure_vetoes(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": False, "score": 95.0, "provider": "ollama", "advisory": False},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["approved"] is False
        assert out["qa_final_verdict"] == "reject"
        assert out["vetoed_by"] == ["ollama_qa"]

    def test_advisory_failure_does_not_veto(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            {"reviewer": "guardrails_brand", "approved": False, "score": 0.0, "provider": "programmatic", "advisory": True},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        # advisory fail doesn't veto; score 0 is ignored (score > 0 filter) → final = 90
        assert out["approved"] is True
        assert out["vetoed_by"] == []
        assert out["qa_final_score"] == 90.0

    def test_below_threshold_rejects_even_if_all_pass(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": True, "score": 60.0, "provider": "ollama", "advisory": False},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["approved"] is False
        assert out["qa_final_verdict"] == "reject"

    def test_empty_reviews_rejects_at_zero(self):
        out = aggregate_rail_reviews([], validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["qa_final_score"] == 0.0
        assert out["approved"] is False

    def test_provider_weights_applied(self):
        # programmatic weight 0.4, ollama weight 0.6 → weighted mean of (100, 50)
        reviews = [
            {"reviewer": "validator", "approved": True, "score": 100.0, "provider": "programmatic", "advisory": False},
            {"reviewer": "ollama_qa", "approved": True, "score": 50.0, "provider": "ollama", "advisory": False},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=10.0)
        # (100*0.4 + 50*0.6) / (0.4+0.6) = (40+30)/1.0 = 70.0
        assert out["qa_final_score"] == 70.0
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_rail_common.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `services.atoms._qa_rail_common` does not exist.

- [ ] **Step 3a: Add the `qa_rail_reviews` channel to `PipelineState`**

In `services/template_runner.py`, locate the existing `qa_reviews: Annotated[list, operator.add]` field (~line 294) and add immediately after it:

```python
    # qa_rail_reviews (#355 Plan 3): the per-rail ReviewerResult dicts
    # emitted by the qa.* rail atoms (qa.deepeval / qa.guardrails /
    # qa.ragas / qa.critic). operator.add so a parallel fan-out of rails
    # (Plan 4's graph_def) can each append concurrently without
    # InvalidUpdateError. qa.aggregate reads the merged list.
    qa_rail_reviews: Annotated[list, operator.add]
```

(`operator` and `Annotated` are already imported in this module — the existing `qa_reviews` line uses them.)

- [ ] **Step 3b: Create `services/atoms/_qa_rail_common.py`**

```python
"""Shared helpers for the qa.* rail atoms (atom-cutover Plan 3, #355).

Underscore-prefixed so ``atom_registry._walk_package`` skips it — this is
a helper module, NOT a discoverable atom. Holds the ReviewerResult->dict
serializer and the pure rail-aggregation function (weighted score +
non-advisory veto + threshold), both unit-testable without a DB.

The aggregation mirrors the CORE of multi_model_qa.review()'s decision
(services/multi_model_qa.py:762-861): provider-weighted mean of the
positive scores, a veto from any non-advisory failing review, and
``approved = all_passed and final_score >= threshold``. Per the spec's
"no parity check" clause (the granularity refactor changes behavior, so
validation is quality-canary based), it intentionally omits the
validator-warning penalty and the web-factcheck override, which depend on
rails outside the OSS qa.* set.
"""

from __future__ import annotations

from typing import Any

# Provider -> weight bucket. programmatic = validator weight; the LLM
# critics = critic weight; the gate providers = gate weight. Unknown
# providers default to 0.5 (matches multi_model_qa.review()).
_VALIDATOR_PROVIDERS = ("programmatic",)
_CRITIC_PROVIDERS = ("anthropic", "google", "ollama")
_GATE_PROVIDERS = ("consistency_gate", "vision_gate", "web_factcheck", "url_verifier")


def reviewer_to_dict(r: Any) -> dict[str, Any]:
    """Serialize a ReviewerResult (or duck-typed equivalent) to a plain
    dict for the ``qa_rail_reviews`` state channel."""
    return {
        "reviewer": r.reviewer,
        "approved": bool(r.approved),
        "score": float(r.score),
        "feedback": getattr(r, "feedback", "") or "",
        "provider": r.provider,
        "advisory": bool(getattr(r, "advisory", False)),
    }


def _weight_for(provider: str | None, *, validator_weight: float, critic_weight: float, gate_weight: float) -> float:
    if provider in _VALIDATOR_PROVIDERS:
        return validator_weight
    if provider in _CRITIC_PROVIDERS:
        return critic_weight
    if provider in _GATE_PROVIDERS:
        return gate_weight
    return 0.5


def aggregate_rail_reviews(
    reviews: list[dict[str, Any]],
    *,
    validator_weight: float = 0.4,
    critic_weight: float = 0.6,
    gate_weight: float = 0.3,
    threshold: float = 70.0,
) -> dict[str, Any]:
    """Combine per-rail review dicts into the gate decision.

    Returns ``{"qa_final_score", "qa_final_verdict", "approved", "vetoed_by"}``.
    """
    def _score(r: dict[str, Any]) -> float:
        try:
            return float(r.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    scored = [r for r in reviews if _score(r) > 0]
    if scored:
        total_w = sum(
            _weight_for(r.get("provider"), validator_weight=validator_weight,
                        critic_weight=critic_weight, gate_weight=gate_weight)
            for r in scored
        )
        if total_w > 0:
            final_score = sum(
                _score(r) * _weight_for(r.get("provider"), validator_weight=validator_weight,
                                        critic_weight=critic_weight, gate_weight=gate_weight)
                for r in scored
            ) / total_w
        else:
            final_score = 0.0
    else:
        final_score = 0.0

    # A non-advisory failing review vetoes the whole pass.
    vetoed_by = [
        r.get("reviewer") for r in reviews
        if not r.get("approved") and not r.get("advisory")
    ]
    all_passed = not vetoed_by
    approved = all_passed and final_score >= threshold
    return {
        "qa_final_score": round(float(final_score), 2),
        "qa_final_verdict": "approve" if approved else "reject",
        "approved": approved,
        "vetoed_by": vetoed_by,
    }


__all__ = ["aggregate_rail_reviews", "reviewer_to_dict"]
```

- [ ] **Step 4: Run, verify they pass**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_rail_common.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all tests pass.

- [ ] **Step 5: Run a quick regression on PipelineState consumers**

Run: `"<venv-python>" -m pytest tests/unit/services/test_template_runner_state_partition.py tests/unit/services/test_capability_outcomes.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass (the new `qa_rail_reviews` field is additive; nothing asserts the exact PipelineState key set).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/template_runner.py src/cofounder_agent/services/atoms/_qa_rail_common.py src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_common.py
git commit -m "feat(qa): qa_rail_reviews channel + shared rail aggregation helper (#355)"
```

---

### Task 2: `qa.aggregate` atom

The combiner: reads `qa_rail_reviews` + DB-config weights (via `site_config`), calls the pure `aggregate_rail_reviews`, emits `qa_final_score` / `qa_final_verdict`, and halts the graph on reject.

**Files:**

- Create: `src/cofounder_agent/services/atoms/qa_aggregate.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the qa.aggregate atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_aggregate


class _Cfg:
    def __init__(self, vals=None):
        self._vals = vals or {}

    def get(self, key, default=None):
        return self._vals.get(key, default)


@pytest.mark.unit
class TestQaAggregateAtom:
    def test_meta(self):
        m = qa_aggregate.ATOM_META
        assert m.name == "qa.aggregate"
        assert "qa_rail_reviews" in m.requires
        assert "qa_final_score" in m.produces
        assert "qa_final_verdict" in m.produces

    async def test_approve_path(self):
        state = {
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert out["qa_final_score"] == 90.0
        assert "_halt" not in out

    async def test_reject_halts(self):
        state = {
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": False, "score": 95.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert "ollama_qa" in out["_halt_reason"]

    async def test_reads_threshold_from_site_config(self):
        # Threshold 95 → an 90-scoring all-pass run now REJECTS.
        state = {
            "site_config": _Cfg({"qa_final_score_threshold": "95"}),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"

    async def test_missing_reviews_key_rejects_at_zero(self):
        out = await qa_aggregate.run({"site_config": _Cfg()})
        assert out["qa_final_score"] == 0.0
        assert out["_halt"] is True
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_aggregate_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `services.atoms.qa_aggregate` does not exist.

- [ ] **Step 3: Create `services/atoms/qa_aggregate.py`**

```python
"""qa.aggregate — combine the qa.* rail reviews into the gate decision.

Atom-cutover Plan 3 (#355). Reads the ``qa_rail_reviews`` channel (the
ReviewerResult dicts emitted by qa.deepeval / qa.guardrails / qa.ragas /
qa.critic), applies the DB-configurable weighted-score + non-advisory-veto
+ threshold aggregation (services/atoms/_qa_rail_common.py), and emits
``qa_final_score`` / ``qa_final_verdict``. On reject it sets ``_halt`` so
build_graph_from_spec's halt-aware router short-circuits the graph.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import aggregate_rail_reviews

ATOM_META = AtomMeta(
    name="qa.aggregate",
    type="atom",
    version="1.0.0",
    description="Combine qa.* rail reviews into the final QA gate decision.",
    inputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="per-rail reviews"),),
    outputs=(
        FieldSpec(name="qa_final_score", type="float", description="weighted QA score"),
        FieldSpec(name="qa_final_verdict", type="str", description="approve|reject"),
    ),
    requires=("qa_rail_reviews",),
    produces=("qa_final_score", "qa_final_verdict"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
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
    out: dict[str, Any] = {
        "qa_final_score": result["qa_final_score"],
        "qa_final_verdict": result["qa_final_verdict"],
    }
    if not result["approved"]:
        out["_halt"] = True
        out["_halt_reason"] = (
            f"qa.aggregate: verdict=reject score={result['qa_final_score']} "
            f"vetoed_by={result['vetoed_by']}"
        )
    return out


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run, verify they pass**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_aggregate_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/atoms/qa_aggregate.py src/cofounder_agent/tests/unit/services/atoms/test_qa_aggregate_atom.py
git commit -m "feat(qa): qa.aggregate rail-combine atom (#355)"
```

---

### Task 3: `qa.deepeval` atom

Wraps the three DeepEval rails (`_check_deepeval_brand` sync + `_check_deepeval_g_eval` / `_check_deepeval_faithfulness` async) by delegating to a `MultiModelQA` instance, marks each result `advisory=True` (the OSS rails are advisory in prod), and appends the serialized results to `qa_rail_reviews`.

**Files:**

- Create: `src/cofounder_agent/services/atoms/qa_deepeval.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_deepeval_atom.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the qa.deepeval atom (atom-cutover Plan 3, #355).
Monkeypatches MultiModelQA._check_* so no DeepEval/Ollama is invoked."""

from __future__ import annotations

import pytest

from services.atoms import qa_deepeval
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(content="a real blog body that is long enough"):
    return {"content": content, "topic": "widgets", "research_context": None, "site_config": _Cfg()}


@pytest.mark.unit
class TestQaDeepevalAtom:
    def test_meta(self):
        m = qa_deepeval.ATOM_META
        assert m.name == "qa.deepeval"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_collects_non_none_rails_as_advisory(self, monkeypatch):
        def brand(self, content, topic):
            return ReviewerResult("deepeval_brand_fabrication", True, 95.0, "clean", "programmatic")

        async def geval(self, content, topic):
            return ReviewerResult("deepeval_g_eval", True, 82.0, "ok", "ollama")

        async def faith(self, content, research):
            return None  # no research → skipped

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)

        out = await qa_deepeval.run(_state())
        revs = out["qa_rail_reviews"]
        assert {r["reviewer"] for r in revs} == {"deepeval_brand_fabrication", "deepeval_g_eval"}
        assert all(r["advisory"] is True for r in revs)

    async def test_all_rails_none_yields_no_key(self, monkeypatch):
        def brand(self, content, topic):
            return None

        async def geval(self, content, topic):
            return None

        async def faith(self, content, research):
            return None

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)
        out = await qa_deepeval.run(_state())
        assert out == {}

    async def test_empty_content_short_circuits(self):
        out = await qa_deepeval.run(_state(content="   "))
        assert out == {}

    async def test_no_site_config_short_circuits(self):
        out = await qa_deepeval.run({"content": "body", "topic": "t"})
        assert out == {}
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_deepeval_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `services.atoms.qa_deepeval` does not exist.

- [ ] **Step 3: Create `services/atoms/qa_deepeval.py`**

```python
"""qa.deepeval — the DeepEval rail family as one composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA's three DeepEval rail
methods (brand-fabrication, g-eval, faithfulness) by delegating to a
MultiModelQA instance — zero rail logic is reimplemented. Each rail
self-gates (returns None when disabled or inapplicable). Results are
marked advisory=True (the OSS rails are advisory in prod) and appended to
the qa_rail_reviews channel. parallelizable=True.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.deepeval",
    type="atom",
    version="1.0.0",
    description="DeepEval rails (brand-fabrication + g-eval + faithfulness), advisory.",
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="advisory reviews"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    topic = state.get("topic") or ""
    research = state.get("research_context")
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    # Lazy import — keeps module discovery cheap (multi_model_qa is heavy).
    from services.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    brand = qa._check_deepeval_brand(content, topic)           # sync
    g_eval = await qa._check_deepeval_g_eval(content, topic)
    faith = await qa._check_deepeval_faithfulness(content, research)

    reviews: list[dict[str, Any]] = []
    for r in (brand, g_eval, faith):
        if r is not None:
            r.advisory = True
            reviews.append(reviewer_to_dict(r))
    return {"qa_rail_reviews": reviews} if reviews else {}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run, verify they pass**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_deepeval_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/atoms/qa_deepeval.py src/cofounder_agent/tests/unit/services/atoms/test_qa_deepeval_atom.py
git commit -m "feat(qa): qa.deepeval rail atom (#355)"
```

---

### Task 4: `qa.guardrails` + `qa.ragas` atoms

The two remaining OSS-advisory rail families. `qa.guardrails` wraps `_check_guardrails_brand` + `_check_guardrails_competitor`; `qa.ragas` wraps `_check_ragas_eval`. Same delegate-and-mark-advisory pattern as `qa.deepeval`.

**Files:**

- Create: `src/cofounder_agent/services/atoms/qa_guardrails.py`
- Create: `src/cofounder_agent/services/atoms/qa_ragas.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_guardrails_atom.py` (create)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_ragas_atom.py` (create)

- [ ] **Step 1: Write the failing tests**

`test_qa_guardrails_atom.py`:

```python
"""Unit tests for the qa.guardrails atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_guardrails
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state():
    return {"content": "a sufficiently long blog body", "topic": "t", "site_config": _Cfg()}


@pytest.mark.unit
class TestQaGuardrailsAtom:
    def test_meta(self):
        m = qa_guardrails.ATOM_META
        assert m.name == "qa.guardrails"
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_collects_advisory(self, monkeypatch):
        async def brand(self, content):
            return ReviewerResult("guardrails_brand", True, 100.0, "ok", "programmatic")

        async def comp(self, content):
            return None  # no competitors configured

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        out = await qa_guardrails.run(_state())
        revs = out["qa_rail_reviews"]
        assert [r["reviewer"] for r in revs] == ["guardrails_brand"]
        assert revs[0]["advisory"] is True

    async def test_all_none(self, monkeypatch):
        async def brand(self, content):
            return None

        async def comp(self, content):
            return None

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        assert await qa_guardrails.run(_state()) == {}

    async def test_empty_content(self):
        assert await qa_guardrails.run({"content": "", "site_config": _Cfg()}) == {}
```

`test_qa_ragas_atom.py`:

```python
"""Unit tests for the qa.ragas atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_ragas
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(research="some retrieved context paragraphs"):
    return {"content": "a sufficiently long blog body", "topic": "t",
            "research_context": research, "site_config": _Cfg()}


@pytest.mark.unit
class TestQaRagasAtom:
    def test_meta(self):
        m = qa_ragas.ATOM_META
        assert m.name == "qa.ragas"
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_collects_advisory(self, monkeypatch):
        async def ragas(self, content, topic, research):
            return ReviewerResult("ragas_eval", True, 77.0, "ok", "programmatic")

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        out = await qa_ragas.run(_state())
        assert out["qa_rail_reviews"][0]["reviewer"] == "ragas_eval"
        assert out["qa_rail_reviews"][0]["advisory"] is True

    async def test_none_when_no_research(self, monkeypatch):
        async def ragas(self, content, topic, research):
            return None

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        assert await qa_ragas.run(_state(research=None)) == {}

    async def test_empty_content(self):
        assert await qa_ragas.run({"content": "  ", "site_config": _Cfg()}) == {}
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_guardrails_atom.py tests/unit/services/atoms/test_qa_ragas_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError for both modules.

- [ ] **Step 3a: Create `services/atoms/qa_guardrails.py`**

```python
"""qa.guardrails — the guardrails-ai rail family as one composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA's guardrails rails
(brand + competitor) by delegation; advisory; appends to qa_rail_reviews.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.guardrails",
    type="atom",
    version="1.0.0",
    description="guardrails-ai rails (brand + competitor), advisory.",
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="advisory reviews"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from services.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    brand = await qa._check_guardrails_brand(content)
    competitor = await qa._check_guardrails_competitor(content)

    reviews: list[dict[str, Any]] = []
    for r in (brand, competitor):
        if r is not None:
            r.advisory = True
            reviews.append(reviewer_to_dict(r))
    return {"qa_rail_reviews": reviews} if reviews else {}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 3b: Create `services/atoms/qa_ragas.py`**

```python
"""qa.ragas — the Ragas rail as one composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA._check_ragas_eval by
delegation; advisory; appends to qa_rail_reviews. Yields nothing when
research context is absent (the rail needs retrieved contexts).
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.ragas",
    type="atom",
    version="1.0.0",
    description="Ragas faithfulness/relevancy/precision rail, advisory.",
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="advisory reviews"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    topic = state.get("topic") or ""
    research = state.get("research_context")
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from services.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    ragas = await qa._check_ragas_eval(content, topic, research)
    if ragas is None:
        return {}
    ragas.advisory = True
    return {"qa_rail_reviews": [reviewer_to_dict(ragas)]}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run, verify they pass**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_guardrails_atom.py tests/unit/services/atoms/test_qa_ragas_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/atoms/qa_guardrails.py src/cofounder_agent/services/atoms/qa_ragas.py src/cofounder_agent/tests/unit/services/atoms/test_qa_guardrails_atom.py src/cofounder_agent/tests/unit/services/atoms/test_qa_ragas_atom.py
git commit -m "feat(qa): qa.guardrails + qa.ragas rail atoms (#355)"
```

---

### Task 5: `qa.critic` atom

The non-advisory hard gate: wraps the legacy LLM critic (`MultiModelQA._review_with_cloud_model`, which returns `(ReviewerResult, cost_log) | None`). Unlike the OSS rails, its review is NOT marked advisory — a failing critic vetoes in `qa.aggregate`.

**Files:**

- Create: `src/cofounder_agent/services/atoms/qa_critic.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_critic_atom.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the qa.critic atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_critic
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state():
    return {"content": "a sufficiently long blog body", "topic": "t",
            "seo_title": "A Title", "research_context": None, "site_config": _Cfg()}


@pytest.mark.unit
class TestQaCriticAtom:
    def test_meta(self):
        m = qa_critic.ATOM_META
        assert m.name == "qa.critic"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_emits_non_advisory_review(self, monkeypatch):
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return ReviewerResult("ollama_qa", True, 84.0, "looks good", "ollama"), {"cost": 0.0}

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        out = await qa_critic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "ollama_qa"
        assert rev["advisory"] is False  # the critic is a hard gate

    async def test_none_result_yields_no_key(self, monkeypatch):
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return None

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        assert await qa_critic.run(_state()) == {}

    async def test_empty_content(self):
        assert await qa_critic.run({"content": "", "site_config": _Cfg()}) == {}
```

- [ ] **Step 2: Run, verify they fail**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_critic_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: ImportError — `services.atoms.qa_critic` does not exist.

- [ ] **Step 3: Create `services/atoms/qa_critic.py`**

```python
"""qa.critic — the legacy adversarial LLM critic as a composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA._review_with_cloud_model
(returns (ReviewerResult, cost_log) | None) by delegation. Unlike the OSS
rails, the critic is the HARD gate — its review is NOT advisory, so a
failing critic vetoes in qa.aggregate. Title is sourced from seo_title
(falling back to title), mirroring the cross_model_qa stage.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.critic",
    type="atom",
    version="1.0.0",
    description="Adversarial LLM critic (the hard QA gate, non-advisory).",
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="critic review"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    title = state.get("seo_title") or state.get("title") or ""
    topic = state.get("topic") or ""
    research = state.get("research_context")
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from services.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    result = await qa._review_with_cloud_model(title, content, topic, research_sources=research)
    if result is None:
        return {}
    reviewer_result, _cost_log = result
    # Hard gate — leave advisory at its default False so qa.aggregate can veto.
    return {"qa_rail_reviews": [reviewer_to_dict(reviewer_result)]}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run, verify they pass**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_critic_atom.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/atoms/qa_critic.py src/cofounder_agent/tests/unit/services/atoms/test_qa_critic_atom.py
git commit -m "feat(qa): qa.critic hard-gate atom (#355)"
```

---

### Task 6: registry discovery + requires/produces validation

Proves the five new atoms are auto-discovered with the right names + requires/produces, and that a rails→aggregate fan-out spec passes Plan 1's build-time validator (the safety net Plan 4's cutover relies on). This ties the new atoms to the registry + validator without touching any live pipeline.

**Files:**

- Test: `src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_registry.py` (create)

- [ ] **Step 1: Write the test**

```python
"""Registry discovery + spec validation for the qa.* rail atoms
(atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services import pipeline_architect
from services.atom_registry import discover, get_atom_callable, get_atom_meta

_RAILS = ("qa.deepeval", "qa.guardrails", "qa.ragas", "qa.critic")
_ALL = _RAILS + ("qa.aggregate",)


@pytest.mark.unit
class TestQaRailRegistry:
    def test_all_atoms_discovered(self):
        discover()  # idempotent
        for name in _ALL:
            assert get_atom_meta(name) is not None, f"{name} not registered"
            assert callable(get_atom_callable(name)), f"{name} has no callable"

    def test_rails_produce_qa_rail_reviews(self):
        discover()
        for name in _RAILS:
            assert "qa_rail_reviews" in get_atom_meta(name).produces
            assert "content" in get_atom_meta(name).requires

    def test_aggregate_contract(self):
        discover()
        m = get_atom_meta("qa.aggregate")
        assert "qa_rail_reviews" in m.requires
        assert "qa_final_score" in m.produces and "qa_final_verdict" in m.produces

    def test_fanout_spec_validates(self):
        """A graph that runs the rails then aggregate must pass the Plan-1
        requires/produces validator — the safety net Plan 4 relies on."""
        discover()
        spec = {
            "name": "qa_block",
            "entry": "critic",
            "nodes": [
                {"id": "critic", "atom": "qa.critic"},
                {"id": "deepeval", "atom": "qa.deepeval"},
                {"id": "guardrails", "atom": "qa.guardrails"},
                {"id": "ragas", "atom": "qa.ragas"},
                {"id": "aggregate", "atom": "qa.aggregate"},
            ],
            "edges": [
                {"from": "critic", "to": "deepeval"},
                {"from": "deepeval", "to": "guardrails"},
                {"from": "guardrails", "to": "ragas"},
                {"from": "ragas", "to": "aggregate"},
                {"from": "aggregate", "to": "END"},
            ],
        }
        ok, errors = pipeline_architect._validate_spec(spec)
        assert ok is True, errors
```

- [ ] **Step 2: Run, verify it passes**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_rail_registry.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all 4 tests pass. (`content` is a declared `PipelineState` field so the rails' `requires=("content",)` validates from seed_keys; `qa_rail_reviews` is produced by the rails upstream of `aggregate`. If `test_fanout_spec_validates` fails on `content`, confirm Task 1 added `qa_rail_reviews` to PipelineState and that `content` is still a PipelineState field.)

- [ ] **Step 3: Final full-suite sweep for the plan + commit**

Run: `"<venv-python>" -m pytest tests/unit/services/atoms/test_qa_rail_common.py tests/unit/services/atoms/test_qa_aggregate_atom.py tests/unit/services/atoms/test_qa_deepeval_atom.py tests/unit/services/atoms/test_qa_guardrails_atom.py tests/unit/services/atoms/test_qa_ragas_atom.py tests/unit/services/atoms/test_qa_critic_atom.py tests/unit/services/atoms/test_qa_rail_registry.py tests/unit/services/test_pipeline_architect_validate.py -p no:cacheprovider > test_out.txt 2>&1` then read `test_out.txt`.
Expected: all pass.

```bash
git add src/cofounder_agent/tests/unit/services/atoms/test_qa_rail_registry.py
git commit -m "test(qa): qa.* rail registry discovery + fan-out validation (#355)"
```

---

## Self-review notes

- **Spec coverage:** implements the spec's "Granularity refactor (#362 content)" — `cross_model_qa → qa.deepeval / qa.guardrails / qa.ragas / qa.critic + qa.aggregate`, each with declared `AtomMeta` (`requires`/`produces`/`capability_tier`), `parallelizable=True` on the rails. Honors the granularity principle (meaningful-action granularity: deepeval's 3 sub-rails grouped into one `qa.deepeval`, refine finer later). **Additive + dormant:** the live `canonical_blog` pipeline still runs the `cross_model_qa` stage (Plan 4 repoints the graph_def); these atoms are discovered + cataloged but executed by no template yet.
- **Wrapping, not reimplementing:** each rail atom delegates to the existing `MultiModelQA._check_*` / `_review_with_cloud_model` methods — zero rail logic is duplicated, so the rails can't drift from the source. `qa.aggregate` reproduces only the stage's _core_ decision (weighted-score + non-advisory-veto + threshold); the validator-warning penalty + web-factcheck override are intentionally omitted (they depend on non-OSS rails) — permitted by the spec's "no parity check" clause.
- **Channel design:** the rails accumulate into a NEW `qa_rail_reviews` channel (`Annotated[list, operator.add]`), distinct from the existing critic-lineage `qa_reviews` (different dict shape), so Plan 4 can fan the rails out in parallel without `InvalidUpdateError`. `qa.aggregate` consumes `qa_rail_reviews` and emits the standard `qa_final_score`/`qa_final_verdict` (+ `_halt` on reject, which `build_graph_from_spec`'s halt-aware router honors).
- **Naming/discovery:** atoms are flat files (`qa_deepeval.py` …) with dotted `ATOM_META.name` (`qa.deepeval` …) — valid because `atom_registry` keys on `ATOM_META.name`, and `pkgutil.iter_modules` discovers flat modules. `_qa_rail_common.py` is underscore-prefixed so it's skipped by discovery (a helper, not an atom).
- **Type consistency:** `reviewer_to_dict(r) -> dict`; `aggregate_rail_reviews(reviews, *, validator_weight, critic_weight, gate_weight, threshold) -> dict` with keys `qa_final_score`/`qa_final_verdict`/`approved`/`vetoed_by`; every atom is `async def run(state: dict) -> dict` returning a `{"qa_rail_reviews": [...]}` delta (or `{}`); `qa.aggregate` returns `qa_final_score`/`qa_final_verdict` (+ `_halt`). `ReviewerResult` is mutated (`.advisory = True`) only on the OSS rails — `ReviewerResult` is a non-frozen dataclass so this is valid.
- **No placeholders:** every step has concrete code + run command + expected output. Tests use monkeypatch on the `MultiModelQA._check_*` seams so no DeepEval/guardrails/Ragas/Ollama runs in CI; the pure aggregation is tested directly.
- **Blast radius:** five new atom files + one helper module + one additive `PipelineState` field + tests. No existing production code path changes behavior — the `cross_model_qa` stage and `multi_model_qa.py` are untouched; nothing references the new atoms yet.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
