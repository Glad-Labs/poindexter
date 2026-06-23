# Single-GPU Budget + Context Control — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the pipeline a deterministic VRAM budget, make context size DB-configurable per-phase on the path the writer actually uses, and clamp any context that would breach the desktop reserve — the budget + capability half of the [VRAM budget design](../specs/2026-06-22-single-gpu-vram-budget-stability-design.md). Builds on Plan 1 (stability core).

**Architecture:** A pure `vram_budget` calculator (no I/O) estimates a model's footprint from architecture read live off Ollama `/api/show`. The LLM dispatch path (`llm_text` → `dispatch_complete` → `litellm_provider`) gains an end-to-end `num_ctx` parameter — closing the seam gap where the writer silently ignores `ollama_num_ctx`. A per-phase resolver picks the context per pipeline phase, and a fit guard in `dispatch_complete` (where model + num_ctx + the GPU lock converge) clamps the context to the budget before the call fires. A Grafana panel surfaces dedicated-VRAM headroom.

**Tech Stack:** Python 3.13 / FastAPI worker, Ollama (host) `/api/show`, LiteLLM, asyncpg/`app_settings`, Prometheus + Grafana.

## Global Constraints

- New `app_settings` keys go in `src/cofounder_agent/services/settings_defaults.py` (`DEFAULTS` + `METADATA`), **never** a migration file.
- `app_settings.value` is `NOT NULL`; `''` is the unset sentinel. Read with `get(key, default) or default` so `''` falls back.
- Required settings fail loud, no silent defaults (`feedback_no_silent_defaults`). Budget read failures emit a `utils.findings.emit_finding` row and fall back, never crash the LLM path.
- Every change ships with contract tests + doc updates (`feedback_docs_and_tests_default`).
- All changes land via PR off a branch; never push `main`; linear history.
- Calculated-over-generated (`feedback_calculated_vs_generated`): the budget is deterministic arithmetic, not an LLM call.
- **Run tests from `src/cofounder_agent`** (not the worktree root — the root `pyproject.toml` `addopts` carries `--load-dotenv`, which errors there). Borrow a complete venv if poetry's is empty: `PYTHONPATH=<worktree>/src/cofounder_agent` + a `poindexter-backend-*-py3.13` venv that has `pytest` (see `reference_borrowed_venv_when_poetry_broken`).

## Interfaces at a glance (cross-task contract)

```python
# services/vram_budget.py
@dataclass(frozen=True)
class ModelArch:
    n_layers: int
    n_kv_heads: int
    head_dim: int
    weight_bytes: int

def estimate_kv_cache_gb(arch: ModelArch, num_ctx: int, kv_bytes_per_elem: float, batch: int = 1) -> float
def estimate_model_vram_gb(arch: ModelArch, kv_cache_gb: float, overhead_gb: float = 1.5) -> float
def fits(footprint_gb: float, total_gb: float, desktop_reserve_gb: float) -> tuple[bool, float]
def max_safe_num_ctx(arch: ModelArch, total_gb: float, desktop_reserve_gb: float, kv_bytes_per_elem: float) -> int
def kv_bytes_per_elem(kv_cache_type: str) -> float            # f16->2, q8_0->1, q4_0->0.5
async def read_model_arch(model: str, base_url: str, client) -> ModelArch | None   # /api/show, cached

# services/ollama_client.py (new helper next to _default_num_ctx)
def resolve_num_ctx(phase: str | None, *, site_config: SiteConfig | None) -> int   # <phase>_num_ctx -> ollama_num_ctx -> 8192
```

---

### Task 1: `vram_budget.py` pure calculator

**Files:**

- Create: `src/cofounder_agent/services/vram_budget.py`
- Test: `src/cofounder_agent/tests/unit/services/test_vram_budget.py`

**Interfaces:**

- Produces: `ModelArch`, `estimate_kv_cache_gb`, `estimate_model_vram_gb`, `fits`, `max_safe_num_ctx`, `kv_bytes_per_elem` (signatures above). Consumed by Tasks 2 and 5.

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/test_vram_budget.py`:

```python
from services.vram_budget import (
    ModelArch,
    estimate_kv_cache_gb,
    estimate_model_vram_gb,
    fits,
    kv_bytes_per_elem,
    max_safe_num_ctx,
)

