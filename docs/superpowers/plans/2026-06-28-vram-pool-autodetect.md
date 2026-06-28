# Auto-detected VRAM Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-set `gpu_vram_total_gb` (default `32`) with an auto-detected pool summed across all GPUs, so a multi-GPU box is usable for image/video gen + a ~70B writer without hand-tuning.

**Architecture:** A new `GPURegistry` service detects total VRAM by summing the nvidia-smi exporter's per-GPU `memory.total` via Prometheus, memoizing the first success. `gpu_vram_total_gb` gains an `"auto"` sentinel (the new default) that the dispatcher's budget guard resolves through the registry; any explicit number still overrides. Detection failure falls back to a configurable floor and emits a fail-loud finding. Serialized one-model-at-a-time execution is unchanged.

**Tech Stack:** Python 3.13, asyncio, httpx, pytest, Prometheus (`local-prometheus`), Grafana, app_settings (`settings_defaults.py`).

## Global Constraints

- New `app_settings` defaults go in `services/settings_defaults.py` `DEFAULTS` + `METADATA`, NEVER in migration files (seeder applies them every boot via `ON CONFLICT DO NOTHING`).
- `app_settings` values are strings; `''` is the unset sentinel, NULL is forbidden.
- Settings are read through `SiteConfig` (DI): `sc.get(key, default)`, `sc.get_float(key, default)`. No new module-level singletons.
- The VRAM clamp path must remain **fail-open**: any error returns `num_ctx` unchanged (never break dispatch).
- TDD: failing test first, minimal impl, frequent commits. Run tests with the borrowed venv:
  `PYTHONPATH="$(pwd)" "/c/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-0I5ETI_x-py3.13/Scripts/python.exe" -m pytest <path> -q` (run from `src/cofounder_agent`).
- Conventional-commit messages; end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `GPURegistry` detection service

**Files:**
- Create: `src/cofounder_agent/services/gpu_registry.py`
- Test: `src/cofounder_agent/tests/unit/services/test_gpu_registry.py`

**Interfaces:**
- Consumes: `SiteConfig` (ctor kwarg), Prometheus HTTP `/api/v1/query`.
- Produces: `class GPURegistry` with `async def total_vram_gb(self) -> float | None` and ctor `GPURegistry(*, site_config: SiteConfig)`.

- [ ] **Step 1: Write the failing tests**

```python
# src/cofounder_agent/tests/unit/services/test_gpu_registry.py
"""Unit tests for services/gpu_registry.py — VRAM pool auto-detection."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gpu_registry import GPURegistry
from services.site_config import SiteConfig


def _sc() -> SiteConfig:
    return SiteConfig(initial_config={"gpu_metrics_prometheus_url": "http://prometheus:9090"})


def _mock_client(*, value: str | None = None, status: int = 200, raise_exc: Exception | None = None):
    """Fake httpx.AsyncClient whose .get returns a Prometheus instant-vector."""
    resp = MagicMock()
    resp.status_code = status
    if value is None:
        resp.json = MagicMock(return_value={"data": {"result": []}})
    else:
        resp.json = MagicMock(return_value={"data": {"result": [{"value": [1782600000.0, value]}]}})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=raise_exc) if raise_exc else AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_sums_and_converts_mib_to_gb():
    # 32607 + 24576 MiB summed by Prometheus = 57183 MiB -> /1024 = 55.84 GB
    client = _mock_client(value="57183")
    with patch("httpx.AsyncClient", return_value=client):
        total = await GPURegistry(site_config=_sc()).total_vram_gb()
    assert total == pytest.approx(57183 / 1024.0, abs=0.01)


@pytest.mark.asyncio
async def test_memoizes_first_success_no_requery():
    client = _mock_client(value="57183")
    reg = GPURegistry(site_config=_sc())
    with patch("httpx.AsyncClient", return_value=client):
        first = await reg.total_vram_gb()
        second = await reg.total_vram_gb()
    assert first == second
    assert client.get.await_count == 1  # cached; second call did not re-query


@pytest.mark.asyncio
async def test_empty_result_returns_none():
    client = _mock_client(value=None)
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_http_error_returns_none():
    client = _mock_client(value="57183", status=503)
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_exception_returns_none():
    client = _mock_client(raise_exc=RuntimeError("boom"))
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_retries_after_failure_then_caches():
    reg = GPURegistry(site_config=_sc())
    fail = _mock_client(value=None)
    with patch("httpx.AsyncClient", return_value=fail):
        assert await reg.total_vram_gb() is None  # not cached
    ok = _mock_client(value="57183")
    with patch("httpx.AsyncClient", return_value=ok):
        assert await reg.total_vram_gb() == pytest.approx(57183 / 1024.0, abs=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_gpu_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.gpu_registry'`.

