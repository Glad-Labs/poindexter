# Semantic Topic Dedup Activation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **REVISION (2026-07-12):** Escalated Workstream 1 from title-based to
> **content-embedding** dedup (spec §1.4). Calibration showed title similarity
> missed "The VRAM Currency Problem" (0.55 title vs 0.735 content) — the post the
> trigger draft most re-stated. Tasks 1–2 (CPU-pin + `semantic` engine) are kept
> as a fallback engine, but the **default is now `content_embedding`**, a new
> engine reusing the proven `topic_dedup_guard`/`find_similar_posts` content
> search at threshold **0.70** (calibrated: VRAM cluster ≥0.65, unrelated
> controls ≤0.60). See `services/topic_dedup_content.py`.

**Goal:** Activate the already-built `SemanticDeduplicator` as the default topic-dedup engine — CPU-pinned and threshold-calibrated — so semantically-duplicate topics (e.g. the VRAM cluster) are suppressed at proposal time instead of spawning near-duplicate posts.

**Architecture:** The `SemanticDeduplicator` (`services/topic_dedup_semantic.py`, `all-MiniLM-L6-v2`, cosine) is already built, unit-tested, and wired through `get_deduplicator` on both proposal paths. This plan (1) pins its model to CPU (it currently defaults to CUDA), (2) flips the seeded engine default `word_overlap` → `semantic`, (3) calibrates the similarity threshold against the real published corpus, and (4) flips the live prod value. No new wiring.

**Tech Stack:** Python 3.13, `sentence-transformers` (already a dependency), `numpy`, asyncpg, pytest.

## Global Constraints

- **Test runner (fresh worktree has no venv):** run pytest with the MAIN checkout's poetry venv python, from the worktree's backend dir, disabling repo addopts:
  `VENV_PY="C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe"`
  `cd "<worktree>/src/cofounder_agent" && "$VENV_PY" -m pytest <path> -o addopts="" -p no:cacheprovider -q`