# gemma-4-31B-class shape (illustrative): 48 layers, 8 KV heads, head_dim 128.
_ARCH = ModelArch(n_layers=48, n_kv_heads=8, head_dim=128, weight_bytes=18 * 1024**3)


def test_kv_bytes_per_elem_maps_dtype():
    assert kv_bytes_per_elem("f16") == 2.0
    assert kv_bytes_per_elem("q8_0") == 1.0
    assert kv_bytes_per_elem("q4_0") == 0.5
    assert kv_bytes_per_elem("") == 2.0  # unset sentinel -> safe f16


def test_kv_cache_grows_linearly_with_context():
    a = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=1.0)
    b = estimate_kv_cache_gb(_ARCH, num_ctx=16384, kv_bytes_per_elem=1.0)
    assert b == pytest.approx(2 * a, rel=1e-6)


def test_q8_halves_kv_vs_f16():
    f16 = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=2.0)
    q8 = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=1.0)
    assert q8 == pytest.approx(f16 / 2, rel=1e-6)


def test_fits_reports_headroom():
    ok, headroom = fits(footprint_gb=25.0, total_gb=32.0, desktop_reserve_gb=3.0)
    assert ok is True
    assert headroom == pytest.approx(4.0)
    bad, deficit = fits(footprint_gb=31.0, total_gb=32.0, desktop_reserve_gb=3.0)
    assert bad is False
    assert deficit == pytest.approx(-2.0)


def test_max_safe_num_ctx_fits_within_budget():
    n = max_safe_num_ctx(_ARCH, total_gb=32.0, desktop_reserve_gb=3.0, kv_bytes_per_elem=1.0)
    foot = estimate_model_vram_gb(_ARCH, estimate_kv_cache_gb(_ARCH, n, 1.0))
    ok, _ = fits(foot, 32.0, 3.0)
    assert ok is True
    assert n > 0
```

Add `import pytest` at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run (from `src/cofounder_agent`): `python -m pytest tests/unit/services/test_vram_budget.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.vram_budget'`.

- [ ] **Step 3: Implement the calculator**

Create `src/cofounder_agent/services/vram_budget.py`:

```python
"""Deterministic single-GPU VRAM footprint math (no I/O except read_model_arch).

Estimates a model's VRAM footprint = weights + KV cache + fixed overhead, and
answers "does it fit within (total - desktop_reserve)?" so the dispatch path can
clamp context before the NVIDIA driver would spill into system RAM (which freezes
the WDDM desktop). See docs/superpowers/specs/2026-06-22-single-gpu-vram-budget-stability-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.logger_config import get_logger

logger = get_logger(__name__)

# Per-element KV-cache byte cost by Ollama OLLAMA_KV_CACHE_TYPE.
_KV_BYTES = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5}
_DEFAULT_OVERHEAD_GB = 1.5  # CUDA context + activations, conservative.

# read_model_arch cache — arch is immutable per model tag.
_ARCH_CACHE: dict[str, "ModelArch"] = {}


@dataclass(frozen=True)
class ModelArch:
    n_layers: int
    n_kv_heads: int
    head_dim: int
    weight_bytes: int


def kv_bytes_per_elem(kv_cache_type: str) -> float:
    """Map an Ollama KV cache dtype to bytes/element; unset/'' -> safe f16."""
    return _KV_BYTES.get(kv_cache_type or "f16", 2.0)


def estimate_kv_cache_gb(
    arch: ModelArch, num_ctx: int, kv_bytes_per_elem: float, batch: int = 1,
) -> float:
    """KV cache = 2 (K+V) * layers * kv_heads * head_dim * ctx * batch * bytes."""
    elems = 2 * arch.n_layers * arch.n_kv_heads * arch.head_dim * num_ctx * batch
    return (elems * kv_bytes_per_elem) / (1024 ** 3)


def estimate_model_vram_gb(
    arch: ModelArch, kv_cache_gb: float, overhead_gb: float = _DEFAULT_OVERHEAD_GB,
) -> float:
    return arch.weight_bytes / (1024 ** 3) + kv_cache_gb + overhead_gb


def fits(
    footprint_gb: float, total_gb: float, desktop_reserve_gb: float,
) -> tuple[bool, float]:
    """Return (fits, headroom_gb). headroom is negative (the deficit) when over."""
    budget = total_gb - desktop_reserve_gb
    headroom = budget - footprint_gb
    return headroom >= 0, headroom


def max_safe_num_ctx(
    arch: ModelArch, total_gb: float, desktop_reserve_gb: float,
    kv_bytes_per_elem: float,
) -> int:
    """Largest num_ctx whose footprint still fits the budget (0 if weights alone
    already exceed it). Closed-form: solve fits() for num_ctx, floor to a
    256-token multiple."""
    budget = total_gb - desktop_reserve_gb
    non_kv = arch.weight_bytes / (1024 ** 3) + _DEFAULT_OVERHEAD_GB
    kv_budget_gb = budget - non_kv
    if kv_budget_gb <= 0:
        return 0
    per_ctx_gb = estimate_kv_cache_gb(arch, num_ctx=1, kv_bytes_per_elem=kv_bytes_per_elem)
    if per_ctx_gb <= 0:
        return 0
    raw = int(kv_budget_gb / per_ctx_gb)
    return max(0, (raw // 256) * 256)


async def read_model_arch(model: str, base_url: str, client) -> ModelArch | None:
    """Read n_layers/n_kv_heads/head_dim/weight_bytes from Ollama /api/show.

    Cached per model tag. Returns None (caller fails open with a finding) when
    /api/show is unreachable or the model_info keys are absent. ``client`` is a
    shared httpx.AsyncClient.
    """
    if model in _ARCH_CACHE:
        return _ARCH_CACHE[model]
    tag = model.split("/", 1)[-1]  # strip any "ollama/" prefix
    try:
        resp = await client.post(f"{base_url}/api/show", json={"model": tag}, timeout=10)
        resp.raise_for_status()
        info = resp.json().get("model_info", {}) or {}
    except Exception as exc:
        logger.warning("[vram_budget] /api/show failed for %s: %s", tag, exc)
        return None
    # Ollama model_info keys are architecture-prefixed, e.g. "gemma3.block_count".
    def _find(suffix: str) -> int | None:
        for k, v in info.items():
            if k.endswith(suffix) and isinstance(v, int):
                return v
        return None
    n_layers = _find(".block_count")
    n_kv_heads = _find(".attention.head_count_kv") or _find(".attention.head_count")
    emb = _find(".embedding_length")
    n_heads = _find(".attention.head_count")
    head_dim = (emb // n_heads) if (emb and n_heads) else None
    size_bytes = info.get("size") or 0
    if not (n_layers and n_kv_heads and head_dim):
        logger.warning("[vram_budget] incomplete model_info for %s: %s", tag, list(info)[:8])
        return None
    arch = ModelArch(n_layers, n_kv_heads, head_dim, int(size_bytes))
    _ARCH_CACHE[model] = arch
    return arch
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/services/test_vram_budget.py -q -p no:cacheprovider`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/vram_budget.py src/cofounder_agent/tests/unit/services/test_vram_budget.py
git commit -m "feat(vram): deterministic VRAM budget calculator"
```

---

### Task 2: Budget config keys

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (`DEFAULTS` + `METADATA`)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: `gpu_vram_total_gb` (`"32"`), `gpu_desktop_reserve_gb` (`"3"`), `ollama_kv_cache_type` (`"q8_0"`), `vram_budget_guard_enabled` (`"true"`). Consumed by Task 5.

- [ ] **Step 1: Write the failing test**

Add to `test_settings_defaults.py`:

```python
def test_vram_budget_defaults_present():
    from services.settings_defaults import DEFAULTS, METADATA
    assert DEFAULTS["gpu_vram_total_gb"] == "32"
    assert DEFAULTS["gpu_desktop_reserve_gb"] == "3"
    assert DEFAULTS["ollama_kv_cache_type"] == "q8_0"
    assert DEFAULTS["vram_budget_guard_enabled"] == "true"
    assert METADATA["gpu_vram_total_gb"]["value_type"] == "float"
    assert METADATA["gpu_desktop_reserve_gb"]["value_type"] == "float"
    assert METADATA["ollama_kv_cache_type"]["value_type"] == "string"
    assert METADATA["vram_budget_guard_enabled"]["value_type"] == "boolean"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/services/test_settings_defaults.py::test_vram_budget_defaults_present -q -p no:cacheprovider`
Expected: FAIL — `KeyError: 'gpu_vram_total_gb'`.

- [ ] **Step 3: Add the keys**

In `DEFAULTS` (near the other `gpu_*` rows):

```python
    'gpu_vram_total_gb': '32',
    'gpu_desktop_reserve_gb': '3',
    'ollama_kv_cache_type': 'q8_0',
    'vram_budget_guard_enabled': 'true',