- [ ] **Step 3: Write the implementation**

```python
# src/cofounder_agent/services/gpu_registry.py
"""Auto-detect total GPU VRAM across the box.

Sums the nvidia-smi exporter's per-GPU ``nvidia_gpu_memory_total_mib`` via
Prometheus (the dispatcher runs in a GPU-less container and can't call
nvidia-smi directly — it reads totals through the same telemetry path the GPU
scheduler uses for util/power). Total VRAM is a static hardware constant for a
process's lifetime, so the first successful detection is memoized permanently;
while detection has not yet succeeded the cache stays empty and each call
retries, so a startup Prometheus blip self-heals on a later call.
"""
from __future__ import annotations

import logging

import httpx

from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

_MIB_PER_GB = 1024.0
_DEFAULT_PROM_URL = "http://prometheus:9090"
_PROM_TIMEOUT_SEC = 5.0
_VRAM_TOTAL_QUERY = "sum(nvidia_gpu_memory_total_mib)"


class GPURegistry:
    """Detects + memoizes the total VRAM pool (GB) across all GPUs."""

    def __init__(self, *, site_config: SiteConfig) -> None:
        self._site_config = site_config
        self._cached_total_gb: float | None = None

    async def total_vram_gb(self) -> float | None:
        """Total VRAM across all GPUs in GB, or None if not yet detectable.

        Cached permanently after the first success; retries while still None.
        """
        if self._cached_total_gb is not None:
            return self._cached_total_gb
        detected = await self._detect()
        if detected is not None:
            self._cached_total_gb = detected
        return detected

    def _prometheus_url(self) -> str:
        return self._site_config.get("gpu_metrics_prometheus_url", "") or _DEFAULT_PROM_URL

    async def _detect(self) -> float | None:
        url = f"{self._prometheus_url()}/api/v1/query"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url, params={"query": _VRAM_TOTAL_QUERY}, timeout=_PROM_TIMEOUT_SEC
                )
            if resp.status_code != 200:
                logger.warning(
                    "[gpu_registry] Prometheus HTTP %s reading total VRAM", resp.status_code
                )
                return None
            result = (resp.json().get("data") or {}).get("result") or []
            if not result:
                logger.debug("[gpu_registry] no nvidia_gpu_memory_total_mib series yet")
                return None
            total_mib = float(result[0]["value"][1])
            if total_mib <= 0:
                return None
            return total_mib / _MIB_PER_GB
        except Exception as exc:  # detection is best-effort; caller falls back
            logger.warning(
                "[gpu_registry] VRAM detect failed: %s: %s", type(exc).__name__, exc
            )
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_gpu_registry.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/gpu_registry.py src/cofounder_agent/tests/unit/services/test_gpu_registry.py
git commit -m "feat(gpu): add GPURegistry VRAM pool auto-detection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Expose `gpu_registry` on `AppContainer`

**Files:**
- Modify: `src/cofounder_agent/services/container.py` (append a `cached_property`)
- Test: `src/cofounder_agent/tests/unit/services/test_container.py` (add one test; create file only if absent — otherwise append)

**Interfaces:**
- Consumes: `GPURegistry` from Task 1; `AppContainer.site_config`.
- Produces: `AppContainer.gpu_registry -> GPURegistry`.

- [ ] **Step 1: Write the failing test**

Append to `src/cofounder_agent/tests/unit/services/test_container.py` (create with this content if the file does not exist):

```python
def test_container_exposes_gpu_registry():
    from services.container import AppContainer
    from services.gpu_registry import GPURegistry
    from services.site_config import SiteConfig

    c = AppContainer(site_config=SiteConfig(initial_config={}))
    assert isinstance(c.gpu_registry, GPURegistry)
    assert c.gpu_registry is c.gpu_registry  # cached
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_container.py::test_container_exposes_gpu_registry -q`
Expected: FAIL — `AttributeError: 'AppContainer' object has no attribute 'gpu_registry'`.

- [ ] **Step 3: Add the cached_property**

In `src/cofounder_agent/services/container.py`, append after the last existing `cached_property` in the SiteConfig-DI block (search for the final `@cached_property` returning a service, add below it):

```python
    @cached_property
    def gpu_registry(self) -> "GPURegistry":
        """Total-VRAM pool auto-detector (2026-06-28).

        Sums the nvidia-smi exporter's per-GPU memory.total via Prometheus and
        memoizes it. Consumed by the dispatcher VRAM budget guard to resolve
        ``gpu_vram_total_gb="auto"``. Inline import avoids an import cycle
        (gpu_registry imports SiteConfig).
        """
        from services.gpu_registry import GPURegistry

        return GPURegistry(site_config=self.site_config)
