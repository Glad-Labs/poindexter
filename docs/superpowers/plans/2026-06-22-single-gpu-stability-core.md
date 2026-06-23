# Single-GPU Stability Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline incapable of freezing the Windows desktop via VRAM spill, and stop the in-process reranker from stacking on the resident writer — the felt-stability half of the [VRAM budget design](../specs/2026-06-22-single-gpu-vram-budget-stability-design.md).

**Architecture:** Three independent changes plus one diagnostic. (1) Confirm the spill mechanism with a Windows GPU perf-counter. (2) Move the sentence-transformers cross-encoder reranker off CUDA onto CPU via a new DB setting. (3) Harden the host: disable NVIDIA's sysmem fallback so over-allocation fails clean, and turn on Ollama flash-attention + q8_0 KV cache. The deterministic VRAM budget calculator, the pre-load fit guard, and the end-to-end per-phase `num_ctx` plumbing are a **separate plan** (Plan 2 — Budget + Context Control) that builds on this one.

**Tech Stack:** Python 3.13 / FastAPI worker, sentence-transformers (optional dep), Ollama (host service), PowerShell host scripts, Postgres `app_settings`.

## Global Constraints

- New `app_settings` keys go in `src/cofounder_agent/services/settings_defaults.py` (`DEFAULTS` + `METADATA`), **never** in a migration file (migrations run once and drift; the seeder runs every boot).
- `app_settings.value` is `NOT NULL`; `''` is the unset sentinel — never store `NULL`. Read with the `get(key, default) or default` idiom so `''` falls back.
- Required settings fail loud, no silent defaults (`feedback_no_silent_defaults`).
- Host PowerShell scripts must stay **pure-ASCII** — CI `check-powershell-encoding` rejects non-ASCII bytes (use `-`, never `—`).
- Every change ships with contract tests + doc updates (`feedback_docs_and_tests_default`).
- All changes land via PR off this branch; never push `main`; linear history (squash/rebase).
- **Ollama runs on the host** (`OLLAMA_BASE_URL=http://host.docker.internal:11434`). `OLLAMA_*` env vars and the NVIDIA driver setting are **host-side** actions Matt performs once — they are not docker-compose changes and cannot be unit-tested in CI.
- Run unit tests with: `cd src/cofounder_agent && poetry run pytest tests/unit/ -q`. If poetry's env is flaky in this worktree, borrow a complete `poindexter-backend-*` venv and set `PYTHONPATH` to the worktree `src/cofounder_agent` (see `reference_borrowed_venv_when_poetry_broken`).

---

### Task 1: Confirm the spill mechanism (host diagnostic, no code)

