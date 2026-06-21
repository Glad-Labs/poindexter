# GPU LLM-Dispatch Serialization

- **Date:** 2026-06-21
- **Status:** Accepted
- **Related:** PR #1766 (media render holds `gpu.lock("video")`), 2026-06-19 validation finding #7 / #4-serial residual. Follow-up: brain writer-probe gating + `OllamaNoModelsLoaded` reachability guard (the brain is the one un-gated caller the worker-side chokepoint can't reach).

## Problem

The stack shares one 32 GB GPU across Ollama LLM inference (writer/director,
~19 GB), SDXL image generation, and wan video render (~14.8 GB).
`services/gpu_scheduler.py::gpu.lock(owner)` serializes these through a
two-tier lock — an in-process `asyncio.Lock` plus a cross-process
`pg_advisory_lock` on a dedicated connection — and `gpu.lock("sdxl"|"video")`
evicts any loaded Ollama model before a render.

Content-pipeline stages correctly wrap their LLM work in `gpu.lock("ollama")`.
But scheduled worker jobs/services (topic research, SEO, newsletter) call the
LLM through `dispatcher.dispatch_complete` **without** acquiring the lock. So a
scheduled LLM call can load the 19 GB writer _concurrently_ with an in-flight
video render, exceed 32 GB VRAM, and fail the render.