```

Add the type-only import near the other `if TYPE_CHECKING:` service imports at the top of the file (search for `if TYPE_CHECKING:`; if a block exists, add `from services.gpu_registry import GPURegistry`; if service types are imported unconditionally there, follow that style instead):

```python
    from services.gpu_registry import GPURegistry
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_container.py::test_container_exposes_gpu_registry -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/container.py src/cofounder_agent/tests/unit/services/test_container.py
git commit -m "feat(gpu): expose gpu_registry on AppContainer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Settings — `auto` default + configurable fallback

**Files:**
- Modify: `src/cofounder_agent/services/settings_defaults.py` (DEFAULTS + METADATA)
- Modify: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py:49-62`

**Interfaces:**
- Produces: `DEFAULTS["gpu_vram_total_gb"] == "auto"`, `DEFAULTS["gpu_vram_autodetect_fallback_gb"] == "32"`; `METADATA["gpu_vram_total_gb"]["value_type"] == "string"`, `METADATA["gpu_vram_autodetect_fallback_gb"]["value_type"] == "float"`.

- [ ] **Step 1: Update the failing test first**

In `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`, edit `test_vram_budget_defaults_present` (around line 49):

```python
def test_vram_budget_defaults_present():
    # gpu_vram_total_gb defaults to "auto" — detected from the GPU pool, not
    # hand-set (2026-06-28). Any explicit number still overrides.
    assert DEFAULTS["gpu_vram_total_gb"] == "auto"
    assert DEFAULTS["gpu_desktop_reserve_gb"] == "3"
    # Fallback used only when auto-detection has never succeeded; tunable.
    assert DEFAULTS["gpu_vram_autodetect_fallback_gb"] == "32"
    assert METADATA["gpu_vram_total_gb"]["value_type"] == "string"
    assert METADATA["gpu_desktop_reserve_gb"]["value_type"] == "float"
    assert METADATA["gpu_vram_autodetect_fallback_gb"]["value_type"] == "float"
    assert METADATA["ollama_kv_cache_type"]["value_type"] == "string"
    assert METADATA["vram_budget_guard_enabled"]["value_type"] == "boolean"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_settings_defaults.py::test_vram_budget_defaults_present -q`
Expected: FAIL — `assert '32' == 'auto'`.

- [ ] **Step 3: Update DEFAULTS + METADATA**

In `settings_defaults.py`, change the `gpu_vram_total_gb` default and add the fallback (the `pipeline_gpu_index` comment block from PR #1956 sits just above these lines):

```python
    'gpu_vram_total_gb': 'auto',
    'gpu_desktop_reserve_gb': '3',
    # Conservative VRAM budget (GB) used ONLY when gpu_vram_total_gb="auto" but
    # detection has never succeeded (Prometheus unreachable). Tunable so an
    # operator on a smaller card can lower the floor instead of over-promising.
    'gpu_vram_autodetect_fallback_gb': '32',
