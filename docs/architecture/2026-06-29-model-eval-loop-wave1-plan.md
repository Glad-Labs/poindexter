# Model Eval Loop — Plan 1: Eval Core (Reranker Vertical Slice) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: ✅ Shipped 2026-06-29.** All 9 tasks landed (`services/model_eval/` + `poindexter/cli/model_eval.py`, 42 tests). Operator runbook: [`../operations/model-eval.md`](../operations/model-eval.md). Next: Plan 2 (discovery + gating), Plan 3 (Embedding/STT scorers).

**Goal:** Build the champion–challenger eval core end-to-end on the single stateless slot `rag_rerank_model`, proving the architecture before discovery/gating (Plan 2) and the remaining Wave-1 scorers (Plan 3).

**Architecture:** A pluggable `Scorer` computes a deterministic metric (nDCG/MRR) for a model against a versioned golden set; an `EvalHarness` (storage seam, Langfuse-backed with a Postgres fallback noted) stores runs/scores; a `runner` orchestrates champion + challengers and emits an `EvalReport`; promotion is PR-based, with opt-in direct auto-swap for this stateless slot. A `poindexter model-eval` CLI is the operator surface.

**Tech Stack:** Python 3.12, `sentence-transformers` (CrossEncoder, already a dep), `langfuse` ^4.6 (already a dep), Click CLI, pytest. Design spec: [`2026-06-29-model-eval-loop-design.md`](2026-06-29-model-eval-loop-design.md).

## Global Constraints

- **Commercial-license-only** for any model the system would ever propose (gating is Plan 2; Plan 1 only evaluates models already pinned/seeded).
- **Config in DB, not code:** every tunable (`model_eval_promotion_margin`, golden-set size, `rag_rerank_*`) is an `app_settings` key seeded in `services/settings_defaults.py`, never a literal. New settings go in `settings_defaults.py` (NOT a migration).
- **app_settings values are NEVER NULL** — use `''` as the unset sentinel.
- **SiteConfig via DI** — services take `site_config` as a constructor/method arg; never a module singleton or `set_site_config`. Secrets (`*_secret_key`) via `get_secret` (async) only; non-secret keys via `site_config.get`.
- **Fail loud + notify** on missing required config; **self-heal** (skip cycle, log, no crash) on transient infra errors. No silent fallbacks.
- **Storage seam:** the `Scorer` computes; the `EvalHarness` stores. The Langfuse adapter must sit behind the `EvalHarness` Protocol so a Postgres impl is a drop-in (do NOT let Langfuse types leak into the runner/scorer/CLI).
- **Adapter-purity:** CLI/routes hold no business logic or raw SQL — they delegate to `services/model_eval/*`.
- **All changes via PR** against `origin` (Glad-Labs/glad-labs-stack), branch off `origin/main`, never push `main`. CI-green is the merge gate. Linear history (squash/rebase).
- **Docs + tests ship with every task.**

---

### Task 1: Settings + core types (`Scorer`, `MetricResult`, `GoldenSet`)

**Files:**

- Create: `src/cofounder_agent/services/model_eval/__init__.py` (empty package marker)
- Create: `src/cofounder_agent/services/model_eval/types.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (add keys + value_type metadata)
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_types.py`

**Interfaces:**

