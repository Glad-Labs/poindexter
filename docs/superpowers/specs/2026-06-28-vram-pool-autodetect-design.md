# Auto-detected VRAM pool — design

**Date:** 2026-06-28
**Status:** approved (brainstorming) — pending implementation plan
**Author:** Claude (with Matt)

## Problem

`gpu_vram_total_gb` (default `32`) and `gpu_desktop_reserve_gb` (default `3`)
are hand-set constants that mirror Matt's single RTX 5090. The dispatcher's VRAM
budget guard (`services/llm_providers/dispatcher.py::_budget_inputs` →
`services/vram_budget.py`) reads them and clamps `num_ctx` so a model's projected
footprint stays within `total − reserve`, preventing the NVIDIA driver from
spilling VRAM into system RAM (the WDDM sysmem-fallback that freezes the
desktop).

A second GPU was added 2026-06-27 (RTX 3090, 24 GB, alongside the 5090's 32 GB)
to get a larger VRAM pool — primarily so image/video generation stops hitting
the 32 GB ceiling and so a ~70B-class writer (Q4_K_M ≈ 40 GB) becomes feasible.
Ollama (host process, no `CUDA_VISIBLE_DEVICES` pin) already sees both cards and
will auto-shard a model that doesn't fit on one. The blocker is purely the
app-level budget guard: it still believes the box has 32 GB and would clamp or
refuse a model that actually fits in the ~56 GB pool.

**Goal:** the total-VRAM number should be auto-detected, never hand-set, while
remaining overridable for a SaaS / multi-tenant operator on different hardware.

## Non-goals

- **Parallel throughput across GPUs.** Today a single global GPU lock
  (`GPU_ADVISORY_LOCK_KEY` in `gpu_scheduler.py`) serializes all model work —
  one model at a time, box-wide. This design keeps that. When the LLM runs it
  may use the whole pool; running an LLM and a video render _concurrently_ on
  separate cards is a separate, deferred effort (per-GPU locks + placement).
- **Static workload pinning** (e.g. dedicating the 3090 to video). Possible
  later; out of scope here.
- **Fixing the PCIe x4 link.** The 3090 sits in the only second slot the board
  offers (x4); the primary is PCIe 5.0 x16. This is a hardware constraint that
  caps sharded-model performance (inter-GPU activations cross x4 each token).
  Accepted, not addressed in software.

## Decisions (locked during brainstorming)

1. **Pool scope = all detected GPUs.** Total = sum of every GPU's VRAM. Matches
   the single-lock "one model at a time" reality — the running model can
   legitimately use all cards.
2. **Detection source = Prometheus**, summing `nvidia_gpu_memory_total_mib`. The
   dispatcher runs in a GPU-less container (`poindexter-prefect-worker` has no
   nvidia device reservation), so it cannot run `nvidia-smi` directly; it reads
   VRAM totals through the telemetry path exactly as `gpu_scheduler` reads
   util/power via `_query_prometheus_scalar`.
3. **`"auto"` sentinel preserves override.** `gpu_vram_total_gb` defaults to
   `"auto"` → detect; any explicit number wins. One field serves both audiences
   (Matt: never set it; SaaS operator: cap a tenant below physical VRAM).
4. **Failure posture = fail-loud + configurable fallback.** If `"auto"` but
   detection has never succeeded, emit an operator finding and fall back to a
   conservative budget until detection recovers (pipeline keeps running). The
   fallback value is itself a tunable app_setting
   (`gpu_vram_autodetect_fallback_gb`, default `32`) — not a hardcoded constant.

## Architecture

### New component: `services/gpu_registry.py`

A single-purpose unit: _detect total VRAM across the box, once._

```
class GPURegistry:
    def __init__(self, *, prometheus_query_url_provider, http_client_provider, ...)
    async def total_vram_gb(self) -> float | None
```

- **Detection:** instant query `sum(nvidia_gpu_memory_total_mib)` against
  Prometheus, convert MiB→GB (`/1024`). Returns `None` on any
  connectivity/empty result.
- **Memoization:** total VRAM is a static hardware constant for a process's
  lifetime, so the first successful detection is cached permanently. While the
  cache is empty (detection has not yet succeeded), each call retries — a
  startup Prometheus blip self-heals on a later call. No TTL.
- **Ownership:** exposed as an `AppContainer` `cached_property`
  (`services/container.py`), DI-consistent — no module-level singleton, and each
  test container gets a fresh instance. Mirrors how other services hang off the
  container.
- **Reuse:** the Prometheus URL resolution and HTTP client follow the existing
  `gpu_scheduler` pattern (`_prometheus_query_url()`, shared `httpx.AsyncClient`).

### Consumer change: `dispatcher.py::_budget_inputs`

Currently:

```python
total = sc.get_float("gpu_vram_total_gb", 32.0)
reserve = sc.get_float("gpu_desktop_reserve_gb", 3.0)
```

Becomes (sketch):

```python
raw = (sc.get("gpu_vram_total_gb", "auto") or "auto").strip().lower()
if raw in ("", "auto"):
    detected = await container.gpu_registry.total_vram_gb()
    if detected is not None:
        total = detected
    else:
        total = sc.get_float("gpu_vram_autodetect_fallback_gb", 32.0)
        emit_finding(source="vram_budget", kind="vram_autodetect_failed",
                     severity="warn", ...)  # once; fail-loud
else:
    total = float(raw)
reserve = sc.get_float("gpu_desktop_reserve_gb", 3.0)
```

`_budget_inputs` is currently sync; resolving `"auto"` needs the async registry.
The implementation plan resolves this (make `_budget_inputs` async — its sole
caller is already on the async clamp path — or have the registry expose a sync
cached read seeded by an async refresh). Detail deferred to the plan; the clamp
must remain fail-open on any error (never break dispatch).

**Reserve semantics:** `usable = total_pool − gpu_desktop_reserve_gb`, the
reserve subtracted once (it models desktop overhead on the single display card;
the 3090 has no desktop). On this box: (32 + 24) − 3 = **53 GB usable** — enough
for a Q4_K_M 70B (~40 GB) plus KV headroom.

### Settings (`services/settings_defaults.py`)

- `gpu_vram_total_gb`: default `'32'` → `'auto'`; metadata `value_type`
  `'float'` → `'string'` (accepts `"auto"` or a number).
- `gpu_desktop_reserve_gb`: unchanged (`'3'`).
- `pipeline_gpu_index` (added 2026-06-27): unchanged and **orthogonal** — it
  selects the one card for util/power _gauges_; the pool _sums all_ cards for
  the _budget_. No conflict.
- `gpu_vram_autodetect_fallback_gb`: **new**, default `'32'`, `value_type`
  `'float'`, owner `gpu_scheduler`. The conservative budget used only when
  `"auto"` detection has never succeeded (the loud-flagged unhappy path). Tunable
  so an operator on a smaller card can lower the floor (a 32 GB fallback on a
  12 GB card would over-promise) — per the "every tunable in app_settings" rule.

### Visibility (Grafana)

Add a stat panel to **Hardware & Power** (`infrastructure/grafana/dashboards/hardware-power.json`):
"Detected VRAM pool" = `sum(nvidia_gpu_memory_total_mib) / 1024` (unit GB) — so
the number the budget guard derives is visible at a glance, per the
"every metric gets a panel" principle.

## Data flow

```
startup / first dispatch with gpu_vram_total_gb="auto"
   → GPURegistry.total_vram_gb()
       → (cache empty) Prometheus sum(nvidia_gpu_memory_total_mib)
       → MiB→GB, cache permanently on success
   → dispatcher._budget_inputs: total = detected
        (or gpu_vram_autodetect_fallback_gb + finding on fail)
   → vram_budget: usable = total − reserve; clamp num_ctx to fit
```

## Error handling

| Condition                                   | Behavior                                                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `"auto"`, detection succeeds                | use detected pool; cache for process life                                                                     |
| `"auto"`, detection fails (never succeeded) | fallback to `gpu_vram_autodetect_fallback_gb` (default 32) + **fail-loud finding** (once); retry on next call |
| explicit numeric override                   | use it; never query Prometheus                                                                                |
| any exception on the clamp path             | fail open (no clamp); driver no-sysmem-fallback is the backstop (existing behavior)                           |

## Testing

- **`test_gpu_registry.py`** (new): `"auto"` → detected sum (mock Prometheus
  JSON); MiB→GB conversion; permanent memoization (second call doesn't re-query);
  detection failure → `None`; retry-after-failure succeeds and caches.
- **`test_settings_defaults.py`**: update `gpu_vram_total_gb` expected default
  (`'auto'`) and `value_type` (`'string'`); confirm the value-type validator
  accepts it.
- **dispatcher `_budget_inputs`**: `"auto"` path calls the registry; numeric
  override bypasses it; detection-fail path uses `gpu_vram_autodetect_fallback_gb`
  and emits the finding once (and a non-default fallback value is honored).
- Full `test-backend` green before merge (per repo norms).

## Docs

- Update `docs/operations/single-gpu-vram-tuning.md` → multi-GPU pool: the
  `"auto"` model, the override, the reserve semantics, and the x4-shard
  performance caveat.

## Rollout / backward-compat

- Fresh installs and Matt's box: default `"auto"` → detection → real pool.
- Any operator who had explicitly set `gpu_vram_total_gb` to a number keeps that
  value (override path unchanged). No migration needed — the seeder uses
  `ON CONFLICT DO NOTHING`, so existing rows are untouched; only fresh seeds get
  `"auto"`.
- `value_type` change is metadata-only (drives admin UI/validation), no data
  migration.

## Open implementation details (for the plan, not blockers)

- Sync-vs-async resolution of `_budget_inputs` (see Consumer change).
- Exact finding `kind`/throttle (emit once per process, not per dispatch).
- Whether the registry seeds an eager refresh in worker lifespan startup (warm
  cache) or stays purely lazy (first-dispatch detection). Lazy is simpler; eager
  avoids a one-time clamp-path latency. Plan decides.