```

And in the METADATA block (near the other gpu_* entries):

```python
    'gpu_vram_total_gb': {'owner': 'gpu_scheduler', 'value_type': 'string'},
    'gpu_desktop_reserve_gb': {'owner': 'gpu_scheduler', 'value_type': 'float'},
    'gpu_vram_autodetect_fallback_gb': {'owner': 'gpu_scheduler', 'value_type': 'float'},
```

- [ ] **Step 4: Run the full settings-defaults file (catches registry-size range)**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_settings_defaults.py -q`
Expected: PASS. If `test_registry_size_in_expected_range` fails because the count grew by one, widen the upper bound in that test by 1 (one-line change) and note it in the commit.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(gpu): gpu_vram_total_gb defaults to auto + configurable fallback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Resolve `"auto"` in the dispatcher budget guard

**Files:**
- Modify: `src/cofounder_agent/services/llm_providers/dispatcher.py:177-193` (make `_budget_inputs` async + add `_resolve_auto_total`) and `:232` (await the caller)
- Test: `src/cofounder_agent/tests/unit/services/test_dispatcher_vram_budget.py` (create; if a dispatcher-budget test file already exists, append the three tests there)

**Interfaces:**
- Consumes: `AppContainer.gpu_registry` (Task 2), `gpu_vram_total_gb` / `gpu_vram_autodetect_fallback_gb` (Task 3), `utils.findings.emit_finding`.
- Produces: `async def _budget_inputs(provider_config) -> tuple[float, float, float]`.

- [ ] **Step 1: Write the failing tests**

```python
# src/cofounder_agent/tests/unit/services/test_dispatcher_vram_budget.py
"""_budget_inputs resolves the gpu_vram_total_gb 'auto' sentinel via GPURegistry."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers import dispatcher
from services.site_config import SiteConfig


def _container(settings: dict, *, detected: float | None):
    c = MagicMock()
    c.site_config = SiteConfig(initial_config=settings)
    c.gpu_registry = MagicMock()
    c.gpu_registry.total_vram_gb = AsyncMock(return_value=detected)
    return c


@pytest.mark.asyncio
async def test_auto_uses_detected_pool():
    container = _container({"gpu_vram_total_gb": "auto"}, detected=55.8)
    with patch("services.container_registry.get_container", return_value=container):
        total, reserve, _kv = await dispatcher._budget_inputs({})
    assert total == pytest.approx(55.8)
    assert reserve == 3.0


@pytest.mark.asyncio
async def test_explicit_number_overrides_and_skips_detection():
    container = _container({"gpu_vram_total_gb": "48"}, detected=55.8)
    with patch("services.container_registry.get_container", return_value=container):
        total, _reserve, _kv = await dispatcher._budget_inputs({})
    assert total == 48.0
    container.gpu_registry.total_vram_gb.assert_not_awaited()


@pytest.mark.asyncio
async def test_detection_fail_uses_fallback_and_emits_finding():
    container = _container(
        {"gpu_vram_total_gb": "auto", "gpu_vram_autodetect_fallback_gb": "24"},
        detected=None,
    )
    with patch("services.container_registry.get_container", return_value=container), \
         patch("utils.findings.emit_finding") as mock_emit:
        total, _reserve, _kv = await dispatcher._budget_inputs({})
    assert total == 24.0
    mock_emit.assert_called_once()
    assert mock_emit.call_args.kwargs["kind"] == "vram_autodetect_failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_dispatcher_vram_budget.py -q`
Expected: FAIL — `_budget_inputs` is sync (awaiting it raises `TypeError: object tuple can't be used in 'await' expression`).

- [ ] **Step 3: Make `_budget_inputs` async + add the resolver**