```

In `METADATA`:

```python
    'gpu_vram_total_gb': {'owner': 'gpu_scheduler', 'value_type': 'float'},
    'gpu_desktop_reserve_gb': {'owner': 'gpu_scheduler', 'value_type': 'float'},
    'ollama_kv_cache_type': {'owner': 'vram_budget', 'value_type': 'string'},
    'vram_budget_guard_enabled': {'owner': 'vram_budget', 'value_type': 'boolean'},
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/services/test_settings_defaults.py -q -p no:cacheprovider`
Expected: PASS (the range + dynamic-count tests still hold; 4 keys added to both dicts).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(vram): seed VRAM budget config keys"
```

---

### Task 3: Per-phase `num_ctx` resolver

**Files:**

- Modify: `src/cofounder_agent/services/ollama_client.py` (add `resolve_num_ctx` next to `_default_num_ctx`, ~line 132)
- Test: `src/cofounder_agent/tests/unit/services/test_ollama_client.py`

**Interfaces:**

- Produces: `resolve_num_ctx(phase, *, site_config) -> int`. Precedence `<phase>_num_ctx` -> `ollama_num_ctx` -> 8192. Consumed by Task 4.

- [ ] **Step 1: Write the failing test**

Add to `test_ollama_client.py`:

```python
def test_resolve_num_ctx_precedence():
    from services.ollama_client import resolve_num_ctx
    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={
        "ollama_num_ctx": "8192",
        "content.generate_draft_num_ctx": "32768",
    })
    # phase with an override wins
    assert resolve_num_ctx("content.generate_draft", site_config=sc) == 32768
    # phase without an override falls back to the global
    assert resolve_num_ctx("content.generate_title", site_config=sc) == 8192
    # no phase -> global
    assert resolve_num_ctx(None, site_config=sc) == 8192
    # nothing configured -> hard default
    assert resolve_num_ctx("x", site_config=SiteConfig(initial_config={})) == 8192
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/services/test_ollama_client.py::test_resolve_num_ctx_precedence -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'resolve_num_ctx'`.

- [ ] **Step 3: Implement the resolver**

In `services/ollama_client.py`, just below `_default_num_ctx`:

```python
def resolve_num_ctx(
    phase: str | None, *, site_config: "SiteConfig | None" = None,
) -> int:
    """Per-phase context window: ``<phase>_num_ctx`` -> ``ollama_num_ctx`` -> 8192.

    Lets context-hungry phases (writer/RAG) run long while title/SEO stay small.
    The '' app_settings sentinel falls through each level via the int() guard.
    """
    if phase:
        raw = _sc_get_di(f"{phase}_num_ctx", "", site_config=site_config)
        if raw:
            try:
                return int(raw)
            except (ValueError, TypeError):
                pass
    return _default_num_ctx(site_config=site_config)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/services/test_ollama_client.py::test_resolve_num_ctx_precedence -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/ollama_client.py src/cofounder_agent/tests/unit/services/test_ollama_client.py
git commit -m "feat(llm): per-phase num_ctx resolver"
```

---

### Task 4: Thread `num_ctx` through the dispatch path (the seam-gap fix)

**Files:**

- Modify: `src/cofounder_agent/services/llm_providers/litellm_provider.py:548` (forward-keys tuple)
- Modify: `src/cofounder_agent/services/llm_text.py:249-257` (pass resolved `num_ctx` into `dispatch_complete`)
- Test: `src/cofounder_agent/tests/unit/services/llm_providers/test_litellm_provider.py` (or nearest existing litellm test module)

**Interfaces:**

- Consumes: `resolve_num_ctx` (Task 3). `dispatch_complete` already forwards `**kwargs` to `provider.complete`, so no change there for pass-through.
- Produces: `num_ctx` reaches `litellm.acompletion(num_ctx=...)` -> Ollama `options.num_ctx`. This is what makes the **writer** honor the context setting.

- [ ] **Step 1: Write the failing test**

Add a test that `litellm_provider.complete` forwards `num_ctx` into the acompletion kwargs (patch `litellm.acompletion`, assert it received `num_ctx`):