**Files:** none (produces a confirmation note appended to the spec's Open Questions).

**Why:** The operator's incident history burned a day chasing the wrong memory layer. Confirm it is VRAM→sysmem spill before hardening against it. This task is a runbook, not red-green — there is nothing to unit-test.

- [ ] **Step 1: Start sampling GPU shared (sysmem-fallback) memory**

In a host PowerShell window (not the container), run:

```powershell
Get-Counter '\GPU Adapter Memory(*)\Shared Usage' -Continuous -SampleInterval 2
```

`Shared Usage` is the WDDM "Shared GPU Memory" pool — i.e. VRAM that has spilled into system RAM over PCIe. At idle it should be near 0. (`nvidia-smi` does **not** expose this number; it is a Windows perf counter.)

- [ ] **Step 2: Drive the GPU toward its ceiling**

While the counter streams, trigger real pressure — run a content task end-to-end (writer + vision QA + image), or deliberately load two large models back-to-back via Ollama:

```powershell
ollama run gemma-4-31B-it-qat:latest "warm up" ; ollama run qwen3-vl:30b "warm up"
```

- [ ] **Step 3: Record the signal**

Confirm whether `Shared Usage` climbs above ~0 (hundreds of MB to GB) at the moment the desktop stutters. Climbing shared usage = confirmed sysmem spill → Task 3's no-fallback setting is the correct fix. If shared usage stays flat while the desktop still freezes, **stop and re-open the diagnosis** (the premise is wrong). Paste the observed numbers into the spec's "Open questions" section and commit that one-line doc update.

---

### Task 2: Seed the `rag_rerank_device` setting

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (the `DEFAULTS` dict near the other `rag_*`/model rows, and the `METADATA` dict near line 1258 where `rag_rerank_model` is declared)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: `app_settings` key `rag_rerank_device` (string, default `"cpu"`), consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Add to `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`:

```python
def test_rag_rerank_device_default_is_cpu():
    from services.settings_defaults import DEFAULTS, METADATA
    assert DEFAULTS["rag_rerank_device"] == "cpu"
    assert METADATA["rag_rerank_device"]["value_type"] == "string"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_rag_rerank_device_default_is_cpu -v`
Expected: FAIL with `KeyError: 'rag_rerank_device'`.

- [ ] **Step 3: Add the default + metadata**

In `DEFAULTS`, next to the existing RAG/model rows, add:

```python
    'rag_rerank_device': 'cpu',
```

In `METADATA`, next to `'rag_rerank_model': {...}` (line ~1258), add:

```python
    'rag_rerank_device': {'owner': 'rag_engine', 'value_type': 'string'},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_rag_rerank_device_default_is_cpu -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(rag): add rag_rerank_device setting (default cpu)"
```

---

### Task 3: Run the cross-encoder reranker on CPU

**Files:**

- Modify: `src/cofounder_agent/services/rag_engine.py:614-631` (the `_get_model` method inside `_build_rerank_retriever_class`)
- Test: `src/cofounder_agent/tests/unit/services/test_rag_engine.py`

**Interfaces:**

- Consumes: `rag_rerank_device` from Task 2.
- Produces: reranker `CrossEncoder` constructed with `device=<setting>`; `_RERANKER_CACHE` keyed on `f"{name}@{device}"`.

- [ ] **Step 1: Write the failing test**

Add to `src/cofounder_agent/tests/unit/services/test_rag_engine.py`. This injects a fake `sentence_transformers` module so the test passes whether or not the optional dep is installed:

```python
def test_reranker_constructs_on_configured_device(monkeypatch):
    import sys
    import types
    import services.rag_engine as rag
    from services.site_config import SiteConfig

    captured = {}

    class _FakeCrossEncoder:
        def __init__(self, name, device=None):
            captured["name"] = name
            captured["device"] = device

        def predict(self, pairs):
            return [0.0 for _ in pairs]

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.CrossEncoder = _FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    rag._RERANKER_CACHE.clear()
    cls = rag._build_rerank_retriever_class()
    sc = SiteConfig(initial_config={"rag_rerank_device": "cpu"})
    r = cls(inner=object(), top_k=5, site_config=sc)

    r._get_model()

    assert captured["device"] == "cpu"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_rag_engine.py::test_reranker_constructs_on_configured_device -v`
Expected: FAIL — `_FakeCrossEncoder` is called with no `device` kwarg, so `captured["device"]` is `None`, not `"cpu"`.

- [ ] **Step 3: Add `_device()` and pass it to CrossEncoder**

In `src/cofounder_agent/services/rag_engine.py`, add a `_device` method right after `_model_name` (mirror its `or` fallback so the `''` sentinel falls back):

```python
        def _device(self) -> str:
            if self._site_config is None:
                return "cpu"
            return (
                self._site_config.get("rag_rerank_device", "cpu") or "cpu"
            )
```

Then replace the body of `_get_model` (lines 614-631) with:

```python
        def _get_model(self) -> Any:
            name = self._model_name()
            device = self._device()
            cache_key = f"{name}@{device}"
            if cache_key in _RERANKER_CACHE:
                return _RERANKER_CACHE[cache_key]
            # ImportError is intentionally left to bubble — see _aretrieve's
            # handler. An enabled rail must fail loud, not degrade quietly.
            from sentence_transformers import CrossEncoder
            logger.info(
                "[rag/rerank] Loading cross-encoder %s on %s (first call)",
                name, device,
            )
            _RERANKER_CACHE[cache_key] = CrossEncoder(name, device=device)
            return _RERANKER_CACHE[cache_key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_rag_engine.py::test_reranker_constructs_on_configured_device -v`
Expected: PASS.

- [ ] **Step 5: Run the surrounding suite for regressions**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_rag_engine.py -q`
Expected: all pass (the cache-key change must not break existing rerank tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/rag_engine.py src/cofounder_agent/tests/unit/services/test_rag_engine.py
git commit -m "feat(rag): run cross-encoder reranker on CPU by default

Stops the in-process reranker stacking on the resident 18GB writer.
Device is DB-tunable via rag_rerank_device (default cpu); cache key
now includes device so a runtime change loads a fresh model."
```

---

### Task 4: Host-hardening runbook — no-sysmem-fallback + Ollama KV tuning

**Files:**

- Create: `docs/operations/single-gpu-vram-tuning.md`

**Why:** These are the highest-impact stability levers, but they are **host** settings (NVIDIA driver + host Ollama env). The deliverable that ships in the PR is the runbook; Matt applies the settings once on the box. No pytest — verification is manual commands the doc itself specifies.

- [ ] **Step 1: Write the runbook**

Create `docs/operations/single-gpu-vram-tuning.md` with these sections (fill verified specifics at apply-time):

1. **Disable NVIDIA System Memory Fallback** — NVIDIA Control Panel → _Manage 3D Settings_ → _CUDA - Sysmem Fallback Policy_ → **Prefer No Sysmem Fallback** (set globally, or per-program for the Ollama / `python.exe` that serves the worker). This makes an over-allocation return a clean CUDA OOM instead of paging VRAM into system RAM and freezing the WDDM compositor. _Verify the exact control label on the installed driver (added ~R535); if absent, use the documented registry key._ Cross-reference: this is the backstop the Plan 2 pre-load guard cooperates with.
2. **Enable Ollama flash-attention + q8_0 KV cache (host env)** — set as Windows user/system environment variables, then restart the Ollama server:

```powershell
setx OLLAMA_FLASH_ATTENTION 1
setx OLLAMA_KV_CACHE_TYPE q8_0
# restart the Ollama server so it re-reads the environment
```

Note `OLLAMA_KV_CACHE_TYPE` only takes effect when `OLLAMA_FLASH_ATTENTION=1`. Both are **global** Ollama settings (no per-model override); if the vision model regresses under q8_0 KV, revert globally. q8_0 is near-lossless and roughly halves KV-cache VRAM, which is what lets context grow safely (Plan 2).

3. **Verification** — after restart, confirm the server picked them up:

```powershell
# env is visible to the running server process:
Get-Process ollama | Select-Object -First 1
# load a model and confirm it runs; KV-quant shows as reduced VRAM for the same context:
ollama run gemma-4-31B-it-qat:latest "ok" ; ollama ps
```

Re-run the Task 1 counter under load and confirm `Shared Usage` now stays flat (no spill) instead of climbing.

- [ ] **Step 2: Apply the host settings (Matt, once)**

Perform the NVIDIA-control-panel change and the two `setx` commands above, then restart Ollama. This is one of the few "requires hands" steps.

- [ ] **Step 3: Commit the runbook**

```bash
git add docs/operations/single-gpu-vram-tuning.md
git commit -m "docs(ops): single-GPU VRAM tuning runbook (no-sysmem-fallback + Ollama KV)"
```

---

## Self-Review

**Spec coverage (Plan 1 scope = spec components 1, 4, 5):**

- Component 1 (freeze capture) → Task 1. ✓
- Component 4 (host hardening: no-sysmem-fallback + flash-attn + q8_0 KV) → Task 4. ✓
- Component 5 (CPU offload) → Tasks 2-3, **scoped to the reranker only** — the embedder is Ollama-served (`_get_embed_model` reads `OLLAMA_BASE_URL`), so it has no in-process device flag and is correctly deferred to the budget/swap story in Plan 2. ✓
- Components 2, 3, 6, 7 (calculator, fit guard, context plumbing, Grafana) → **out of scope, Plan 2.** ✓

**Placeholder scan:** Task 4 leaves the exact NVIDIA control label "to verify at apply-time" — this is a genuine host-driver unknown flagged in the spec's open questions, not a code placeholder; the runbook still gives the menu path + registry fallback. No code-step placeholders.

**Type consistency:** `_device()` returns `str`; `_get_model` cache key `f"{name}@{device}"` used consistently in both the read and write. `rag_rerank_device` spelled identically in DEFAULTS, METADATA, the test, and `_device()`.

## Execution Handoff

Plan 1 saved. **Plan 2 (Budget + Context Control)** — the `vram_budget.py` calculator, the `gpu_scheduler` pre-load fit guard, the LiteLLM `num_ctx` threading + per-phase overrides + interlock, and the Grafana headroom panel — will be written as its own plan once this lands (it depends on the q8_0 KV setting from Task 4 for its footprint math).