Replace `_budget_inputs` in `dispatcher.py` (lines 177-193) with:

```python
async def _budget_inputs(provider_config: dict[str, Any]) -> tuple[float, float, float]:
    """``(total_gb, desktop_reserve_gb, kv_bytes_per_elem)`` from app_settings.

    ``gpu_vram_total_gb`` defaults to ``"auto"`` — the total VRAM pool detected
    across all GPUs via :class:`GPURegistry`. An explicit number overrides.
    Falls back to the seeded defaults when no container is bootstrapped
    (CLI, tests).
    """
    from services.container_registry import get_container
    from services.vram_budget import kv_bytes_per_elem

    container = get_container()
    if container is None:
        return 32.0, 3.0, kv_bytes_per_elem("q8_0")
    sc = container.site_config
    raw = (sc.get("gpu_vram_total_gb", "auto") or "auto").strip().lower()
    if raw in ("", "auto"):
        total = await _resolve_auto_total(container)
    else:
        try:
            total = float(raw)
        except ValueError:
            total = await _resolve_auto_total(container)
    reserve = sc.get_float("gpu_desktop_reserve_gb", 3.0)
    kv = kv_bytes_per_elem(sc.get("ollama_kv_cache_type", "q8_0") or "q8_0")
    return total, reserve, kv


async def _resolve_auto_total(container: Any) -> float:
    """Detected VRAM pool (GB), or the configurable fallback + a fail-loud
    finding when detection has never succeeded."""
    detected = await container.gpu_registry.total_vram_gb()
    if detected is not None:
        return detected
    fallback = container.site_config.get_float("gpu_vram_autodetect_fallback_gb", 32.0)
    from utils.findings import emit_finding

    emit_finding(
        source="vram_budget",
        kind="vram_autodetect_failed",
        severity="warn",
        title="GPU VRAM auto-detect unavailable",
        body=(
            f"Could not read total GPU VRAM from Prometheus; using fallback "
            f"{fallback}GB for the num_ctx budget guard until detection recovers."
        ),
        dedup_key="vram_autodetect_failed",
    )
    return fallback
```

- [ ] **Step 4: Await the caller**

In `_clamp_num_ctx_to_budget` (line 232), change:

```python
    total, reserve, kv = _budget_inputs(provider_config)
```
to:
```python
    total, reserve, kv = await _budget_inputs(provider_config)
```

- [ ] **Step 5: Run the new tests + the dispatcher suite**

Run: `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_dispatcher_vram_budget.py tests/unit/services/ -k "dispatcher or vram or budget" -q`
Expected: PASS. Fix any other caller the grep `_budget_inputs(` surfaces (there should be exactly one — line 232).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/llm_providers/dispatcher.py src/cofounder_agent/tests/unit/services/test_dispatcher_vram_budget.py
git commit -m "feat(gpu): resolve gpu_vram_total_gb=auto via GPURegistry in budget guard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Grafana — "Detected VRAM pool" stat panel

**Files:**
- Modify: `infrastructure/grafana/dashboards/hardware-power.json`

**Interfaces:**
- Consumes: Prometheus `sum(nvidia_gpu_memory_total_mib) / 1024`.
- Produces: a `stat` panel titled "Detected VRAM pool".

- [ ] **Step 1: Add the panel object**

In the dashboard's top-level `"panels"` array, add the following object immediately after the panel with `"id": 56` (the "VRAM headroom (dedicated)" panel). Use a fresh unique `id` (scan the file for the max existing `id` and use max+1; the snippet uses `90` as a placeholder — change if `90` is taken) and place it on a new grid row:

```json
    {
      "id": 90,
      "type": "stat",
      "title": "Detected VRAM pool",
      "description": "Total GPU VRAM across all cards (sum of nvidia_gpu_memory_total_mib). This is the number the dispatcher's gpu_vram_total_gb=\"auto\" budget guard derives.",
      "datasource": { "type": "prometheus", "uid": "local-prometheus" },
      "gridPos": { "h": 4, "w": 6, "x": 0, "y": 22 },
      "fieldConfig": {
        "defaults": {
          "unit": "decgbytes",
          "color": { "mode": "fixed", "fixedColor": "blue" }
        },
        "overrides": []
      },
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "colorMode": "value",
        "graphMode": "none",
        "textMode": "value"
      },
      "targets": [
        {
          "datasource": { "type": "prometheus", "uid": "local-prometheus" },
          "expr": "sum(nvidia_gpu_memory_total_mib) / 1024",
          "legendFormat": "VRAM pool",
          "refId": "A"
        }
      ]
    },
```

- [ ] **Step 2: Validate JSON**

Run: `python -c "import json; json.load(open(r'infrastructure/grafana/dashboards/hardware-power.json')); print('valid')"`
Expected: `valid`. (CI `grafana-panels-lint` provides the deeper check on push.)

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/dashboards/hardware-power.json
git commit -m "feat(obs): add Detected VRAM pool stat panel to Hardware & Power

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Docs — multi-GPU VRAM pool

**Files:**
- Modify: `docs/operations/single-gpu-vram-tuning.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Add a multi-GPU pool section**

Append a section to `docs/operations/single-gpu-vram-tuning.md` (keep existing single-GPU guidance; this documents the auto-detect behavior):

```markdown
## Multi-GPU VRAM pool (auto-detection)

`gpu_vram_total_gb` defaults to `auto`. On boot the dispatcher's budget guard
asks `GPURegistry` for the total VRAM pool — the sum of every GPU's
`nvidia_gpu_memory_total_mib`, read from Prometheus — and uses it as the budget
(`pool − gpu_desktop_reserve_gb`). You never hand-set it.

- **Override:** set `gpu_vram_total_gb` to a number to pin the budget below
  physical VRAM (e.g. a multi-tenant cap). Any non-`auto` value skips detection.
- **Fallback:** if detection is unavailable (Prometheus unreachable at startup),
  the guard falls back to `gpu_vram_autodetect_fallback_gb` (default `32`) and
  emits a `vram_autodetect_failed` finding. Detection self-heals on a later call.
- **Reserve:** `gpu_desktop_reserve_gb` (default `3`) is subtracted once — it
  models desktop overhead on the single display card.

### Sharding caveat (mixed cards / slow interconnect)

When a model is larger than the biggest single card, Ollama shards it across
GPUs. Inter-GPU activations cross the PCIe bus every token, so a card on a
narrow link (e.g. x4) or an older architecture bottlenecks the shard. The pool
makes big models *fit*; it does not make them *fast*. Benchmark before committing
a latency-sensitive workload to a sharded model.
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/single-gpu-vram-tuning.md
git commit -m "docs(ops): document multi-GPU VRAM pool auto-detection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Run the touched suites green:
  `PYTHONPATH="$(pwd)" <venv-python> -m pytest tests/unit/services/test_gpu_registry.py tests/unit/services/test_container.py tests/unit/services/test_settings_defaults.py tests/unit/services/test_dispatcher_vram_budget.py tests/unit/services/test_gpu_scheduler.py -q`
- [ ] Confirm exactly one caller of `_budget_inputs` and that it awaits: `grep -rn "_budget_inputs(" src/cofounder_agent`.
- [ ] Push branch `claude/vram-pool-autodetect`, open PR against `main`, let CI (`test-backend`, `grafana-panels-lint`, `migrations-smoke`) gate, merge when green.
- [ ] After merge: the fresh-seed default is already `auto`, but the existing prod row is `32` — update it via `poindexter settings set gpu_vram_total_gb auto` (or leave a pinned value if preferred), then watch the "Detected VRAM pool" panel read ~56 GB and confirm a 70B model is no longer num_ctx-clamped.

## Notes / decisions deferred to execution

- **Eager vs lazy warm-up:** this plan is lazy (first dispatch with `"auto"` triggers detection). An optional later enhancement is an eager `await container.gpu_registry.total_vram_gb()` in worker lifespan startup to avoid a one-time clamp-path latency. Not required for correctness.
