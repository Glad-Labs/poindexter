# Media-gated publish: wire the dormant per-medium gate engine into the live flow

**Status:** Draft for review · **Date:** 2026-05-31 · **Refs:** Glad-Labs/poindexter#24 (two-phase approval), #338 (gate-system polish)

## Problem

The operator wants the content lifecycle to be:

> **approve (text + images) → media generates → operator reviews media → publish (text + media together)**

Today it's effectively the inverse. Approving a post moves it to `approved` (it parks — auto-publish is off), and only a _separate_ publish step (manual `tasks publish` / `scheduled_publisher`) takes it live. Media (podcast/video) then generates **after** publish (post-publish hooks in `publish_service.py` + the 4h `backfill_*` jobs), and the media-review gate (`media_approvals` / `is_approved()`) currently gates only **distribution** (RSS/YouTube), not publish. So the post goes live _before_ its media exists or is reviewed.

## Key finding: the engine already exists — it's dormant

`services/gates/post_approval_gates.py` is a complete **per-medium approval gate state machine** whose canonical order is exactly the desired flow:

```
topic → draft → podcast → video → short → final
```

- `MEDIUM_GATE_NAMES = (podcast, video, short)` — inserted only when that medium is in `posts.media_to_generate`.
- `final` is "the post-everything-generated checkpoint before distribution."
- States: `pending → approve → approved`, plus `reject`, `revise` (regenerate, resets to `pending`), `skipped`.
- `advance_workflow` returns a structured "what to do next" descriptor (e.g. "run media-gen for video"); the **caller** wires it to the real subsystem.

**But it's vestigial.** `post_approval_gates` holds 143 rows, all `gate_name='final'`, all `state='rejected'`, all stamped `2026-05-02 02:48` (one batch). No medium/draft/topic gates were ever created in production; the 3 most-recently-approved posts have **zero** gate rows. The engine's original driver was `services/idle_worker.py`, which is **retired** (its methods were migrated to the plugin Job system; Prefect replaced its loop 2026-05-16). So the gate workflow has no driver.

**What IS wired (consumer side):** `publish_service.py` already defers on gates:

- `_post_has_pending_gates(pool, post_id)` → True iff the post has any `state='pending'` gate row.
- When True, the publish path **defers** distribution + media-gen; when False (today's reality — no gate rows), everything fires immediately (back-compat preserved).
- A `_gates_block_distribution` guard wraps the distribution side-effects.

So **the engine + the distribution-gating consumer exist; the missing half is the producer/driver** that creates the gate sequence on approval and advances it (media-gen per medium → final).

## Design

Wire the producer/driver and split media-generation from publish.

### 1. Resolve `media_to_generate` at draft-approval (was: publish)

The gate sequence needs to know which medium gates to create at approval time. Today `media_to_generate` is populated **at publish** from `niches.default_media_to_generate` (migration `20260519_134736`). Move that resolution to the draft-approval handler so it's known when gates are created. A post whose niche opts into no media skips straight to `final` (or publishes immediately — see Decision D2).

### 2. On draft-approval: create the gate sequence + trigger media-gen

In the draft-approval handler (the `tasks approve` / `post approve` path → approval route):

- Create the gate rows in workflow order: `draft` (immediately `approved`, since this _is_ the draft approval) → one gate per medium in `media_to_generate` → `final`.
- **Trigger media generation now** (per medium), pre-publish. This is the crucial split: media-gen must run so the artifact is reviewable; it must NOT be deferred by `_post_has_pending_gates`. Reuse the existing `generate_podcast_episode` / `generate_video_episode` / shot-list path — just fire them from the approval/advance path instead of the post-publish hook.
- As each medium finishes, its gate becomes `pending` (operator review). The brain probes that already ship (`gate_pending_summary_probe`, `gate_auto_expire_probe`, `notify_gate_pending`) handle notification + batching + expiry.

### 3. Operator reviews each medium

Via the surfaces that already exist: `poindexter media pending` / `media approve <post> <medium>` / `media reject`, and the gate CLI on `poindexter post`. Per-niche auto-approve (`niche.<slug>.media.<medium>.auto_approve`) still applies — an auto-approved medium's gate is created already `approved`.

### 4. `final` gate → publish + distribute

When every medium gate is `approved` (or `skipped`), `advance_workflow` reaches `final`. The operator's `final` approval (or auto-advance if no human-final-gate is configured) triggers the **actual publish** (text + media live together) and the distribution side-effects (RSS / social / dev.to). This is the "publish-last" the operator wants. The distribution side-effects move out of the unconditional post-publish hook and behind the `final` gate — the `_gates_block_distribution` guard already exists; this makes it real by giving it gates to enforce.

### 5. Split media-gen from publish+distribution in `publish_service`

Today both media-gen and distribution are post-publish hooks gated together by `_post_has_pending_gates`. Restructure so:

- **media-gen** fires post-_approval_, pre-publish (driven by the gate workflow, step 2).
- **publish + distribution** fire post-_final-gate_ (step 4).
  The `_should_run_post_publish_hooks()` (worker-mode) guard and back-compat for gate-less posts stay intact.

### Driver location

The retired `idle_worker` tick is gone. The gate-workflow advancement (calling `advance_workflow` and acting on its descriptor) should live in the live dispatch path — either a small Prefect-flow hook fired on gate-state changes, or a lightweight plugin Job that polls `post_approval_gates` for actionable rows each tick (mirrors how the other `idle_worker` methods became Jobs). Decision D3.

## Reuse vs. build

**Reuse (already exists):** the gate engine + state machine; `media_approvals` + `poindexter media` CLI; per-niche auto-approve; brain notify/summary/auto-expire probes; `_post_has_pending_gates` + `_gates_block_distribution` consumer; `generate_podcast_episode`/`generate_video_episode`.

**Build:** (a) `media_to_generate` resolution at approval; (b) gate-sequence creation on draft-approval; (c) media-gen trigger from the approval/advance path (not post-publish); (d) the media-gen ↔ publish/distribution split in `publish_service`; (e) the workflow driver (Prefect hook or Job); (f) `final`-gate → publish trigger.

## Decisions to lock

- **D1 — Rejection handling:** default to **revise → regenerate** (the engine already has a `revising` state that resets the gate to `pending` after regen). Operator can still hard-reject (post stays unpublished). _(Operator deferred this earlier; this is the proposed default.)_
- **D2 — Text-only posts:** posts whose niche opts into no media skip the medium gates and publish at `final` immediately (no waiting on nothing).
- **D3 — Driver:** Prefect-flow hook vs. polling Job — pick during implementation; lean Job (consistent with the idle_worker→Job migration pattern).
- **D4 — Cost-estimate pre-gen gate (#338):** a preview gate _before_ media-gen so the operator can reject an expensive plan unpaid. **Defer** to a follow-up — not required for the core flow.

## Data flow

```
draft approved
  → resolve media_to_generate (niche default)
  → create gates: draft(approved) · podcast/video/short(pending-after-gen) · final(pending)
  → trigger media-gen per medium  ──┐
                                     ↓ (artifact ready)
  → medium gate = pending → operator reviews (poindexter media …)
  → all media approved/skipped → final gate
  → final approved → publish (text+media) + distribute (RSS/social/devto)
```

## Error handling

- Media-gen failure per medium → `media_generation_failed` gate (already a canonical gate name) after the retry budget; operator decides revise vs. publish-without.
- Back-compat: posts with no gate rows behave exactly as today (publish + distribute immediately) — `_post_has_pending_gates` returns False.
- DB-probe failures err toward publishing (existing defensive behavior) — but the new producer must avoid creating partial gate sequences (create all-or-nothing in one transaction).

## Testing

- Unit: gate-sequence creation for each `media_to_generate` combination (incl. empty → text-only fast path); `advance_workflow` transitions; the media-gen ↔ publish split (media generated but post NOT published until `final`).
- Integration: a `canonical_blog` task with `media_to_generate=[podcast,video]` does not publish until both medium gates + `final` are approved; a text-only post publishes at `final` immediately; auto-approve niche fast-path.
- Regression: gate-less legacy posts still publish + distribute on approval (back-compat).

## Out of scope

Cost-estimate pre-gen gate (#338, D4); web admin UI for gate review (#338); per-target distribution sub-gating (#338); full task decomposition into child `podcast_generation`/`video_generation` tasks (the operator's longer-term "ultimate direction" — this design is the interim that the decomposition can later supersede, per #24's design notes).