```python
@pytest.mark.asyncio
async def test_complete_forwards_num_ctx(monkeypatch):
    import services.llm_providers.litellm_provider as lp

    captured = {}

    async def _fake_acompletion(**kwargs):
        captured.update(kwargs)
        class _Choice:
            message = type("M", (), {"content": "ok"})()
            finish_reason = "stop"
        return type("R", (), {"choices": [_Choice()], "usage": None})()

    import litellm
    monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)

    provider = lp.LiteLLMProvider()
    await provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="ollama/gemma-4-31B-it-qat:latest",
        num_ctx=32768,
        _provider_config={},
    )
    assert captured.get("num_ctx") == 32768
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/services/llm_providers/test_litellm_provider.py::test_complete_forwards_num_ctx -q -p no:cacheprovider`
Expected: FAIL — `num_ctx` is dropped (`kwargs` not forwarded), `captured.get("num_ctx")` is None.

- [ ] **Step 3: Forward `num_ctx` in the provider, resolve it at the call site**

In `litellm_provider.py`, add `"num_ctx"` to the forwarded-keys tuple at line 548:

```python
        for key in ("temperature", "max_tokens", "top_p", "response_format", "num_ctx"):
            if key in kwargs:
                completion_kwargs[key] = kwargs[key]
```

