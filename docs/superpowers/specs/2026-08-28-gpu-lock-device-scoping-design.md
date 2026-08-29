# GPU lock device scoping — design

**Status:** proposed · **Date:** 2026-08-28 · **Supersedes nothing; extends**
[2026-07-26 GPU scheduler queue admission](2026-07-26-gpu-scheduler-queue-admission-design.md)

One `pg_advisory_lock(7_777_777_777)` currently serialises every GPU workload
in the stack. This proposes replacing it with a lock **scoped to the physical
devices a workload can actually contend for**, in a way that survives a change
of serving stack (Ollama → vLLM), a move to cloud, and a second node.

## 1. Why now

Three separate reasons, in ascending order of importance.

**a. QA judge rails are being skipped for no reason (the trigger).** The
judges are pinned to GPU 1 and renders run on GPU 0 — genuinely disjoint
hardware — but both take the same lock. Over 7 days that produced **238
`gpu_lock_timeout` findings at the 45 s admission budget** and **73
`qa_rail_degraded`**, concentrated in exactly the GPU-bound judges
(`deepeval_faithfulness` 24, `deepeval_g_eval` 13, ragas). The gate fails safe
(`None` + finding, never a fake score), so this erodes QA _coverage_ quietly.

**b. It is already wrong for more than one node (the latent bug).** The key is
a bare constant against a **shared** Postgres. Add a second worker node and
both serialise on it — the whole fleet gets one GPU's worth of throughput.
This is true today, before any change here.

**c. The current shape is not portable.** Anything keyed on Ollama endpoints
or `ollama_gpu_indexes` gets rewritten when the serving stack changes.

## 2. The invariant

> Two workloads must be serialised **iff** the sets of physical devices they
> may occupy intersect.

Everything below is mechanism for computing that. VRAM contention is a property
of a _card_, not of what is serving on it — which is what makes the design
survive a backend swap.

### Logical vs physical locks — the trap this creates

The codebase already has two sibling advisory-lock namespaces, both documented
as built for "the multi-worker SaaS future":

| namespace                 | key             | scope that is CORRECT                                     |
| ------------------------- | --------------- | --------------------------------------------------------- |
| `_SOCIAL_POST_LOCK_NS`    | `0x50AC`        | **cluster-wide** — one node posts a given draft, globally |
| `_MEDIA_DISPATCH_LOCK_NS` | `0x4D44`        | **cluster-wide** — one node dispatches a given render     |
| GPU session               | `7_777_777_777` | **per-node** — the card exists on one machine             |

Same primitive, opposite semantics, and nothing in the code marks the
difference. That is precisely how someone adds a node and wonders why
throughput did not scale. Any new key must state which kind it is.

## 3. Design

### 3.1 Scope = a set of device identities

```python
LockScope = frozenset[DeviceId]        # empty set == no GPU, no lock
DeviceId  = tuple[str, int]            # (node_id, gpu_index)
```

`node_id` defaults to the hostname. Single node today ⇒ a constant prefix ⇒
byte-identical behaviour; multi-node later ⇒ correct with no rewrite.

**The empty set is first-class, not an edge case.** A managed inference API
(Bedrock, Together), serverless GPU (Modal/Replicate), or a CPU-only judge all
resolve to `frozenset()` and take **no lock at all**. That is what makes the
cloud cases fall out for free rather than needing a code path.

### 3.2 Role → device set, declared in one place

```jsonc
// app_settings: gpu_lock_scopes
{
  "render": [0], // comfyui / wan / image-gen
  "qa_judge": [1], // whatever serves the judge — Ollama now, vLLM later
  "llm_primary": [0, 1], // unpinned, so it overlaps everything
}
```

Roles are **stable across serving stacks**. Switching to vLLM edits one entry.
Moving the judge to a managed API sets it to `[]`.

### 3.3 Scope is derived, so no call site changes

