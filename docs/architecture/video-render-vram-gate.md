# Video-render VRAM gate + reclaim

**Status:** gate live (PR 1, 2026-07-12). Reclaim live (PR 2, 2026-07-12) — the idle-WSL-reset half of PR 2 ships disabled pending a supervised live run (see below). **Related:** [`video-pipeline-redesign.md`](video-pipeline-redesign.md), plan `docs/superpowers/plans/2026-07-12-video-render-vram-gate-reclaim.md`.

## Problem

The Wan 2.2 TI2V-5B hero renderer (`scripts/wan-server.py`) loads **~24 GB resident** onto `pipeline_gpu_index` — on the operator box that's the **RTX 5090, which also drives the desktop**. When stale WSL2/Docker GPU retention (`vmwp`/`vmmemWSL`) plus the desktop compositor already hold ~18 GB, the render's 24 GB pushes demand past the card's 32 GB:

- **CUDA OOM** → the render process crashes/SIGKILLs (and can leak its allocation into the WSL VM, shrinking headroom further), and
- **WDDM spills to shared system RAM** → the display compositor starves → **the desktop locks up.**

Root-cause investigation (2026-07-12): the scheduler's pre-render eviction only frees Ollama (~0.3 GB resident), never the ~18 GB actually occupying the card, so the render dispatched blind and oversubscribed. Renders succeeded only overnight, when the desktop was idle and the card was clear.

## The gate

`services/media_infra_health.py::check_media_infra_health` — already the Stage‑2 dispatch health gate for wan-server / image-gen / DNS — gains a **render‑GPU free‑VRAM preflight**:

1. `services/render_vram.py::render_gpu_free_vram_gb(site_config)` reads live free VRAM on `pipeline_gpu_index` from Prometheus: `nvidia_gpu_memory_total_mib{gpu="<idx>"} − nvidia_gpu_memory_used_mib{gpu="<idx>"}` (base URL `gpu_metrics_prometheus_url`, default `http://prometheus:9090` — the same seam the GPU scheduler already uses). Returns `None` when unreadable.
2. If free VRAM `< media_render_min_free_vram_gb` **or** unreadable, the pass is `unhealthy` (with `vram_insufficient=True`).
3. `services/jobs/dispatch_media_pipeline.py` already **defers the whole cycle** on an unhealthy pass and leaves the piece intact for the next cycle — so a render can never start unless the card has room.

**Fail-closed by design.** An unreadable VRAM reading defers rather than rendering blind — the cost of a false defer (video waits) is trivial next to the cost of a false render (desktop freeze). This mirrors the existing wan/image-gen probes, which also defer on failure.

`vram_insufficient` distinguishes a VRAM-only defer from a wan/image-gen/DNS outage, so the reclaim follow-up (PR 2) can attempt to free VRAM before deferring — a reclaim during an infra outage would be pointless.

### TTS probe (2026-08-15)

The same pass also probes the **configured TTS engine's `/health`** —
`podcast_tts_engine='chatterbox'` → `plugin.tts_provider.chatterbox.base_url`,
anything else → Speaches via `podcast_tts_base_url` (URL resolution shared
with `probe_narration_failure` via `resolve_tts_health_url`). Narration is
fail-soft by design, so before this probe a TTS outage did not defer anything:
it shipped **silent, caption-less videos** (captions are Whisper ASR _of the
narration_, so they die together). The 2026-08-13 outage — the chatterbox
container is an opt-in `--profile tts-hq` service that a stack stop leaves
stopped and a plain `compose up -d` never restarts — ran two days and every
render in the window was operator-rejected. A wan outage defers; a TTS outage
must too.

Skipped entirely when `podcast_tts_enabled` is off (an install that
deliberately runs without TTS keeps rendering, silent by choice) or when no
engine URL resolves. A TTS failure never sets `vram_insufficient` — a VRAM
reclaim can't fix a stopped sidecar. Because `media_reconciliation` requires a
healthy pass before resetting a cap-wedged task's re-dispatch counter, attempts
burned during a TTS outage now also self-heal on recovery, same as wan outages.

## Settings (`settings_defaults.py`, DB-tunable)

| Key                              | Default                  | Meaning                                                                                |
| -------------------------------- | ------------------------ | -------------------------------------------------------------------------------------- |
| `media_render_vram_gate_enabled` | `true`                   | Master switch for the preflight.                                                       |
| `media_render_min_free_vram_gb`  | `25`                     | Min free VRAM on `pipeline_gpu_index` to allow a render (~24 GB model + ~1 GB margin). |
| `pipeline_gpu_index`             | `0`                      | The render/display GPU (existing key — the 5090).                                      |
| `gpu_metrics_prometheus_url`     | `http://prometheus:9090` | Prometheus base URL (existing key).                                                    |
| `media_tts_gate_enabled`         | `true`                   | TTS-engine probe; only consulted while `podcast_tts_enabled=true`.                     |

