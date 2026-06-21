# GPU LLM-Dispatch Serialization

- **Date:** 2026-06-21
- **Status:** Accepted
- **Related:** PR #1766 (media render holds `gpu.lock("video")`), 2026-06-19 validation finding #7 / #4-serial residual

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

## Testing

- `test_gpu_scheduler.py`: nested `gpu.lock` does not deadlock; the
  cross-process pg-advisory lock is acquired exactly once across nesting; the
  contextvar resets so a subsequent independent lock acquires fresh; a reentrant
  inner acquire does not re-evict Ollama.
- dispatcher tests: `dispatch_complete` locks local calls, skips cloud calls,
  and honors `gpu_serialize_llm_dispatch=false`.

## Rollout

Pure code plus a default-true setting; takes effect on worker restart
(deploy-from-sync). No migration. Reversible via `gpu_serialize_llm_dispatch=false`.