- Produces: `MetricResult` (frozen dataclass), `GoldenCase` / `GoldenSet` (frozen dataclasses), `Scorer` (Protocol with `capability: str`, `primary_metric: str`, `score(*, model, golden_set, site_config) -> MetricResult`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/model_eval/test_types.py
from services.model_eval.types import MetricResult, GoldenCase, GoldenSet

def test_metric_result_is_frozen_and_carries_provenance():
    r = MetricResult(slot="rag_rerank_model", model="x", metric_name="ndcg@10",
                     value=0.83, n_cases=50, latency_ms=1200, detail={})
    assert r.value == 0.83 and r.n_cases == 50
    import dataclasses
    assert dataclasses.is_frozen(type(r)) if hasattr(dataclasses, "is_frozen") else True

def test_golden_set_groups_cases():
    gs = GoldenSet(name="reranker-v1", version=1,
                   cases=[GoldenCase(query="q", candidates=[{"doc_id": "a", "text": "t", "relevance": 1}])])
    assert gs.name == "reranker-v1" and len(gs.cases) == 1
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/unit/services/model_eval/test_types.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement `types.py`**

```python
# services/model_eval/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

@dataclass(frozen=True)
class GoldenCase:
    query: str
    candidates: list[dict[str, Any]]  # each: {"doc_id": str, "text": str, "relevance": int}

@dataclass(frozen=True)
class GoldenSet:
    name: str
    version: int
    cases: list[GoldenCase]

@dataclass(frozen=True)
class MetricResult:
    slot: str
    model: str
    metric_name: str
    value: float
    n_cases: int
    latency_ms: int
    detail: dict[str, Any] = field(default_factory=dict)

@runtime_checkable
class Scorer(Protocol):
    capability: str        # e.g. "reranker"
    primary_metric: str    # e.g. "ndcg@10"
    def score(self, *, model: str, golden_set: GoldenSet, site_config: Any) -> MetricResult: ...
```

- [ ] **Step 4: Add settings** to `services/settings_defaults.py` — in `DEFAULTS`:

```python
    'model_eval_promotion_margin': '0.02',          # rel. improvement a challenger must beat champion by
    'model_eval_reranker_golden_size': '50',        # # of golden cases to bootstrap
    'model_eval_reranker_candidates_per_case': '20', # candidate docs ranked per query
```

and in the value-type metadata block:

```python
    'model_eval_promotion_margin': {'owner': 'model_eval', 'value_type': 'float'},
    'model_eval_reranker_golden_size': {'owner': 'model_eval', 'value_type': 'int'},
    'model_eval_reranker_candidates_per_case': {'owner': 'model_eval', 'value_type': 'int'},
```

- [ ] **Step 5: Run + commit** — `pytest tests/unit/services/model_eval/test_types.py -v` → PASS; then `git add services/model_eval/ services/settings_defaults.py tests/unit/services/model_eval/test_types.py && git commit -m "feat(model-eval): core types + settings for eval loop"`.

---

### Task 2: Ranking metrics (`ndcg@k`, `mrr`) — pure & deterministic

**Files:**

- Create: `src/cofounder_agent/services/model_eval/metrics.py`
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_metrics.py`

**Interfaces:**

- Produces: `ndcg_at_k(ranked_relevances: list[float], k: int) -> float`, `mrr(ranked_is_relevant: list[bool]) -> float`.

- [ ] **Step 1: Write failing tests** (hand-computed expected values — these are the ground truth the whole system rests on):

```python
# tests/unit/services/model_eval/test_metrics.py
import math
from services.model_eval.metrics import ndcg_at_k, mrr

def test_ndcg_perfect_ranking_is_1():
    assert ndcg_at_k([3, 2, 1], k=3) == 1.0

def test_ndcg_worst_ranking_below_1():
    # reversed relevances -> dcg < idcg
    assert ndcg_at_k([1, 2, 3], k=3) < 1.0

def test_ndcg_known_value():
    # dcg = 1/log2(2) + 0/log2(3) + 1/log2(4) = 1 + 0 + 0.5 = 1.5
    # idcg(k=3 over [1,1,0]) = 1/log2(2) + 1/log2(3) = 1 + 0.6309 = 1.6309
    got = ndcg_at_k([1, 0, 1], k=3)
    assert math.isclose(got, 1.5 / (1 + 1 / math.log2(3)), rel_tol=1e-9)

def test_mrr_first_relevant_at_rank_3():
    assert mrr([False, False, True]) == 1 / 3

def test_mrr_none_relevant_is_0():
    assert mrr([False, False]) == 0.0
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/unit/services/model_eval/test_metrics.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement `metrics.py`**

```python
# services/model_eval/metrics.py
from __future__ import annotations
import math

def _dcg(relevances: list[float], k: int) -> float:
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))

def ndcg_at_k(ranked_relevances: list[float], k: int) -> float:
    """nDCG@k. `ranked_relevances` are the graded relevances in the order the
    model ranked them. iDCG is computed from the best possible ordering of the
    same relevance values."""
    idcg = _dcg(sorted(ranked_relevances, reverse=True), k)
    if idcg == 0:
        return 0.0
    return _dcg(ranked_relevances, k) / idcg

def mrr(ranked_is_relevant: list[bool]) -> float:
    """Reciprocal rank of the first relevant item (0.0 if none)."""
    for i, is_rel in enumerate(ranked_is_relevant):
        if is_rel:
            return 1.0 / (i + 1)
    return 0.0
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/unit/services/model_eval/test_metrics.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add services/model_eval/metrics.py tests/unit/services/model_eval/test_metrics.py && git commit -m "feat(model-eval): nDCG@k + MRR ranking metrics"`.

---

### Task 3: `RerankerScorer`

**Files:**

- Create: `src/cofounder_agent/services/model_eval/scorers/__init__.py`
- Create: `src/cofounder_agent/services/model_eval/scorers/reranker.py`
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_reranker_scorer.py`

**Interfaces:**

- Consumes: `MetricResult`, `GoldenSet` (Task 1); `ndcg_at_k`, `mrr` (Task 2).
- Produces: `RerankerScorer` (implements `Scorer`; `capability="reranker"`, `primary_metric="ndcg@10"`).

**Note on model invocation:** mirror [`rag_engine.py:605` `CrossEncoderRerankRetriever`](../../src/cofounder_agent/services/rag_engine.py) — `from sentence_transformers import CrossEncoder; CrossEncoder(model_name, device).predict([(query, doc_text), ...])` returns a relevance score per pair; device from `rag_rerank_device` (default `cpu`). Cache the loaded encoder by `(name, device)` as `rag_engine` does.

- [ ] **Step 1: Write the failing test** (inject a fake CrossEncoder so the test is deterministic and offline):

```python
# tests/unit/services/model_eval/test_reranker_scorer.py
from services.model_eval.types import GoldenSet, GoldenCase
from services.model_eval.scorers.reranker import RerankerScorer

class _FakeEncoder:
    # returns higher score for docs whose text starts with "good"
    def predict(self, pairs):
        return [1.0 if doc.startswith("good") else 0.0 for (_q, doc) in pairs]

def _site_config():
    from services.site_config import SiteConfig
    return SiteConfig(initial_config={"rag_rerank_device": "cpu"})

def test_reranker_scorer_perfect_when_relevant_ranked_first():
    gs = GoldenSet(name="r", version=1, cases=[GoldenCase(
        query="q",
        candidates=[{"doc_id": "1", "text": "good doc", "relevance": 1},
                    {"doc_id": "2", "text": "bad doc", "relevance": 0}])])
    scorer = RerankerScorer(encoder_factory=lambda name, device: _FakeEncoder())
    result = scorer.score(model="cross-encoder/x", golden_set=gs, site_config=_site_config())
    assert result.metric_name == "ndcg@10"
    assert result.value == 1.0
    assert result.n_cases == 1
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/unit/services/model_eval/test_reranker_scorer.py -v` → FAIL.

- [ ] **Step 3: Implement `reranker.py`** (constructor takes an `encoder_factory` defaulting to the real CrossEncoder loader, so tests inject a fake):

```python
# services/model_eval/scorers/reranker.py
from __future__ import annotations
import time
from typing import Any, Callable
from services.model_eval.types import GoldenSet, MetricResult
from services.model_eval.metrics import ndcg_at_k, mrr

_K = 10

def _default_encoder_factory(name: str, device: str):
    from sentence_transformers import CrossEncoder  # heavy import; lazy
    return CrossEncoder(name, device=device)

class RerankerScorer:
    capability = "reranker"
    primary_metric = f"ndcg@{_K}"

    def __init__(self, *, encoder_factory: Callable[[str, str], Any] = _default_encoder_factory) -> None:
        self._encoder_factory = encoder_factory

    def score(self, *, model: str, golden_set: GoldenSet, site_config: Any) -> MetricResult:
        device = (site_config.get("rag_rerank_device", "cpu") or "cpu").strip()
        encoder = self._encoder_factory(model, device)
        t0 = time.monotonic()
        ndcgs: list[float] = []
        mrrs: list[float] = []
        for case in golden_set.cases:
            pairs = [(case.query, c["text"]) for c in case.candidates]
            scores = list(encoder.predict(pairs))
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            ranked_rel = [float(case.candidates[i]["relevance"]) for i in order]
            ndcgs.append(ndcg_at_k(ranked_rel, _K))
            mrrs.append(mrr([r > 0 for r in ranked_rel]))
        n = len(golden_set.cases)
        avg_ndcg = sum(ndcgs) / n if n else 0.0
        return MetricResult(
            slot="rag_rerank_model", model=model, metric_name=self.primary_metric,
            value=avg_ndcg, n_cases=n, latency_ms=int((time.monotonic() - t0) * 1000),
            detail={"mrr": (sum(mrrs) / n if n else 0.0), "golden_version": golden_set.version},
        )
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/unit/services/model_eval/test_reranker_scorer.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add services/model_eval/scorers/ tests/unit/services/model_eval/test_reranker_scorer.py && git commit -m "feat(model-eval): RerankerScorer (nDCG@10 over a golden set)"`.

---

### Task 4: `EvalHarness` seam + Langfuse adapter (with API spike)

**Files:**

- Create: `src/cofounder_agent/services/model_eval/harness.py`
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_harness.py`

**Interfaces:**

- Consumes: `MetricResult`, `GoldenSet` (Task 1).
- Produces: `EvalHarness` (Protocol: `ensure_dataset(golden_set) -> str`, `record_results(run_name, results: list[MetricResult]) -> None`, `latest_by_model(slot, metric_name) -> dict[str, float]`), `LangfuseEvalHarness` (impl), `InMemoryEvalHarness` (test/fallback double).

- [ ] **Step 1: SPIKE (research step — not a placeholder).** Confirm the Langfuse v4.6 dataset-run + score SDK surface against the installed package:

```bash
poetry run python -c "import langfuse, inspect; c=langfuse.Langfuse; print([m for m in dir(c) if 'dataset' in m.lower() or 'score' in m.lower()])"
poetry run python -c "from langfuse import Langfuse; help(Langfuse.create_dataset_item)" 2>&1 | head -40
```

Record the exact method names/signatures for: create dataset, create dataset item, start a dataset _run_, and attach a _score_ to a run/trace. Use the Langfuse client init pattern already proven in [`langfuse_experiments.py:136`](../../src/cofounder_agent/services/langfuse_experiments.py) (`Langfuse(host=, public_key=, secret_key=)` from `site_config`). Write the confirmed calls into Step 3.

- [ ] **Step 2: Write the failing test** against the `InMemoryEvalHarness` double (the seam is what we test; Langfuse calls are mocked):

```python
# tests/unit/services/model_eval/test_harness.py
from services.model_eval.types import MetricResult
from services.model_eval.harness import InMemoryEvalHarness

def test_in_memory_harness_roundtrips_latest():
    h = InMemoryEvalHarness()
    h.record_results("run-1", [
        MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.80, 10, 5, {}),
        MetricResult("rag_rerank_model", "chall", "ndcg@10", 0.86, 10, 6, {}),
    ])
    latest = h.latest_by_model("rag_rerank_model", "ndcg@10")
    assert latest == {"champ": 0.80, "chall": 0.86}
```

- [ ] **Step 3: Implement `harness.py`** — the Protocol, the `InMemoryEvalHarness`, and `LangfuseEvalHarness` using the spike-confirmed calls. Langfuse client lazy-init + fail-loud on missing creds, copied from the `_get_client` pattern in `langfuse_experiments.py`. **No Langfuse type may appear in any return value** (return plain dicts/`MetricResult`).

```python
# services/model_eval/harness.py  (skeleton — fill Langfuse calls from Step 1 spike)
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable
from services.model_eval.types import GoldenSet, MetricResult

@runtime_checkable
class EvalHarness(Protocol):
    def ensure_dataset(self, golden_set: GoldenSet) -> str: ...
    def record_results(self, run_name: str, results: list[MetricResult]) -> None: ...
    def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]: ...

class InMemoryEvalHarness:
    def __init__(self) -> None:
        self._runs: list[tuple[str, list[MetricResult]]] = []
    def ensure_dataset(self, golden_set: GoldenSet) -> str:
        return f"{golden_set.name}@{golden_set.version}"
    def record_results(self, run_name: str, results: list[MetricResult]) -> None:
        self._runs.append((run_name, list(results)))
    def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for _run, results in self._runs:
            for r in results:
                if r.slot == slot and r.metric_name == metric_name:
                    out[r.model] = r.value
        return out

class LangfuseEvalHarness:
    def __init__(self, *, site_config: Any) -> None:
        self._site_config = site_config
        self._client: Any = None
    def _get_client(self) -> Any:
        # copy the lazy/fail-loud pattern from langfuse_experiments._get_client
        ...
    def ensure_dataset(self, golden_set: GoldenSet) -> str:
        ...  # client.create_dataset(...) + create_dataset_item(...) per spike
    def record_results(self, run_name: str, results: list[MetricResult]) -> None:
        ...  # one dataset run; client.create_score(name=metric_name, value=..., metadata=...)
    def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]:
        ...  # read run scores back; return plain {model: value}
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/unit/services/model_eval/test_harness.py -v` → PASS (the `LangfuseEvalHarness` path is covered by a mocked-client test you add alongside).

- [ ] **Step 5: Commit** — `git add services/model_eval/harness.py tests/unit/services/model_eval/test_harness.py && git commit -m "feat(model-eval): EvalHarness seam + Langfuse adapter + in-memory double"`.

---

### Task 5: Reranker golden-set bootstrap (from production posts)

**Files:**

- Create: `src/cofounder_agent/services/model_eval/golden_sets/__init__.py`
- Create: `src/cofounder_agent/services/model_eval/golden_sets/reranker.py`
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_reranker_golden.py`

**Interfaces:**

- Consumes: `GoldenSet`, `GoldenCase` (Task 1).
- Produces: `async build_reranker_golden_set(*, pool, site_config) -> GoldenSet`.

**Design:** mine (query → relevant doc) from `posts`: title-as-query, own body chunk = relevant (`relevance=1`); fill each case's candidate list with `model_eval_reranker_candidates_per_case - 1` _distractor_ chunks sampled from _other_ posts (`relevance=0`). Size = `model_eval_reranker_golden_size`. **No dummy data** — if there aren't enough posts, fail loud with the count.

- [ ] **Step 1: Write the failing test** (inject a fake pool returning 3 posts; assert structure + one relevant per case).
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement `reranker.py`** — async DB read via `pool.acquire()` (raw SQL is allowed in a service, not in CLI/routes), assemble `GoldenCase`s, shuffle distractors with a fixed seed for reproducibility, version the set by a hash of the source post ids.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(model-eval): bootstrap reranker golden set from posts"`.

_(Full test + impl code written inline at execution time following the Task 1–3 shape; the query/relevant/distractor rule above is the complete spec.)_

---

### Task 6: Eval runner + `EvalReport` comparison

**Files:**

- Create: `src/cofounder_agent/services/model_eval/runner.py`
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_runner.py`

**Interfaces:**

- Consumes: `Scorer`, `MetricResult`, `GoldenSet` (Task 1); `EvalHarness` (Task 4).
- Produces: `EvalReport` (frozen dataclass: `slot`, `champion`, `champion_score`, `best_challenger`, `best_challenger_score`, `winner`, `margin`, `beats_margin: bool`), `run_slot_eval(*, slot, champion, challengers, scorer, golden_set, harness, site_config, promotion_margin) -> EvalReport`.

- [ ] **Step 1: Write the failing test** — stub scorer returns fixed scores (champ 0.80, challenger 0.86); margin 0.02; assert `winner == challenger` and `beats_margin is True`. Add a second case where challenger=0.805 → `beats_margin is False` (within margin).

```python
# tests/unit/services/model_eval/test_runner.py
from services.model_eval.types import MetricResult, GoldenSet
from services.model_eval.harness import InMemoryEvalHarness
from services.model_eval.runner import run_slot_eval

class _StubScorer:
    capability = "reranker"; primary_metric = "ndcg@10"
    def __init__(self, table): self._t = table
    def score(self, *, model, golden_set, site_config):
        return MetricResult("rag_rerank_model", model, "ndcg@10", self._t[model], 1, 1, {})

def test_runner_flags_challenger_beating_margin():
    gs = GoldenSet("r", 1, [])
    rep = run_slot_eval(slot="rag_rerank_model", champion="champ", challengers=["chall"],
                        scorer=_StubScorer({"champ": 0.80, "chall": 0.86}),
                        golden_set=gs, harness=InMemoryEvalHarness(),
                        site_config=None, promotion_margin=0.02)
    assert rep.winner == "chall" and rep.beats_margin is True
```

- [ ] **Step 2–4:** verify fail → implement (`relative margin = (best - champ) / champ`; record results to harness; pick best challenger; set `beats_margin`) → verify pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(model-eval): eval runner + EvalReport comparison"`.

---

### Task 7: Promotion (PR proposal + opt-in stateless auto-swap)

**Files:**

- Create: `src/cofounder_agent/services/model_eval/promotion.py`
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_promotion.py`

**Interfaces:**

- Consumes: `EvalReport` (Task 6).
- Produces: `propose_promotion(*, report, site_config) -> PromotionProposal | None` (returns `None` unless `report.beats_margin`); `PromotionProposal` (slot, from_model, to_model, metric_delta, kind: `"pr"` | `"auto_swap"`, body: str). Auto-swap only when `app_settings.{slot}_auto_promote == 'true'` AND the slot is stateless (reranker).

- [ ] **Step 1: Write failing tests** — (a) `beats_margin=False` → `None`; (b) default settings → `kind == "pr"` with a body containing both model names + the delta; (c) `rag_rerank_model_auto_promote='true'` → `kind == "auto_swap"`.
- [ ] **Step 2–4:** verify fail → implement (PR body is a formatted markdown report; actual PR creation/`gh` invocation and the auto-swap `settings set` are wired in Task 9, this module only _decides_ + _renders_) → verify pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(model-eval): promotion proposal (PR + opt-in stateless auto-swap)"`.

---

### Task 8: CLI `poindexter model-eval`

**Files:**

- Create: `src/cofounder_agent/poindexter/cli/model_eval.py`
- Modify: the CLI group registrar that mounts subcommands (same place `experiments_group` is registered — grep `experiments_group` to find it)
- Test: `src/cofounder_agent/tests/unit/cli/test_model_eval_cli.py`

**Interfaces:**

- Consumes: `run_slot_eval` (Task 6), `build_reranker_golden_set` (Task 5), `LangfuseEvalHarness` (Task 4), `RerankerScorer` (Task 3), `propose_promotion` (Task 7).

**Pattern:** copy [`poindexter/cli/experiments.py`](../../src/cofounder_agent/poindexter/cli/experiments.py) exactly — Click group, lazy `import asyncpg` in each `_impl()`, DSN via `poindexter.cli._bootstrap.resolve_dsn`, `asyncio.run(_impl())`, thin adapter (no business logic). Tests patch `asyncpg.create_pool` like `test_experiments_cli.py`.

- [ ] **Step 1: Write the failing test** — `CliRunner().invoke(model_eval_group, ["run", "--slot", "rag_rerank_model", "--challenger", "cross-encoder/foo"])` with the service layer patched; assert it prints the `EvalReport` summary and exits 0.
- [ ] **Step 2–4:** verify fail → implement `run` subcommand (builds golden set → constructs `RerankerScorer` + `LangfuseEvalHarness` via `site_config` → `run_slot_eval` → `propose_promotion` → prints) + a `status` subcommand (reads `harness.latest_by_model`) → verify pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(model-eval): poindexter model-eval CLI (run/status)"`.

---

### Task 9: Wire-up, docs, integration test

**Files:**

- Modify: `docs/architecture/2026-06-29-model-eval-loop-design.md` (mark Plan 1 shipped; link this plan)
- Create: `docs/operations/model-eval.md` (operator runbook: how to run a reranker bakeoff, read the result in Langfuse, promote)
- Test: `src/cofounder_agent/tests/unit/services/model_eval/test_loop_integration.py`

- [ ] **Step 1: Write the failing integration test** — full loop on a 3-case in-memory golden set with a fake encoder + `InMemoryEvalHarness`: champion (weak fake) vs challenger (strong fake) → assert `EvalReport.winner == challenger`, `beats_margin is True`, and `propose_promotion` returns a `kind=="pr"` proposal.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** any glue the test reveals (e.g. a `run_reranker_bakeoff(...)` convenience in `runner.py` that the CLI and test share — DRY).
- [ ] **Step 4: Run the full module suite** — `pytest tests/unit/services/model_eval/ tests/unit/cli/test_model_eval_cli.py -v` → all PASS. Then `npm run type:check` (mypy) on the new package.
- [ ] **Step 5: Commit + open PR** — `git commit -m "test(model-eval): end-to-end loop integration + operator runbook"` then push the branch to `origin` and open a PR against `main` (CI-green is the gate).

---

## Self-Review

**Spec coverage (vs `2026-06-29-model-eval-loop-design.md`):**

- §4 loop stages Evaluate/Promote → Tasks 6/7. Discover/Gate → **deferred to Plan 2** (intentional; Plan 1 seeds the one challenger via CLI `--challenger`).
- §5 Langfuse harness behind storage seam → Task 4 (Protocol + Langfuse impl + in-memory double; no Langfuse types leak).
- §6 `Scorer` interface + reranker → Tasks 1/3. §7 golden set from prod data → Task 5.
- §8 promotion: Wave-1 PR-based + reranker opt-in auto-swap → Task 7. Embeddings re-embed migration + STT → **Plan 3**.
- §10 `@observe`-bug orthogonality → respected (harness uses explicit dataset/score calls only; Task 4 spike confirms).
- §12 error handling (fail-loud creds, self-heal) → Task 4 `_get_client`; §13 testing → every task is TDD.

**Placeholder scan:** Tasks 5–8 compress Steps 2–4 to descriptions + complete _interfaces/specs_ rather than full code, because they follow the verbatim shape of Tasks 1–3 and the existing `experiments.py`/`rag_engine.py` references; the decision-bearing logic (metric math, scorer, comparison, promotion rules) is fully specified. Task 4 Step 3 is a deliberate spike-then-fill, not a placeholder. No "TBD/add error handling/handle edge cases" left.

**Type consistency:** `MetricResult`, `GoldenSet`/`GoldenCase`, `Scorer`, `EvalHarness`, `EvalReport`, `PromotionProposal` names + signatures are consistent across Tasks 1→9. `slot="rag_rerank_model"` is the single literal used throughout.

---

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, fast iteration.
2. **Inline Execution** — execute in this session with checkpoints.