- **Settings defaults belong in `services/settings_defaults.py`** (`DEFAULTS` dict), NOT migration files — seeded every boot via `INSERT … ON CONFLICT (key) DO NOTHING`.
- **`app_settings` values are strings and never NULL** — use `''` for unset, quote numbers (`'0.72'`).
- **No hardcoded config in code** — every tunable is an `app_settings` key with a sensible default.
- **Config already-seeded on prod won't move on boot** (ON CONFLICT DO NOTHING) — a live value change is a separate `set_setting` deploy step.
- **Conventional commits**, end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Branch/PR:** work on the current feature branch; this plan is PR 1 of the RAG-self-echo effort (draft PR #2359). Never push main.

---

### Task 1: Pin the embedding model to CPU

The dedup model must not land on the GPU this whole effort is protecting. `_get_model` currently calls `SentenceTransformer(model_name)` with no `device` → defaults to CUDA when a GPU is present.

**Files:**

- Modify: `src/cofounder_agent/services/topic_dedup_semantic.py` (`_get_model`, `_embed`, `_embed_async`; add `_get_device`)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (add `topic_dedup_device`)
- Test: `src/cofounder_agent/tests/unit/services/test_topic_dedup_semantic.py`

**Interfaces:**

- Produces: `_get_model(model_name: str, device: str = "cpu") -> Any` (cache keyed on `(model_name, device)`); `SemanticDeduplicator._get_device() -> str` (reads `topic_dedup_device`, default `"cpu"`).

- [ ] **Step 1: Write the failing tests**

Add to `test_topic_dedup_semantic.py`:

```python
@pytest.mark.unit
class TestCpuPin:
    def test_get_model_passes_device_to_sentence_transformer(self):
        import services.topic_dedup_semantic as mod
        mod._model_cache.clear()
        fake_st = MagicMock()
        # _get_model does `from sentence_transformers import SentenceTransformer`
        # inside the function, so patch it at its source module.
        with patch("sentence_transformers.SentenceTransformer", fake_st):
            mod._get_model("all-MiniLM-L6-v2", "cpu")
        fake_st.assert_called_once_with("all-MiniLM-L6-v2", device="cpu")
        mod._model_cache.clear()

    def test_get_device_defaults_to_cpu(self):
        assert _make_dedup()._get_device() == "cpu"

    def test_get_device_reads_setting(self):
        assert _make_dedup({"topic_dedup_device": "cuda"})._get_device() == "cuda"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "<worktree>/src/cofounder_agent" && "$VENV_PY" -m pytest tests/unit/services/test_topic_dedup_semantic.py::TestCpuPin -o addopts="" -p no:cacheprovider -q`
Expected: FAIL — `_get_model()` takes 1 positional arg / `SemanticDeduplicator` has no `_get_device`.

- [ ] **Step 3: Implement the CPU pin**

In `services/topic_dedup_semantic.py`, replace `_get_model`:

```python
def _get_model(model_name: str, device: str = "cpu") -> Any:
    """Lazy-load + cache a sentence-transformer model on ``device``.

    Thread-safe: two callers racing on the same (name, device) see only one
    load; subsequent calls skip the lock entirely (fast path). Pinned to CPU
    by default so topic dedup never competes for VRAM with the inference
    pipeline (mirrors the rag_rerank_device reranker-to-CPU fix).
    """
    key = (model_name, device)
    if key in _model_cache:
        return _model_cache[key]
    with _model_lock:
        if key not in _model_cache:
            from sentence_transformers import SentenceTransformer
            logger.info(
                "[topic_dedup_semantic] Loading sentence-transformer: %s on %s "
                "(first call — subsequent calls reuse the cached model)",
                model_name, device,
            )
            _model_cache[key] = SentenceTransformer(model_name, device=device)
    return _model_cache[key]
```

Add a `_get_device` method to `SemanticDeduplicator` (next to `_get_model_name`):

```python
    def _get_device(self) -> str:
        """Device for the dedup embedding model. Default 'cpu' so it never
        competes with the inference pipeline for VRAM."""
        try:
            return self._site_config.get("topic_dedup_device", "cpu") or "cpu"
        except Exception:
            return "cpu"
```

Update `_embed` and `_embed_async` to pass the device:

```python
    def _embed(self, texts: list[str]) -> Any:
        """Encode texts to a (N, D) numpy array. Synchronous — internal use only."""
        model = _get_model(self._get_model_name(), self._get_device())
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    async def _embed_async(self, texts: list[str]) -> Any:
        """Encode texts via asyncio.to_thread so the blocking encode call does
        not freeze the event loop. Returns a (N, D) numpy array."""
        model = _get_model(self._get_model_name(), self._get_device())
        return await asyncio.to_thread(
            model.encode, texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
```

In `services/settings_defaults.py`, add under the Topic dedup block (after line 809, `topic_dedup_intra_batch_threshold`):

```python
    # Device for the semantic topic-dedup model (all-MiniLM). Default 'cpu' so
    # dedup never competes with the inference pipeline for VRAM (mirrors
    # rag_rerank_device). Set 'cuda' only on a box with spare VRAM.
    'topic_dedup_device': 'cpu',
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "<worktree>/src/cofounder_agent" && "$VENV_PY" -m pytest tests/unit/services/test_topic_dedup_semantic.py -o addopts="" -p no:cacheprovider -q`
Expected: PASS (all TestCpuPin + the pre-existing engine/intra-batch/vs-existing tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_dedup_semantic.py src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_topic_dedup_semantic.py
git commit -m "fix(topic-dedup): pin semantic dedup model to CPU (topic_dedup_device)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Flip the seeded engine default to semantic

Flip the DB-seeded default so all installs get semantic. Leave `get_deduplicator`'s hardcoded fallback as `word_overlap` (conservative when no setting row exists at all) — real installs always run `seed_all_defaults`, so they get semantic.

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py:807`
- Test: `src/cofounder_agent/tests/unit/services/test_topic_dedup_semantic.py`

**Interfaces:**

- Consumes: nothing new. Produces: `DEFAULTS['topic_dedup_engine'] == 'semantic'`.

- [ ] **Step 1: Write the failing test**

Add to `TestEngineSelector` in `test_topic_dedup_semantic.py`:

```python
    def test_seeded_default_is_semantic(self):
        from services.settings_defaults import DEFAULTS
        assert DEFAULTS["topic_dedup_engine"] == "semantic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "<worktree>/src/cofounder_agent" && "$VENV_PY" -m pytest "tests/unit/services/test_topic_dedup_semantic.py::TestEngineSelector::test_seeded_default_is_semantic" -o addopts="" -p no:cacheprovider -q`
Expected: FAIL — `assert 'word_overlap' == 'semantic'`.

- [ ] **Step 3: Flip the default**

In `services/settings_defaults.py:807`, change:

```python
    'topic_dedup_engine': 'semantic',
```

Update the adjacent comment (line 801-805) to note semantic is now the default and word_overlap remains available as the lexical fallback.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "<worktree>/src/cofounder_agent" && "$VENV_PY" -m pytest tests/unit/services/test_topic_dedup_semantic.py -o addopts="" -p no:cacheprovider -q`
Expected: PASS (including `test_default_returns_word_overlap_engine`, which tests the code fallback with no setting — unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_topic_dedup_semantic.py
git commit -m "feat(topic-dedup): default topic_dedup_engine to semantic

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Calibrate the semantic threshold against the real corpus

Reconcile the threshold inconsistency (code `0.65` vs docstring `0.85`, `topic_dedup_existing_threshold_semantic` unseeded) with an empirical value, and confirm the VRAM trigger case is actually caught. **This task carries the escalation gate** (spec §1.4).

**Files:**

- Create: `scripts/calibrate_topic_dedup_threshold.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (seed calibrated thresholds)
- Modify: `src/cofounder_agent/services/topic_dedup_semantic.py` (reconcile `DEFAULT_*` constants + docstring)

**Interfaces:**

- Consumes: `_get_model`/`_get_device` from Task 1. Produces: seeded `topic_dedup_existing_threshold_semantic`, `topic_dedup_intra_batch_threshold_semantic`.

- [ ] **Step 1: Write the calibration script**

Create `scripts/calibrate_topic_dedup_threshold.py`:

```python
"""One-off: calibrate the semantic topic-dedup threshold against the live
published-title corpus and confirm the VRAM trigger case is separable.

Run with the backend poetry env:
    python scripts/calibrate_topic_dedup_threshold.py

Prints the nearest-neighbor cosine distribution over published titles, the
score the "GPU VRAM Budgeting" candidate gets against the corpus, and a
recommended threshold. Read-only against the DB.
"""
from __future__ import annotations

import asyncio
import sys

import asyncpg
import numpy as np
from sentence_transformers import SentenceTransformer

# The topic that slipped through word_overlap (task b740e4b8). Its nearest
# published neighbor MUST land above the recommended threshold or we escalate.
CANDIDATE = "GPU VRAM Budgeting for Local AI Inference"
MODEL = "all-MiniLM-L6-v2"


async def _load_titles() -> list[str]:
    from brain.bootstrap import resolve_database_url
    dsn = resolve_database_url()
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT title FROM posts WHERE status='published'")
    finally:
        await conn.close()
    return [r["title"] for r in rows if r["title"]]


def _nearest(sim_row: np.ndarray, self_idx: int) -> tuple[int, float]:
    row = sim_row.copy()
    row[self_idx] = -1.0  # exclude self
    j = int(np.argmax(row))
    return j, float(row[j])


def main() -> int:
    titles = asyncio.run(_load_titles())
    if len(titles) < 5:
        print(f"Not enough titles ({len(titles)}) to calibrate."); return 1

    model = SentenceTransformer(MODEL, device="cpu")
    embs = model.encode(titles, normalize_embeddings=True, show_progress_bar=False)
    sims = np.dot(embs, embs.T)

    nn = np.array([_nearest(sims[i], i)[1] for i in range(len(titles))])
    p50, p90, p95, p99 = (float(np.percentile(nn, p)) for p in (50, 90, 95, 99))
    print(f"Corpus nearest-neighbor cosine: p50={p50:.3f} p90={p90:.3f} "
          f"p95={p95:.3f} p99={p99:.3f} (n={len(titles)})")

    cand = model.encode([CANDIDATE], normalize_embeddings=True, show_progress_bar=False)[0]
    cand_sims = np.dot(embs, cand)
    order = np.argsort(cand_sims)[::-1][:5]
    print(f"\nCandidate: {CANDIDATE!r}")
    for j in order:
        print(f"  {cand_sims[j]:.3f}  {titles[j]!r}")
    cand_best = float(cand_sims[order[0]])

    # Recommend the threshold at the p90 of unrelated nearest-neighbor scores,
    # floored so the VRAM candidate is caught with a small margin.
    rec = round(min(p90, cand_best - 0.02), 2)
    print(f"\nRecommended topic_dedup_existing_threshold_semantic = {rec}")

    # Escalation gate (spec §1.4): if the candidate's best score can't clear a
    # sane floor without swallowing the cross-topic p90, the title signal is
    # too weak — escalate to the content-embedding signal.
    if cand_best < p90:
        print("\n*** ESCALATE: candidate best < corpus p90 — title-based signal "
              "insufficient; switch to content-embedding dedup (spec §1.4). ***")
        return 2
    print("\nOK: title-based semantic dedup separates the VRAM case.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the calibration script**

Run: `cd "<worktree>" && "$VENV_PY" scripts/calibrate_topic_dedup_threshold.py`
Expected: prints the distribution, the candidate's 5 nearest published titles (the VRAM cluster should top the list), and a `Recommended … = <X>` line. Record `<X>` (expected ballpark 0.70–0.80).

**Decision point:** if the script exits 2 (ESCALATE), STOP and switch to the content-embedding approach (spec §1.4) — do not proceed with Steps 3-5. Report the escalation to the operator.

- [ ] **Step 3: Seed the calibrated thresholds + reconcile the constants**

In `services/settings_defaults.py`, under the Topic dedup block, add (replace `<X>` with the recorded value; use the same value for both unless intra-batch calibration differs):

```python
    # Semantic-engine dedup thresholds (topic_dedup_engine='semantic'). Cosine
    # at/above which a candidate title is a near-duplicate. Calibrated
    # 2026-07-12 against the live published corpus (see
    # scripts/calibrate_topic_dedup_threshold.py); catches the VRAM cluster
    # while clearing cross-topic neighbors.
    'topic_dedup_existing_threshold_semantic': '<X>',
    'topic_dedup_intra_batch_threshold_semantic': '<X>',
```

In `services/topic_dedup_semantic.py`, set `DEFAULT_EXISTING_THRESHOLD` / `DEFAULT_INTRA_BATCH_THRESHOLD` to `<X>` and fix the class docstring so the code default, the docstring, and the seeded value all agree (removes the 0.65-vs-0.85 drift).

- [ ] **Step 4: Run the semantic dedup tests**

Run: `cd "<worktree>/src/cofounder_agent" && "$VENV_PY" -m pytest tests/unit/services/test_topic_dedup_semantic.py -o addopts="" -p no:cacheprovider -q`
Expected: PASS (threshold-override test uses an explicit value, unaffected by the default change).

- [ ] **Step 5: Commit**

```bash
git add scripts/calibrate_topic_dedup_threshold.py src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/services/topic_dedup_semantic.py
git commit -m "feat(topic-dedup): calibrate + seed semantic dedup threshold

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Flip the live prod value + verify (deploy step)

Code changes ship the new default to fresh installs; prod's existing `topic_dedup_engine='word_overlap'` row must be flipped explicitly.

**Files:** none (operational).

- [ ] **Step 1: Merge PR 1, then rebuild + restart the worker**

The CPU-pin (Task 1) is bind-mounted Python — it takes effect on worker restart:
`docker compose -f docker-compose.local.yml up -d --build poindexter-prefect-worker`

- [ ] **Step 2: Flip the live setting**

Via MCP `set_setting` (or `poindexter settings set`): `topic_dedup_engine = semantic`. Setting changes propagate on the next `reload_site_config` (~1 min) — no second restart needed.

- [ ] **Step 3: Verify**

- Confirm the live value: MCP `get_setting topic_dedup_engine` → `semantic`.
- Watch worker logs on the next topic sweep for `[DEDUP/semantic]` lines (the lexical engine logs `[DEDUP]`), and for the sentence-transformer load line reporting `on cpu`.
- Confirm no GPU VRAM bump from the dedup model (Grafana Hardware & Power / `nvidia-smi`).

---

## Self-Review

**Spec coverage (§1):** 1.1 flip engine → Task 2 + Task 4; 1.2 CPU pin → Task 1; 1.3 reconcile + calibrate → Task 3; 1.4 escalation gate → Task 3 Step 2 decision point; 1.5 tests → Tasks 1-3 test steps. All covered.

**Placeholder scan:** The only intentional `<X>` is the empirically-derived threshold, resolved by running the Task 3 Step 2 script (a deterministic calibration, not a lazy TODO) with a recorded acceptance range and escalation criterion. No other placeholders.

**Type consistency:** `_get_model(model_name, device="cpu")` and `_get_device() -> str` are used consistently across `_embed`/`_embed_async` and the tests. `DEFAULTS['topic_dedup_engine']` string value consistent between Task 2 and Task 4.