There are **18 `async with gpu.lock(...)` call sites** across exactly three
owners — `ollama` (13), `image_gen` (4), `video` (1). **None need to change:**
the scope is derived from the `owner` and `model` arguments callers already
pass. (Counted as real call sites; a naive grep also matches ~50 docstring and
comment mentions of `gpu.lock("ollama")`, which is what makes this look larger
than it is.)

```
owner=image_gen | video          -> role "render"
owner=ollama, model has a
  per-model endpoint override    -> role "qa_judge"
owner=ollama, anything else      -> role "llm_primary"
```

The override lookup reuses the existing LiteLLM `model_api_base_overrides`
map, which is already the single source of truth for "this model is served
somewhere else".

### 3.4 Overlap ⇒ serialise, computed not declared

With today's config `llm_primary` is `[0,1]`, which intersects both `[0]` and
`[1]`, so **everything still serialises — behaviour is unchanged on merge.**
Pinning ollama-primary to GPU 0 later changes one entry to `[0]`, and the
judge/render concurrency appears with **no code change**.

The same mechanism gives the safety property that motivated this: unpin GPU 1
and `qa_judge` widens to `[0,1]`, overlapping everything, and serialisation
returns automatically.

### 3.5 Key derivation

```python
key(node_id, gpu_index) = GPU_ADVISORY_LOCK_KEY + 1 + stable_hash(node_id, gpu_index)
```

`GPU_ADVISORY_LOCK_KEY` (`7_777_777_777`) is retained as the **whole-GPU**
key used when scoping is disabled or a scope cannot be resolved. Derived keys
sit far from the sibling int32 namespaces (`0x50AC`, `0x4D44`), so no
collision — matching the existing "stable arbitrary, distinct from the others"
convention.

### 3.6 Acquisition algorithm

1. Resolve scope → sorted list of device keys (deterministic order).
2. Acquire **in-process** gates for each key **in ascending order** (deadlock-free).
3. Acquire **pg** advisory locks for the same keys, ascending, on the existing
   single dedicated connection (session-level locks stack fine).
4. On timeout or failure at step _n_: release the _n-1_ already held, in
   reverse order, before raising. Partial acquisition must never survive.
5. Release: pg first (closing the connection releases all session locks), then
   in-process gates in reverse order.

Empty scope ⇒ steps 2-5 are no-ops; the caller runs unserialised by design.

### 3.7 Fail closed, never open

If the device set cannot be resolved — unknown owner, malformed map, or the
declared card count disagrees with the nvidia-smi exporter — fall back to the
**single whole-GPU key**. Wrong-but-serialised costs throughput;
wrong-and-split costs a CUDA OOM. Emit a `gpu_lock_scope_unresolved` finding
so it is visible rather than silently conservative.

## 4. The contract has six consumers

This is the part most likely to bite: the key is **not** a scheduler private.