(LiteLLM maps `num_ctx` to Ollama's `options.num_ctx`; with `drop_params=True` other providers ignore it.)

In `llm_text.py`, resolve per-phase context and pass it into `dispatch_complete` (the `pool is not None` branch, ~line 249):

```python
        from services.ollama_client import resolve_num_ctx

        completion = await dispatch_complete(
            pool=pool,
            messages=messages,
            model=resolved_model,
            tier=tier,
            timeout_s=int(timeout),
            task_id=task_id,
            phase=phase,
            num_ctx=resolve_num_ctx(phase, site_config=site_config),
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/services/llm_providers/test_litellm_provider.py::test_complete_forwards_num_ctx -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Runtime confirmation (manual, per `verify`)**

After deploy, with a content task running: `curl http://localhost:11434/api/ps` (or host `ollama ps`) shows the writer model's `context` at the resolved value (e.g. 32768), not Ollama's ~4096 default — closing the seam gap the spec flagged.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/llm_providers/litellm_provider.py src/cofounder_agent/services/llm_text.py src/cofounder_agent/tests/unit/services/llm_providers/test_litellm_provider.py
git commit -m "fix(llm): thread per-phase num_ctx through the dispatch path

The writer routed through LiteLLM never sent num_ctx, so it ran at
Ollama's Modelfile default and ignored ollama_num_ctx. Forward num_ctx
in the provider and resolve it per-phase at the call site."
```

---

### Task 5: Pre-load fit guard (clamp context to the budget)

**Files:**

- Modify: `src/cofounder_agent/services/llm_providers/dispatcher.py` (in `dispatch_complete`, before the `gpu.lock` acquire ~line 364)
- Test: `src/cofounder_agent/tests/unit/services/llm_providers/test_dispatcher.py`

**Note (deviation from spec wording):** the spec placed the guard "in `gpu_scheduler`", but `dispatch_complete` is where model + `num_ctx` + the local-GPU lock actually converge, and it already gates local calls (`_gpu_serialize_local_dispatch`). Putting the clamp here guards the LiteLLM writer path (the high-VRAM case). The 3 direct `ollama_client.py` callers keep their fixed `num_ctx` and are out of scope.

**Interfaces:**

- Consumes: `vram_budget` (Task 1), the budget config keys (Task 2). Reads model arch via `read_model_arch` using `app.state.http_client` / a shared client + `OLLAMA_BASE_URL`.

- [ ] **Step 1: Write the failing test**

Add `test_dispatcher.py` covering the clamp helper. Factor the clamp into a small async function `_clamp_num_ctx_to_budget(pool, model, num_ctx, provider_config) -> int` so it is unit-testable without a live Ollama:

```python
@pytest.mark.asyncio
async def test_clamp_num_ctx_reduces_when_over_budget(monkeypatch):
    import services.llm_providers.dispatcher as d
    from services.vram_budget import ModelArch

    # 18GB weights, tiny reserve headroom -> a 65k context must clamp down.
    monkeypatch.setattr(
        d, "_budget_inputs",
        lambda pc: (32.0, 3.0, 1.0),  # total, reserve, kv_bytes_per_elem
    )
    async def _arch(*a, **k):
        return ModelArch(n_layers=48, n_kv_heads=8, head_dim=128, weight_bytes=18 * 1024**3)
    monkeypatch.setattr(d, "_read_arch_for_budget", _arch)

    clamped = await d._clamp_num_ctx_to_budget(
        pool=None, model="ollama/gemma-4-31B-it-qat:latest",
        num_ctx=65536, provider_config={},
    )
    assert 0 < clamped < 65536


@pytest.mark.asyncio
async def test_clamp_noop_when_arch_unavailable(monkeypatch):
    import services.llm_providers.dispatcher as d
    async def _none(*a, **k):
        return None
    monkeypatch.setattr(d, "_read_arch_for_budget", _none)
    # fail-open: unknown arch -> return the requested num_ctx unchanged
    out = await d._clamp_num_ctx_to_budget(
        pool=None, model="m", num_ctx=8192, provider_config={},
    )
    assert out == 8192
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/services/llm_providers/test_dispatcher.py -q -p no:cacheprovider`
Expected: FAIL — `_clamp_num_ctx_to_budget` / helpers don't exist.

- [ ] **Step 3: Implement the clamp + wire it in**

In `dispatcher.py`, add the helpers and call the clamp in `dispatch_complete` only when `num_ctx` is present and the guard is enabled, before the `gpu.lock` block:

```python
async def _budget_inputs(provider_config: dict) -> tuple[float, float, float]:
    from services.vram_budget import kv_bytes_per_elem
    sc = _sc()  # container SiteConfig accessor used elsewhere in this module
    total = sc.get_float("gpu_vram_total_gb", 32.0)
    reserve = sc.get_float("gpu_desktop_reserve_gb", 3.0)
    kv = kv_bytes_per_elem(sc.get("ollama_kv_cache_type", "q8_0") or "q8_0")
    return total, reserve, kv


async def _read_arch_for_budget(model: str):
    from services.bootstrap_defaults import DEFAULT_OLLAMA_URL
    from services.vram_budget import read_model_arch
    base = _sc().get("ollama_base_url", DEFAULT_OLLAMA_URL) or DEFAULT_OLLAMA_URL
    async with httpx.AsyncClient() as client:
        return await read_model_arch(model, base, client)


async def _clamp_num_ctx_to_budget(
    pool, model: str, num_ctx: int, provider_config: dict,
) -> int:
    """Return num_ctx clamped to max_safe_num_ctx for the budget. Fails open
    (returns num_ctx unchanged) + emits a finding when arch is unavailable."""
    from services.vram_budget import (
        estimate_kv_cache_gb, estimate_model_vram_gb, fits, max_safe_num_ctx,
    )
    arch = await _read_arch_for_budget(model)
    if arch is None:
        return num_ctx  # fail open; the no-sysmem-fallback driver setting backstops
    total, reserve, kv = await _budget_inputs(provider_config)
    foot = estimate_model_vram_gb(arch, estimate_kv_cache_gb(arch, num_ctx, kv))
    ok, _ = fits(foot, total, reserve)
    if ok:
        return num_ctx
    safe = max_safe_num_ctx(arch, total, reserve, kv)
    from utils.findings import emit_finding
    emit_finding(
        source="vram_budget", kind="num_ctx_clamped", severity="warning",
        title=f"num_ctx clamped {num_ctx}->{safe} for {model}",
        body=(f"Requested context {num_ctx} would exceed the VRAM budget "
              f"(total {total}GB - reserve {reserve}GB). Clamped to {safe} to "
              f"avoid a sysmem spill / desktop freeze."),
        dedup_key=f"num_ctx_clamp_{model}",
    )
    return safe
```

Then in `dispatch_complete`, just before the `if _gpu_serialize_local_dispatch(...)` block:

```python
            num_ctx = kwargs.get("num_ctx")
            if num_ctx and _sc().get_bool("vram_budget_guard_enabled", True):
                kwargs["num_ctx"] = await _clamp_num_ctx_to_budget(
                    pool, model, int(num_ctx), provider_config or {},
                )
```

(If `dispatcher.py` lacks an `_sc()` SiteConfig accessor, add the same `container_registry.get_container().site_config` helper used in `gpu_scheduler._sc`.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/services/llm_providers/test_dispatcher.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/llm_providers/dispatcher.py src/cofounder_agent/tests/unit/services/llm_providers/test_dispatcher.py
git commit -m "feat(vram): clamp num_ctx to the VRAM budget before GPU acquire"
```

---

### Task 6: Grafana VRAM-headroom panel

**Files:**

- Modify: the Hardware & Power dashboard JSON under `infrastructure/grafana/provisioning/dashboards/` (the board with `uid` `hardware-power`)
- Doc: append a panel note to `docs/operations/single-gpu-vram-tuning.md`

**Note:** the Python budget (projected footprint) is a pre-flight decision value, not a continuous metric. The live panel uses the existing Prometheus `nvidia_gpu_*` series so it needs no new exporter: **dedicated headroom = (`gpu_vram_total_gb` - `gpu_desktop_reserve_gb`) - used_GB**. The Windows "Shared Usage" spill counter is not in Prometheus today (windows_exporter has no GPU collector); surfacing it is a future exporter task, noted but out of scope.

- [ ] **Step 1: Add the panel**

Open the `hardware-power` dashboard JSON, add a `timeseries` (or `gauge`) panel "VRAM headroom (GB)" with a Prometheus target:

```
( 32 - 3 ) - (nvidia_gpu_memory_used_bytes / 1024 / 1024 / 1024)
```

Use the literals matching the seeded defaults (32 / 3); add a note in the panel description that these mirror `gpu_vram_total_gb` / `gpu_desktop_reserve_gb`. Threshold: red when the expression drops below 0 (use a blue/amber split, not red/green — operator is red-green colorblind, `feedback` user profile).

- [ ] **Step 2: Validate the dashboard JSON**

Run: `python -c "import json,sys; json.load(open(sys.argv[1]))" <path-to-dashboard.json>`
Expected: no error (valid JSON). Reload Grafana provisioning (or restart the grafana container) and confirm the panel renders.

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/provisioning/dashboards/ docs/operations/single-gpu-vram-tuning.md
git commit -m "feat(grafana): VRAM headroom panel on Hardware & Power"
```

---

## Self-Review

**Spec coverage (Plan 2 = spec components 2, 3, 6, 7):**

- Component 2 (`vram_budget.py` calculator + `read_model_arch`) -> Task 1. ✓
- Component 3 (pre-load fit guard) -> Task 5 (placed in `dispatch_complete`; deviation from spec wording noted + justified). ✓
- Component 6 (DB-configurable context: dispatch threading + per-phase + interlock) -> Tasks 3 (resolver) + 4 (threading/seam-gap) + 5 (interlock). ✓
- Component 7 (config keys + Grafana) -> Task 2 (keys) + Task 6 (panel). ✓
- Components 1, 4, 5 are Plan 1 (shipped). ✓

**Placeholder scan:** Task 1's `ModelArch` numbers (48/8/128) are labelled illustrative — the real values come from `read_model_arch` at runtime; the tests only assert relationships (linearity, halving, fit), not absolute GB, so they don't depend on the illustrative shape. Task 6 leaves the exact dashboard filename to the executor (the `hardware-power` uid is the locator) because panel JSON is large and the board file is found by uid — not a code placeholder.

**Type consistency:** `ModelArch` fields (`n_layers`, `n_kv_heads`, `head_dim`, `weight_bytes`) used identically across `estimate_*`, `max_safe_num_ctx`, and `read_model_arch`. `resolve_num_ctx(phase, *, site_config)` signature matches its call in Task 4. `num_ctx` flows kwargs -> `dispatch_complete` -> `provider.complete` -> `completion_kwargs` consistently.

**Open items for execution:**

- Confirm `dispatcher.py` has (or add) a `_sc()` container SiteConfig accessor (Task 5).
- Pick the nearest existing litellm/dispatcher test module names (Task 4/5 assume `tests/unit/services/llm_providers/test_litellm_provider.py` + `test_dispatcher.py`; create if absent).
- Verify the Ollama `/api/show` `model_info` key suffixes for gemma-4 (`.block_count`, `.attention.head_count_kv`, `.embedding_length`) on the live box; `read_model_arch` already fails open + logs the available keys if a suffix differs.

## Execution Handoff

Plan 2 saved. It depends on Plan 1's `ollama_kv_cache_type=q8_0` for its footprint math. Two execution options: **(1) Subagent-Driven** (fresh subagent per task, review between) or **(2) Inline** (executing-plans, batch with checkpoints). Task 4 (the writer `num_ctx` seam-gap fix) is the highest-value standalone slice and can ship first.