## Observability

The **Hardware & Power** dashboard carries a dedicated **"Render VRAM gate — free (render GPU)"** stat panel (`gpu="0"` free VRAM in GB, background red below the 25 GB gate / green at or above) — the at-a-glance "is video gated right now?" signal, so a quiet video lane during desktop use reads as _expected deferral_, not a failure. The broader per-GPU trend stays on the adjacent "VRAM headroom" panel (`nvidia_gpu_memory_used_mib` / `nvidia_gpu_memory_total_mib`). A deferral is also visible as a `dispatch_media_pipeline` scheduler log `detail='… render-GPU free VRAM … < 25 GB required …'`.

> The panel's `gpu="0"` filter mirrors the `pipeline_gpu_index` default; a static dashboard can't read `app_settings`, so if you retune `pipeline_gpu_index`, update the panel query to match.

## Reclaim (PR 2, 2026-07-12)

The gate alone only _prevents_ the lockup — it doesn't free anything, so with the WSL GPU baseline high, renders would mostly just **defer forever**. PR 2 adds an active, bounded reclaim before giving up:

1. **Hard image-gen unload** — `POST /unload {"hard": true}` (`scripts/image-gen-server.py`) now unloads the pipeline _and exits the process_. `torch.cuda.empty_cache()` alone does **not** return the CUDA context/reserved pool to the host under WSL2 (confirmed 2026-07-12: soft `/unload` freed 0 GB; a container restart freed ~7 GB) — only a process exit does. Docker's `restart: unless-stopped` on `poindexter-image-gen-server` brings it back; it lazy-loads on the next `/generate`. The pre-existing soft `/unload` (no body) is unchanged — the GPU scheduler's `prepare_mode('ollama'/'idle')` callers still get the old behavior.
2. **`gpu_scheduler._unload_image_gen(hard: bool = False)`** — the scheduler's existing eviction helper gained the `hard` param; it POSTs `{"hard": true}` when set. A hard call's HTTP response commonly reads as a connection error (the process exits before uvicorn can flush the response) — that's the reclaim _working_, not a failure; logged at debug, distinct from the "server likely offline" case.
3. **`services/jobs/dispatch_media_pipeline.py::_attempt_vram_reclaim`** — when the gate's pre-dispatch probe is unhealthy **specifically because `vram_insufficient=True`** (never for a wan/image-gen/DNS outage — restarting image-gen mid-outage is pointless), the job: evicts Ollama (`gpu._unload_ollama_models()`, ~0.3 GB) → hard-unloads image-gen (~7 GB) → soft-unloads chatterbox → hard-unloads wan → sleeps `media_render_reclaim_settle_seconds` (default `8`, lets the freed VRAM show up in the next Prometheus scrape) → re-probes the gate **once**. A healthy re-probe lets the cycle dispatch immediately; still-unhealthy falls through to the pre-existing defer path unchanged. Bounded to one reclaim attempt per cycle — never a retry loop.

   The **wan rung** (poindexter#962, 2026-08-01) exists because wan's own idle unloader frees only the pipeline _objects_: the process keeps its multi-GB CUDA reserved pool until exit — observed **10,240 MiB still held ~6.5 h after the last render**, pinning the gate under its floor all night with no lever able to touch it. `POST /unload {"hard": true}` mirrors image-gen's contract exactly: measures `memory_reserved` (allocated is ~0 by construction after the object drop), declines with `nothing_to_reclaim` below the `WAN_HARD_UNLOAD_MIN_RESERVED_MB` floor (default 512, env — the wan-server is deliberately DB-free), else exits so Docker's `restart: unless-stopped` revives it cold. `scripts/wan-server.py` is **baked into its image** — activating a change there needs `start-stack.sh build wan-server`, not a restart.

### The ghost on the ladder (poindexter#999, 2026-08-07)

`stable-audio` — the SFX server — was squatting **~11 GiB on the render GPU** with `"model_loaded": false`, and no lever in the system could touch it: its `/unload` was soft-only, and it had never been added to `_attempt_vram_reclaim`.

| step                      | GPU0                           |
| ------------------------- | ------------------------------ |
| before                    | 13071 MiB                      |
| after soft `POST /unload` | 13068 MiB — **freed 3 MiB**    |
| after process restart     | 2107 MiB — **freed 10.96 GiB** |

This is the same defect as wan (#962) and image-gen before it — dropping the model objects returns nothing, because what squats is torch's caching-allocator pool plus the CUDA context, and only a process exit returns those. It is the third instance, so **treat it as a class, not an incident**: any GPU sidecar that lazy-loads a model needs the hard-unload contract _and_ a seat on the ladder, or it becomes invisible dead weight.

It also explains the `vram_reclaim_ineffective` findings. wan peaks at **25.4 GiB** (measured, production geometry 480x832 x117f x50steps — 144.1s, matching the live server's logged 147.3s / 147.8s renders) on a 31.8 GiB card. With an 11 GiB ghost the arithmetic was 36.4 GiB on a 31.8 GiB card: **impossible**, and the reclaim reported "freed nothing" because it was dutifully evicting four services that between them held almost nothing.

Fixed by mirroring wan's contract exactly — floor-gated `os._exit(0)` behind `{"hard": true}` (`STABLE_AUDIO_HARD_UNLOAD_MIN_RESERVED_MB`, default 512), the in-flight decline from #992 so the reclaim can never kill a render it is trying to make room for, a self-driven hard exit on the idle path, and `vram_reserved_mb` + `inflight` on `/health` so the next squat is one curl away instead of an nvidia-smi PID hunt.

> **Measured, not computed.** CPU offload was evaluated as an alternative (`enable_model_cpu_offload`): it does work now that the old ~23 GB WSL RAM ceiling is gone, cutting wan's peak to **15.5 GiB** — but at **324.8s vs 144.1s**, a 2.25× penalty. Reclaiming the ghost buys more headroom for free, so offload stays unused. Keep it in mind only for a card that genuinely cannot be cleared.

### Two-phase render order (poindexter#966, 2026-08-01)

The per-clip pre-hero unload above turned out to need the render order #907 originally proposed. The render pass was sequential, so a **mid-list** hero evicted image-gen while ~24 GB of wan crowded the card — every later image-gen-family shot, and every later hero's own init still, failed deterministically into a Pexels substitute (task e68e4fe8 rendered twice, substituting the identical 4 shots both times; QA can't see it because substitutes are real footage). `_render_pass` (`shot_list_renderer.py`) now runs two VRAM-coherent phases: **still phase** — every non-hero shot plus every hero's image-gen init still, in list order, with image-gen resident throughout; **hero phase** — each pre-rendered still animated via wan (`_render_hero_still` / `_animate_hero`). The first animation's hard-unload now fires when image-gen has no work left in the pass; later heroes' unload calls no-op via the `nothing_to_reclaim` decline.

This closes the ~7 GB image-gen slice of the deficit. The remaining ~8.6 GB stuck in WSL2's `vmwp`/`vmmemWSL` (stale Docker GPU retention that outlives any container restart) needs a host-side `wsl --shutdown` + Docker Desktop restart — that's the idle-only WSL reset below.

### The reclaim is not free — two guards (2026-07-28)

A hard unload costs image-gen a cold start plus a lazy model reload, and **any `/generate` landing in that window fails**. On the article path a failed render used to silently become a stock photo, so an over-eager reclaim degrades blog images to buy VRAM for a video render that may never happen. Two guards keep that trade honest:

1. **Server-side floor.** `/unload {"hard": true}` now refuses to exit unless at least `image_gen_hard_unload_min_reserved_mb` (default `512`) is actually **reserved**. The endpoint previously logged `torch.cuda.memory_allocated()`, which is `0` by construction immediately after `unload_pipeline()` drops the tensors — so the number it printed could never detect the thing it was trying to reclaim. `torch.cuda.memory_reserved()` is the caching-allocator pool a process exit really returns, and it is what the gate reads.
2. **Caller-side cooldown.** The per-cycle conditions were already correct (eligible work **and** a specifically-VRAM failure), but nothing remembered across cycles, so on a 5-minute cron a reclaim that _cannot_ help simply repeated forever. After a reclaim that runs and leaves the gate unhealthy, `dispatch_media_pipeline` now sits out `media_render_reclaim_cooldown_minutes` (default `30`) and emits a `vram_reclaim_ineffective` finding. A reclaim that works clears the marker immediately.

**Observed 2026-07-27** (the regression these guards close): image-gen hard-unloaded every 5 minutes for 2+ hours, each exit logging `vram_used=0 MB`, each freeing nothing, each opening a cold-start window — while the re-probe kept failing and the render never ran. Every one of those exits was pure loss, paid for in downgraded article images.

### The gate holds no reservation — mid-flow choreography (2026-07-29, poindexter#907)

The preflight checks free VRAM **once**, before the flow starts, and nothing keeps that VRAM reserved for the minutes a render takes. So the gate can pass honestly and the render still lose the card to a concurrent consumer.

Measured on the operator box, 2026-07-29:

| time        | event                                                               |
| ----------- | ------------------------------------------------------------------- |
| 05:03:45    | gate passes — **29.4 GB free** on GPU0, render dispatches           |
| 05:03–05:07 | **image-gen loads 25.1 GB** to illustrate the _next_ article        |
| 05:06:36    | wan OOMs — **98 MB free**, its own process holding just **1.82 GB** |
|             | → `hero_render_fallback`; the clip ships as a Ken-Burns still       |

wan was **crowded out, not too big** — note it held only 1.82 GB of its own at the moment it failed. This is why no `media_render_min_free_vram_gb` value fixes it: the crowding happens _after_ the check, so raising the bar just admits fewer renders that fail the same way. 18 `hero_render_fallback` findings trace to this.

Two fixes, matching the two defects in the issue:

1. **Clear the card at the point of use.** `shot_list_renderer._clear_image_gen_for_hero` hard-unloads image-gen immediately before each wan load (`video_hero_unload_image_gen`, default on). Best-effort — a failed reclaim must not turn a _possible_ render into a _certain_ skip. Cheap to repeat: once image-gen holds nothing the server declines the exit (`nothing_to_reclaim`), so only the first call of a run actually pays.
2. **Stop a failed load from latching.** `wan-server._ensure_pipeline_loaded` now releases the partial load (`_release_partial_load` — an OOM part-way through `.to("cuda")` used to strand ~14.9 GB with no handle) and only latches `degraded` for _persistent_ causes. An OOM is a statement about the card at that moment, not about the model, so it stays retryable instead of 503-ing every later request until a container restart.

### The finding carries the server's own reason (poindexter#996, 2026-08-07)

Every failure above surfaces as a `hero_render_fallback` finding — and until this fix all of them said the same thing:

> `wan provider returned no result — check wan-server logs/health`

That is the least useful true statement available. It points at a server that had usually answered, and answered precisely: `500 OutOfMemoryError: CUDA out of memory. Tried to allocate 1.48 GiB. GPU 0 has a total capacity of 31.34 GiB of which 1.47 GiB is free.` And because wan hard-exits after an OOM (and after a reclaim), the container that produced the miss is normally gone by the time anyone reads the finding — the logs it directs you to no longer exist.

The plumbing for this was already correct end to end: `_emit_hero_fallback_finding` takes a `reason` and puts it in `extra.reason`, and `_render_generative_clip` returns `(ok, reason)`. The break was one layer down — `wan2_1._generate_to_path` returned a bare `bool`, logging the status and body at ERROR level and discarding both.

Now:

- `_generate_to_path` returns `(ok, reason)`, unwrapping FastAPI's `{"detail": …}` so the reason reads as the exception the server actually hit.
- `Wan21Provider.last_error` carries it out of `fetch()`. The `VideoProvider` contract is unchanged — `fetch` still returns `[]` — so no other consumer is affected. It is reset at the top of every `fetch`, so a reused instance can never report a stale reason.
- `_render_generative_clip` prefers it; the old generic string survives only as the last resort for a provider that somehow reported nothing (an unlabelled miss being worse than a vague one).

The reason is capped at `_MAX_REASON_CHARS` (400) — deliberately more generous than the 200-char log clip, because the _tail_ of a CUDA OOM message ("Tried to allocate 1.48 GiB … 1.47 GiB is free") is its diagnostic half.

### Settings (`settings_defaults.py`)

| Key                                     | Default | Meaning                                                                                                                  |
| --------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------ |
| `media_render_reclaim_enabled`          | `true`  | Master switch for the reclaim-then-reprobe attempt.                                                                      |
| `media_render_reclaim_settle_seconds`   | `8`     | Delay between the reclaim and the re-probe, so Prometheus has re-scraped.                                                |
| `media_render_reclaim_cooldown_minutes` | `30`    | Pause after a reclaim that left the gate unhealthy. `0` restores the old every-cycle behaviour.                          |
| `image_gen_hard_unload_min_reserved_mb` | `512`   | Reserved-VRAM floor below which image-gen refuses a hard unload (nothing worth the exit).                                |
| `video_hero_unload_image_gen`           | `true`  | Hard-unload image-gen immediately before each wan hero load (#907). Off = skip the cold reload on a card that fits both. |
| `video_hero_unload_settle_seconds`      | `3`     | Pause after that unload so the CUDA context returns to the host before wan asks for it.                                  |

### The un-claim is bounded (poindexter#995, 2026-08-07)

When a `media_pipeline` run raises **and** the post-failure probe is unhealthy, `dispatch_media_pipeline` un-claims the piece (`media_pipeline_dispatched_at` back to `NULL`) instead of leaving it for the watchdog. That is deliberate: an outage fast-fail must never consume one of the task's bounded `media_pipeline_redispatch_count` attempts — six posts wedged permanently at that cap before the health gate existed.

The gap that made it a **loop**: the post-failure probe cannot distinguish _"infra died independently"_ from _"this render's own VRAM footprint is why the probe fails."_ wan holds 23–27 GB mid-render, so any failure leaves the card under `media_render_min_free_vram_gb` and reads as an outage. A piece that fails **because of itself** was therefore re-claimed for free, forever, on a 5-minute cron. Task `8faf3617` (published 2026-07-17, no `video_url`) rode that loop for weeks — three full ~15-minute GPU renders a day, **40 of 147 findings in one 24h window (27%)**, with `media_pipeline_redispatch_count` still reading `0` because no capped path ever ran.

So the un-claim now carries its own budget, `media_pipeline_unclaim_max`, tracked on `pipeline_tasks.media_pipeline_unclaim_count`:

- The un-claim UPDATE is guarded (`AND media_pipeline_unclaim_count < $2`) and bumps the counter atomically. A 0-row result means the budget is spent.
- **Spent → the marker stays set.** The piece stops re-rendering and a `media_unclaim_budget_exhausted` finding fires, because "stopped looping" must not be indistinguishable from "silently wedged".
- The counter **resets on any successful dispatch**, and on either `media_reconciliation` re-arm path (`_CLEAR_MARKER_SQL` / `_CAP_RESET_SQL`) — those are deliberately-authorised fresh attempts and should start with a full budget. So the ceiling caps _consecutive self-inflicted_ retries; it is not a lifetime quota, and a task that fails once a month never accumulates its way to it.

It is a **separate** budget from `media_pipeline_redispatch_max` on purpose. Sharing one would re-create the 2026-07-03 wedge, where a genuine multi-hour outage burns every in-flight piece's cap in minutes.

The full escalation ladder is now finite at every rung: **N free outage retries → the bounded watchdog re-dispatches → the 24h cap-reset self-heal.**

| Key                          | Default | Meaning                                                                                                             |
| ---------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------- |
| `media_pipeline_unclaim_max` | `3`     | Free outage retries per task before the dispatch marker is left set. `0` disables the un-claim (watchdog owns all). |

## Idle-only WSL/Docker reset (PR 2, host-side — build shipped, registration deferred)

The ~8.6 GB `vmwp`/`vmmemWSL` retention only returns to the host on `wsl --shutdown` + a Docker Desktop restart — a container restart doesn't touch it, and `wsl --shutdown` alone breaks GPU passthrough until Docker restarts too. The containerized worker runs _inside_ WSL and cannot reset it; this has to be a Windows host task.

`scripts/idle-wsl-gpu-reset.ps1` acts only when **all** hold: the user has been idle ≥ `idle_wsl_reset_min_idle_minutes` minutes (`GetLastInputInfo`), no `pipeline_tasks` are `in_progress`/`claimed` and no media dispatch is in flight, render-GPU free VRAM is below `idle_wsl_reset_trigger_free_vram_gb` AND GPU utilization is low (not gaming), and the last reset was ≥ `idle_wsl_reset_cooldown_hours` ago. `-DryRun` evaluates every condition and prints the decision without resetting anything.

`scripts/register-idle-wsl-reset.ps1` registers the Windows Scheduled Task — **shipped but not yet run against this machine.** Per `feedback_rebuild_authority` / the explicit-permission tier for persistent host configuration, actually registering the task (even disabled) and running the first live reset are held for a session with Matt at the keyboard: `-DryRun` gets validated against live state first, then the task registers **disabled**, then one **supervised** live reset confirms the stack bounces cleanly and self-heals before it's ever enabled to run unattended. Runbook: [`docs/operations/idle-wsl-gpu-reset.md`](../operations/idle-wsl-gpu-reset.md).

## What this does and doesn't do

- **Does:** guarantee a video render never oversubscribes the display GPU → **no more desktop freezes.** Actively reclaims the ~7 GB image-gen slice before giving up on a cycle. Bounds the outage un-claim so a piece that fails on its own footprint stops re-rendering instead of looping on the 5-minute cron.
- **Doesn't (yet):** touch the ~8.6 GB WSL2 retention — that needs the idle-only host reset, which is built but not yet enabled on this machine (supervised activation pending).