Observed 2026-06-21: `gemma-4-31B` pinned at 19 GB VRAM (kept hot by scheduled
LLM jobs resetting Ollama's keep-alive) while a render needed wan's ~14.8 GB —
they cannot coexist, and the render path had no way to keep the writer out.

## Goal

Durable internal GPU serialization: no LLM call — content **or** scheduled —
runs concurrently with a media render, enforced at a single chokepoint so no
future caller can reintroduce the gap.

## Design

1. **Reentrant `gpu.lock`** (`gpu_scheduler.py`). A module-level
   `ContextVar` (`_gpu_session_active`) records whether the current async call
   chain already holds the GPU. `lock()` becomes reentrant: if the flag is set,
   the call is a pass-through no-op (no second `asyncio.Lock` / `pg_advisory_lock`
   acquire, no re-eviction); otherwise it acquires exactly as before and
   sets/resets the contextvar token in `try`/`finally`. Generator-based context
   managers run in the caller's context, so the `set()` is visible to nested
   `gpu.lock()` calls within the same `async with` body and is reset on exit.

2. **Lock at the chokepoint** (`dispatcher.py::dispatch_complete`). For LOCAL
   calls (`not _is_paid_llm_call(model, provider_config)`) and when
   `gpu_serialize_llm_dispatch` is true, wrap `provider.complete(...)` in
   `async with gpu.lock("ollama", model=model, task_id=task_id, phase=phase)`.
   Cloud calls (no local GPU) and embeddings (a separate code path) skip it.
   Reentrancy makes this safe inside content stages that already hold
   `gpu.lock("ollama")` — their nested `dispatch_complete` is a no-op — while
   still catching every previously-unwrapped scheduled call.

3. **Tunable** `gpu_serialize_llm_dispatch` (default `true`) in
   `settings_defaults.py`, mirroring `pipeline_explicit_writer_unload_before_sdxl`
   — abundant-VRAM operators can opt out.

## Net effect

A media render holding `gpu.lock("video")` (which already evicts Ollama) now
blocks every local LLM call until it finishes, and an in-flight local LLM call
blocks a render from starting mid-inference. Contention is impossible by
construction, at the one point every LLM call already flows through.

## Brain writer-model probe (separate process, non-blocking)

The chokepoint above closes the gap for the worker — but the
`poindexter-brain-daemon` is a **separate container** (stdlib + asyncpg +
urllib only; no FastAPI / `services` imports), so it can neither import
`gpu_scheduler` nor route through `dispatch_complete`. Its `content_gen` health
probe (`brain/health_probes.py::probe_content_gen`) exercises the DB-configured
writer via Ollama `/api/generate`, loading the ~19 GB writer into VRAM — the
same contention, from a process the chokepoint can't see.

The brain shares one thing with the worker: Postgres. So it takes the **same**
cross-process advisory lock, by value — `GPU_ADVISORY_LOCK_KEY = 7_777_777_777`
duplicated into `health_probes.py` with a comment cross-referencing
`services/gpu_scheduler.py` (the brain can't import the constant). Two
differences from the worker path:

1. **Non-blocking.** A health probe must not stall for a multi-minute render,
   so it uses `pg_try_advisory_lock` (not the worker's blocking
   `pg_advisory_lock`). Lock held → **skip** this cycle; lock free → run the
   probe, then `pg_advisory_unlock` on the **same** pinned connection in a
   `finally` (advisory locks are session-scoped — a leaked lock would wedge the
   worker's real scheduler).
2. **Skip is non-alerting and observable.** The skip returns
   `{"ok": true, "status": "skipped_gpu_busy"}` (logged + persisted to
   `brain_knowledge`), NOT a failure — reporting the writer DOWN merely because
   the GPU is legitimately busy would be a false page.

Net: the brain's writer probe never adds VRAM pressure during a render, and a
render still can't start mid-probe (the probe holds the real lock for its
~30 s `/api/generate`, at most once per 30 min).

## False-critical: `OllamaNoModelsLoaded`

During the same 2026-06-21 render the `OllamaNoModelsLoaded` alert fired a
spurious **critical** at 18:21. It is **not** brain-raised — it is a DB-rendered
rule (`prometheus_rule_builder.py`) over `poindexter_ollama_model_count`, a
gauge the **worker's** `metrics_exporter` sets from Ollama `/api/tags` (counts
_installed_ models, which a VRAM eviction never empties). Under render load that
3 s scrape can time out, and the exporter's `except` branch zeroes **both**
`poindexter_ollama_reachable` and `poindexter_ollama_model_count`. The bare
`model_count == 0` expr then pages "up but no models" when the truth is "Ollama
didn't answer in time" — already owned by the static `PoindexterOllamaDown`
(`reachable == 0`).

Fix: guard the expr with `unless poindexter_ollama_reachable == 0` (mirrors the
existing `unless approval_queue_length > 0` cost-alert idiom). Timeouts route to
the reachability alert; only the genuine up-but-empty case (`reachable=1,
count=0`) still fires the dedicated critical.

## Testing

- `test_gpu_scheduler.py`: nested `gpu.lock` does not deadlock; the
  cross-process pg-advisory lock is acquired exactly once across nesting; the
  contextvar resets so a subsequent independent lock acquires fresh; a reentrant
  inner acquire does not re-evict Ollama.
- dispatcher tests: `dispatch_complete` locks local calls, skips cloud calls,
  and honors `gpu_serialize_llm_dispatch=false`.
- `test_brain_health_probes.py::TestProbeContentGenGpuLock`: the writer probe
  skips (non-alerting, `status=skipped_gpu_busy`) without calling
  `/api/generate` when `pg_try_advisory_lock` returns false; probes with the
  shared `GPU_ADVISORY_LOCK_KEY`; runs and releases on the same connection when
  the lock is free; and unlocks in `finally` even when the probe body raises.
- `test_prometheus_rule_builder.py::TestOllamaNoModelsLoadedRule`: the
  `OllamaNoModelsLoaded` expr carries the `unless reachable == 0` guard and it
  survives YAML rendering.

## Rollout

Pure code plus a default-true setting; takes effect on worker restart
(deploy-from-sync). No migration. Reversible via `gpu_serialize_llm_dispatch=false`.

The brain probe gating is image-baked into `poindexter-brain-daemon`, so it
takes effect on a brain rebuild + recreate (not a bind-mount restart). The
`OllamaNoModelsLoaded` guard re-renders into `rules/*.yml` within ~5 min via
`RenderPrometheusRulesJob` (no restart needed). Both are pure code — no
migration, no settings.
