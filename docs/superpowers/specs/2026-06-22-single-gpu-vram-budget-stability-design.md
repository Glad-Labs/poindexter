# Single-GPU VRAM Budget + Desktop-Stability Guard — Design

**Date:** 2026-06-22
**Status:** Design (approved verbally; pending written-spec review)
**Author:** Claude (brainstormed with Matt)

## Problem

The pipeline runs on a single RTX 5090 (32GB VRAM) shared with the Windows
desktop. The operator-felt symptom is **"memory pegging freezes my input"** —
the whole desktop (keyboard/mouse) becomes unresponsive under model load.

The leading mechanism is **NVIDIA's System Memory Fallback policy**: on Windows,
when a CUDA allocation would exceed VRAM, the driver does not hard-fail — it
silently pages VRAM↔system RAM over PCIe. During that spill the GPU saturates the
bus evicting memory, and the WDDM desktop compositor (which shares the GPU)
starves → input freezes. This sits _underneath_ the app-level VRAM-OOM and
host-RAM-exhaustion theories already ruled out in
`project_vram_oversub_docker_crashes` (those chased the wrong layer).

The pipeline cycles three distinct ~18GB-class LLMs (`gemma-4-31B-it-qat` writer,
`glm-4.7` reviser, `qwen3-vl:30b` vision) plus image/video models, serialized by
the `gpu_scheduler` `pg_advisory_lock`. Only one fits at a time; the churn is a
secondary concern (it costs swap latency and is the suspected `dxg`-passthrough
cause of WSL2 wedges) but is **not** the priority here.

### Operator priorities (from brainstorming, ranked)

1. **Stability** — the desktop must never freeze from VRAM pressure.
2. **Capability** — run the biggest/best model and the longest context that fit
   32GB _safely_.
3. Cutting swap-thrash is "would be good" but secondary; raw speed is the cheap
   currency (operator has explicitly said latency is acceptable to spend).

Because model-size and context-length trade directly against each other in fixed
VRAM, the operator chose **"build the budget first, decide later"**: ship the
stability core + a deterministic VRAM budget tool now, then pick the
size↔context operating point with real numbers in hand.

## Goals

- Make it **impossible** for the pipeline to freeze the desktop via VRAM spill.
- Provide a **deterministic VRAM budget calculator** that, for a given model +
  context + KV dtype, reports the footprint and whether it fits within
  `(total − desktop_reserve)`.