| consumer                             | uses it how                                                                                   | change needed                                                                         |
| ------------------------------------ | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `services/gpu_scheduler.py`          | acquires/releases                                                                             | the change itself                                                                     |
| `brain/health_probes.py`             | **takes** it (`pg_try_advisory_lock`) so the writer-model probe never loads ~19 GB mid-render | must take the _render_ scope keys, not the base                                       |
| `brain/sidecar_ram_watch.py`         | **reads** it as an idle gate                                                                  | must test "any GPU key held", or it reads idle mid-render and recycles a live sidecar |
| `brain/ollama_runner_ram_watch.py`   | **reads** it as an idle gate (#3441)                                                          | same as above — found UNPINNED by the Phase 1 ratchet, now pinned                     |
| `gpu_scheduler` status surface       | exposes `pg_advisory_lock_key`                                                                | becomes a list                                                                        |
| `social_drafts` / `dispatch_handles` | comment-only siblings                                                                         | none, but the logical/physical note belongs there                                     |

All three brain consumers **duplicate the key by value on purpose** — the brain runs
stdlib + asyncpg only and cannot import the worker package. So the contract
cannot be centralised into one importable constant; it has to be centralised
into one _documented_ derivation with a test that pins the values equal across
both trees.

## 5. Phases

**Phase 1 — centralise the contract (no behaviour change).** _Shipped in
stack#3463 — the ratchet found a sixth, unpinned consumer on its first run._
One owner for key derivation, a shared "is any GPU lock held" predicate, all
all six consumers routed through it, keys byte-identical to today. A test asserts
the brain-side duplicated values equal the worker-side ones. _This is the phase
that stops a future split silently breaking the probes, and is worth landing
even if the rest is deferred._

**Phase 2 — set-based locking, shipped inert.**
Scope resolution, ordered multi-key acquire/release, rollback. Ships with
`llm_primary: [0,1]`, so every scope still overlaps and behaviour is unchanged.
Soak on the real workload with the new code path exercised.

**Phase 3 — enable concurrency (config only).**
Pin ollama-primary to GPU 0 the same way `ollama-vision.sh` pins GPU 1 (resolve
the UUID, refuse to start unpinned), set `llm_primary: [0]`, and set
`ollama_gpu_indexes=0` so the #1016 VRAM fit gate inspects the cards primary can
actually use. Judge/render concurrency appears. **Reversible by reverting the
map entry.**

**Phase 4 — node scoping (only when a second node exists).**
`node_id` is in the key from Phase 2, so this is verification, not new code.

## 6. Test plan

The mutex is the highest-blast-radius file in the repo; a partial-acquire or
release-ordering bug deadlocks content generation. Minimum bar:

- **Disjoint scopes run concurrently** — two locks, no overlap, both proceed.
- **Overlapping scopes serialise** — `[1]` vs `[0,1]` must not both proceed.
- **Ordering is deterministic** — acquisition is ascending regardless of the
  order the scope was declared in (the deadlock proof).
- **Partial-acquire rollback** — fail the second of three keys; assert the
  first is released and no key is left held.
- **Empty scope takes no lock** — and does not block anything.
- **Unresolvable scope falls back to the whole-GPU key** and emits the finding.
- **Unpin restores serialisation** — widen `qa_judge` to `[0,1]`; assert it now
  serialises against `render`. _This is the regression test for the property
  that motivated the change._
- **Cross-tree key agreement** — brain duplicated values == worker values.
- **Reentrancy** — a nested `gpu.lock()` in the same scope stays a pass-through.

## 7. Risks

| risk                                          | mitigation                                                                                                                                                     |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deadlock from unordered multi-key acquire     | ascending-order acquisition, pinned by test                                                                                                                    |
| Partial acquisition on timeout                | explicit reverse-order rollback, pinned by test                                                                                                                |
| Split while cards actually overlap → CUDA OOM | fail-closed to the single key; Phase 3 gated on an _enforced_ pin, not a declared one                                                                          |
| Brain probes read a stale key                 | Phase 1 lands first, with the cross-tree test                                                                                                                  |
| GPU index reordering across boots             | exporter exposes index only (no UUID reachable from the worker container); validate declared count against the exporter at startup and fail closed on mismatch |

## 8. What would make us revert

Any of: a deadlock in the pipeline, a CUDA OOM attributable to two workloads on
one card, or `gpu_lock_scope_unresolved` firing steadily (meaning the map does
not describe reality). Phase 3 is a single config entry, so revert is a setting
change, not a deploy.

## 9. Open questions

1. **Is Phase 3's pin acceptable?** GPU 1 currently has ~3 GB free (the judge
   holds 21.3/24.6), so primary can barely use it — but pinning makes that
   explicit and forecloses spillover.
2. **Does `ollama_gpu_indexes=0` regress #1016?** That fix widened the VRAM fit
   gate because primary could place on either card. If primary is _enforced_ to
   GPU 0, narrowing the gate is correct, not a regression — but it must land
   together with the pin, never before it.
3. Node identity source — hostname vs an explicit `node_id` setting. Hostname
   is zero-config; an explicit setting survives container renames.