- Make **context size DB-configurable on every LLM path** (today it only reaches
  the legacy `ollama_client.py` callers, not the writer's LiteLLM dispatch path),
  with **per-phase overrides** and a **budget interlock** that clamps/refuses an
  unsafe context instead of allowing a spill.
- Stop "small" GPU models (embedder, cross-encoder reranker) from stacking on the
  resident large model.
- Surface VRAM headroom on a Grafana panel (everything-gets-a-panel).

## Non-goals (YAGNI)

- **vLLM.** Wrong tool for a single-tenant, serialized, model-swapping pipeline:
  its wins are continuous batching (no concurrency here) and it is built to _own_
  the GPU (pre-allocates ~90% VRAM for one model, no hot-swap). It worsens the
  VRAM-headroom problem and breaks the swap model.
- **Engine swaps** (ExLlamaV2/TabbyAPI, TensorRT-LLM). Only relevant if a single
  always-resident model + raw latency ever becomes the bottleneck. Not today.
- **Committing to a 70B now.** The budget calculator is the _prerequisite_ for
  that decision, not a casualty of it. The size↔context operating point is chosen
  in a later step using the tool this spec builds.
- Re-opening the writer-model bakeoff (`gemma-4-31B-it-qat` is a strong
  quality-per-GB QAT point; model _selection_ is downstream of the budget tool).

## Grounding findings (verified in code)

- **KV-cache quantization + flash attention are not set anywhere.** No
  `OLLAMA_KV_CACHE_TYPE` / `OLLAMA_FLASH_ATTENTION` in compose or env.
- **The reranker loads onto the GPU.** `rag_engine.py:630` calls
  `CrossEncoder(name)` with no `device=` arg → sentence-transformers defaults to
  CUDA. It is a "small" model (skips the `SMALL_MODEL_THRESHOLD_GB = 2.0` lock) so
  it stacks on the resident 18GB writer at the pinch point.
- **Context is a uniform 8192 and only wired into legacy callers.**
  `ollama_client.py:132 _default_num_ctx()` reads `ollama_num_ctx` (default 8192,
  already DB-backed) and applies it to the 3 direct `ollama_client.py`
  chat/generate/stream calls. **`litellm_provider.py` sets no `num_ctx`**
  (no `options` / `model_kwargs` / `extra_body`), and `llm_text.py` routes the
  writer through `dispatch_complete` without passing it → the writer almost
  certainly runs at Ollama's Modelfile-default context (commonly 4096), silently
  ignoring the 8192 setting. **To confirm at runtime:** `GET /api/ps` reports the
  loaded model's `context_length`.
- **Ollama runs on the host** (`OLLAMA_BASE_URL=http://host.docker.internal:11434`),
  so host-side env vars and the NVIDIA driver setting are the right control
  surface — not docker-compose.
- The `gpu_scheduler` already owns VRAM coordination (serialize lock,
  unload-before-image, `nvidia-smi`/Prometheus reads) — the natural home for a
  pre-load fit guard.

## Components

Each unit is isolated, has one job, and is independently testable.

### 1. Freeze-mechanism capture (step 0, throwaway diagnostic)

Confirm the spill hypothesis before building on it (operator history: don't
assume the memory layer). Extend the existing `#1844` wedge-forensics
(`docker-watchdog.ps1` `Save-WedgeForensics`, which already snapshots
`nvidia-smi`) to also record **shared/sysmem GPU memory** usage, and capture one
sample during a deliberate over-allocation (or at the next organic freeze).
Expected signal: nonzero "Shared GPU Memory" climbing while dedicated VRAM is
pinned at ~32GB. Deliverable is a confirmation note, not shipped code.

### 2. `vram_budget.py` — pure footprint calculator (new)

A dependency-free module of pure functions. No I/O.

```
estimate_kv_cache_gb(n_layers, n_kv_heads, head_dim, num_ctx, kv_dtype, batch=1) -> float
estimate_model_vram_gb(weight_bytes, kv_cache_gb, overhead_gb) -> float
fits(footprint_gb, total_gb, desktop_reserve_gb) -> (bool, headroom_gb)
max_safe_num_ctx(model_arch, total_gb, desktop_reserve_gb, kv_dtype) -> int
```

Model architecture (`n_layers`, `n_kv_heads`, `head_dim`, weight size) is read
**live from Ollama** via `GET /api/show` (`model_info`), not hardcoded — one thin
adapter `read_model_arch(model) -> ModelArch`. KV-cache bytes-per-element follow
the dtype (f16=2, q8_0≈1, q4_0≈0.5). This is the tool that answers "what is the
biggest model + longest context that fits safely?"

### 3. Pre-load fit guard in `gpu_scheduler`

Before an Ollama acquire (`owner == "ollama"`), resolve the target model +
effective `num_ctx`, call `vram_budget`, and compare against
`(gpu_vram_total_gb − gpu_desktop_reserve_gb)`:

- **Fits** → proceed.
- **Over budget** → clamp `num_ctx` down to `max_safe_num_ctx` (preferred) or
  refuse with a fail-loud finding, per `findings`/`feedback_no_silent_defaults`.
  Never let the request reach the driver's spill path.

This is the runtime enforcement that makes the no-spill backstop (component 4)
something the pipeline cooperates with rather than crashes into.

### 4. Host-side hardening (one-time, outside the repo tree)

- **NVIDIA System Memory Fallback → "Prefer No Sysmem Fallback"** so
  over-allocation fails clean instead of freezing the compositor. _Exact current
  control path (NVIDIA Control Panel vs. registry vs. per-process) to be verified
  in the plan; the policy was added ~driver R535._
- **`OLLAMA_FLASH_ATTENTION=1`** and **`OLLAMA_KV_CACHE_TYPE=q8_0`** in the host
  Ollama environment (q8_0 is near-lossless). Both are **global** Ollama server
  settings — Ollama has no native per-model KV dtype. If a model regresses under
  q8_0 KV (e.g. the vision model), the baseline fallback is to disable the global
  setting and revisit; a true per-model split would require a second Ollama
  instance on another port (extra host RAM + ops — out of scope unless forced).
- Document both in `docs/operations/` so a fresh box reproduces them.

### 5. Move embedder + reranker to CPU

`CrossEncoder(name, device="cpu")` at `rag_engine.py:630`, and the embedder
likewise. Stops "small" models nibbling headroom while the big model is resident;
the 9950X3D handles rerank/embed on CPU comfortably (latency is acceptable to
spend). Make the device a setting (`rag_rerank_device`, default `cpu`).

### 6. DB-configurable context, end to end

Three layers:

- **Thread `num_ctx` through the dispatch path.** Add `num_ctx` handling to
  `litellm_provider` (Ollama optional param) and pass the resolved value from
  `dispatch_complete` / `llm_text` so the **writer** honors the DB setting. This
  closes the seam gap in the findings above.
- **Per-phase overrides.** Resolve `num_ctx` as `<phase>_num_ctx` →
  `ollama_num_ctx` (global) → default. Writer/RAG phases go long; title/SEO stay
  small to save cache.
- **Budget interlock.** The resolved per-phase `num_ctx` is validated by the
  component-3 guard before load. The DB knob is therefore _crankable but safe_.

### 7. Config + Grafana

- New `app_settings` (in `settings_defaults.py`, not a migration —
  `feedback_seed_data_in_baseline`): `gpu_vram_total_gb` (32),
  `gpu_desktop_reserve_gb` (e.g. 3), `ollama_kv_cache_type` (`q8_0`),
  `ollama_flash_attention` (`true`), `rag_rerank_device` (`cpu`), and per-phase
  `<phase>_num_ctx` overrides (optional).
- **Grafana "VRAM headroom" panel**: `total − desktop_reserve − projected
footprint`, plus the loaded-model context length, on the Hardware & Power board.

## Data flow

```
content stage → dispatch_complete(model, phase)
   → resolve num_ctx: <phase>_num_ctx → ollama_num_ctx → default
   → gpu_scheduler.lock("ollama", model, phase)
        → read_model_arch(model)            [Ollama /api/show, cached]
        → vram_budget.fits(model, num_ctx)  [pure]
            ├─ fits      → acquire, load, generate
            └─ over      → clamp num_ctx (or refuse + finding)
   → litellm_provider(... num_ctx=resolved)  [now plumbed]
```

## Error handling

- Budget read failures (Ollama `/api/show` down) fail **open with a finding**, not
  silently — same fail-loud-but-recover pattern as `gpu_scheduler._cfg_*`. The
  no-spill driver setting is the hard backstop if the soft guard can't evaluate.
- Over-budget defaults to **clamp** (keep the pipeline running at a safe context)
  and emits a finding so the operator sees the tuned value didn't fully apply.
- KV-quant / flash-attn is a **global** Ollama server setting (no per-model
  override). If a model regresses under q8_0 KV, disable it globally and
  re-evaluate, emitting a finding. The budget calculator still models f16 vs.
  q8_0 so the headroom cost of reverting is visible.

## Testing

- **Unit (pure):** `vram_budget` math against known model arches (golden numbers
  for gemma-4-31B at 8K/16K/32K, q8_0 vs f16). `max_safe_num_ctx` monotonicity.
- **Unit:** per-phase `num_ctx` resolution precedence; clamp logic in the guard
  (mock arch + reserve).
- **Contract:** `litellm_provider` actually emits `num_ctx` on the Ollama request
  (the seam-gap regression guard).
- **Verification (manual, per `verify`):** `GET /api/ps` before/after shows the
  writer's `context_length` now follows the DB setting; deliberate over-allocation
  with no-sysmem-fallback set fails clean (no desktop freeze); Grafana headroom
  panel reads sane.

## Sequencing

1. **Stability now:** component 1 (capture) → component 4 (host hardening) +
   component 5 (CPU offload). Desktop stability is felt after this.
2. **Budget + control:** component 2 (`vram_budget`) → component 3 (fit guard) →
   component 6 (context plumbing + per-phase + interlock).
3. **Visibility:** component 7 (config keys + Grafana panel).
4. **Decide the operating point** (separate step, out of this spec): use the tool
   to pick max-context-31B vs. bigger-model-via-offload vs. two-profiles.

## Open questions (resolve in the plan)

- Confirm via `/api/ps` that the writer currently ignores `ollama_num_ctx`
  (expected, per findings).
- Exact NVIDIA sysmem-fallback control surface on this driver (NVCP / registry /
  per-process env) and whether it can be scripted for reproducibility.
- Whether `qwen3-vl:30b` tolerates flash-attn + q8_0 KV — these are global Ollama
  settings, so a regression means reverting globally (or a second Ollama
  instance), not isolating the vision model.
- **Resolved (2026-06-22):** the embedder is Ollama-served
  (`rag_engine._get_embed_model` reads `OLLAMA_BASE_URL`), not an in-process CUDA
  model — so it has no device flag and needs no `rag_embed_device`. It belongs to
  the model-swap/budget story (Plan 2), not the CPU-offload change.

## Diagnostic captures & applied changes (2026-06-22)

**Spill baseline (Task 1, idle):** RTX 5090 at 8.3GB dedicated / 1% util showed
`Shared Usage` = 0.72GB on the card's adapter (24GB dedicated free) — normal WDDM
baseline (desktop/browser-backed shared allocations), **not** spill. The
measurement method (`Get-Counter '\GPU Adapter Memory(*)\Shared Usage'`) works and
the 5090 adapter is identifiable. **Still pending:** a capture at high VRAM
pressure (a model load pushing dedicated -> ~32GB) to confirm `Shared Usage`
climbs multi-GB at the freeze. Not forced live to avoid freezing the operator's
active desktop.

**Peak under load (Task 1, after no-sysmem-fallback + Ollama restart):** 5090
`Shared Usage` ~1.65GB (1,769,287,680 B), up from 0.72GB idle (secondary adapter
8KB). No desktop lockup. With _Prefer No Sysmem Fallback_ enabled, genuine CUDA
over-allocation hard-fails rather than spilling, so ~1.65GB is consistent with
normal WDDM + pinned-transfer staging, not catastrophic spill. Fully definitive
confirmation still wants a paired `Shared Usage` + `Dedicated Usage` sample with
dedicated near 32GB.

**Applied (Plan 1):** reranker now defaults to CPU (`rag_rerank_device`, shipped);
host Ollama env set at User scope — `OLLAMA_FLASH_ATTENTION=1` +
`OLLAMA_KV_CACHE_TYPE=q8_0` (verified). **Remaining host actions for the
operator:** (1) NVIDIA Control Panel -> _CUDA - Sysmem Fallback Policy_ -> **Prefer
No Sysmem Fallback**; (2) restart the Ollama server to apply the env vars
(`ollama ps` was empty at apply-time -> zero-cost restart). See
[`docs/operations/single-gpu-vram-tuning.md`](../../operations/single-gpu-vram-tuning.md).
