# Component-scoped regen gate (preview gate)

**Status:** Design — approved in principle (operator, 2026-06-21), pending spec review.
**Scope:** `canonical_blog` content pipeline. One operator review point, richer regen actions.
**Related, out of scope:** the automatic vision-QA gate already exists (`qa.vision` — image relevance + rendered-preview checks); it is default-off, a separate settings change, not rebuilt here.

## Problem

Today the operator reviews a post once, at `awaiting_approval`, after the whole
graph has run. The only actions are **approve** (→ publish-stage) and **reject**.
A rejection (`approval_service` → `rejection_handlers`) enqueues a **full draft
regen** with the reason as steering — there is no way to keep one part and
regenerate the other.

The common failure (e.g. the 2026-06-21 keyboard post) is "**text is fine, an
image is bad**." Today that forces a full redo, throwing away good text. The
operator wants surgical regen: keep the text, regenerate the images — and the
inverse.

## The constraint that shapes the design: images are derived from text

`image.decision` plans every image from the **final sections** of the text.
Text does not depend on images; images depend on text. So the action matrix is
asymmetric:

| Operator intent             | Coherent? | Maps to                                                         |
| --------------------------- | --------- | --------------------------------------------------------------- |
| Keep text, **regen images** | ✅ clean  | re-run image block from same text                               |
| **Regen text**              | ✅ clean  | re-run writer block; images **auto-refresh** (cascade)          |
| Keep **images**, regen text | ⚠️ stale  | inline images were planned for the _old_ sections — not offered |
| Keep both                   | —         | = approve                                                       |

So we offer three operator actions — **approve / regen_images / regen_text /
reject** — and _not_ a literal "keep images while changing text," because that
is incoherent for inline images. "regen_both" collapses into `regen_text`
(text regen already cascades through images), so it is not a separate action.

## Design

Insert one in-graph gate, `preview_gate` (an `atoms.approval_gate` node), into the
finalize block **after the draft is persisted** (`content.persist_task` →
`content.record_pipeline_version`) and **before** the terminal publish decision
(`content.evaluate_auto_publish`). The draft is persisted and the task is set
`awaiting_approval` exactly as today, so the operator reviews the **real
persisted draft** through the existing surface — the review UX does not change.
When the gate is enabled it _is_ the approval, so on approve the run proceeds to
publish-stage and the auto-publish-threshold branch in `evaluate_auto_publish`
is subsumed (no double gate).
The gate then `interrupt()`s: LangGraph durably checkpoints the whole graph
(Postgres, keyed on `task_id`) and releases the worker. The graph stays paused
until the operator decides; the decision is the resume value.

```
… → content.persist_task (status=awaiting_approval)
      → preview_gate (interrupt, wait for operator)
            ├─ approve      → content.evaluate_auto_publish → publish-stage
            ├─ regen_images → [loop] content.plan_image_markers   (image block)
            ├─ regen_text   → [loop] content.generate_draft       (writer block)
            └─ reject       → _halt   (existing reject behaviour)
```

The two regen edges reuse the existing bounded **loop-edge** pattern (the
`qa.rewrite` rescue cycle: a `"loop": true` edge exempt from DAG validation):

- `regen_images`: `preview_gate → content.plan_image_markers`. Re-runs the image
  block → image-dependent QA → SEO → … → back to `preview_gate`. Text untouched.
- `regen_text`: `preview_gate → content.generate_draft`. Re-runs the writer block;
  the existing forward edges carry it through the image block (fresh images) and
  all QA again — the cascade the operator explicitly wants.

### Operator surface (CLI-first, then MCP)

Extend the approval actions from `{approve, reject}` to add `regen_images` /
`regen_text`. Each writes a `pipeline_gate_history` row (new `event_kind`s) and
resumes the graph with a matching `Command(resume=...)`.

- **CLI:** `poindexter pipeline regen <task_id> --images|--text [--steering "…"]`
- **MCP:** a `regen_post(task_id, component, steering?)` tool (phone surface).

`--steering` is optional free text threaded into the writer / image-decision
prompt as guidance ("make the featured image less busy"), mirroring how the
current reject reason already steers the draft regen.

### Bounding

HITL means the operator is the loop bound (no runaway). For observability and a
sanity cap, each component carries a durable counter on `pipeline_tasks`
(`regen_images_attempts` / `regen_text_attempts`) compared against
`app_settings.regen_images_max_attempts` (default `3`) /
`regen_text_max_attempts` (default `2`) — DB-tunable, defaults seeded in
`settings_defaults.py`. On cap the gate forces an approve-or-reject decision
rather than another loop.

### Rollout (enabled by default)

`preview_gate` is **enabled by default** (`pipeline_gate_preview_gate=true`,
seeded in `settings_defaults.py`) — the operator reviews every post anyway
(`auto_publish_threshold=0`), so the gate becomes the review mechanism rather
than the terminal `awaiting_approval` hold. The flag still exists, so it can be
turned **off** to fall back to today's terminal review (passthrough →
`evaluate_auto_publish` → `awaiting_approval`, approve/reject only) with no code
deploy.

**Because it is on by default it changes a battle-tested flow on merge**, so the
flag is only flipped to `true` on prod _after_ end-to-end verification: a real
`canonical_blog` run must pause at `preview_gate`, surface the draft for review,
and resume cleanly on approve / each regen action. Until that passes, develop
behind `false` and treat the default flip as the last implementation step.

## Data flow / state

- Decision is carried by `pipeline_gate_history` (existing typed-history table)
  plus the LangGraph resume value — same mechanism `approval_gate` already uses.
- Stale-approval freshness (the `approved_at_retry_count` check in
  `approval_gate._gate_decision`) extends to the regen events: a regen bumps the
  attempt, so a prior approval cannot auto-pass the regenerated content.
- `regen_*` is recorded as a router/variant outcome signal (like reject today,
  via `_record_router_outcome`) so the variant weights learn from partial regen.

## Error handling

- No DB pool / `pause_at_gate` failure → `_halt` with reason (existing
  `approval_gate` behaviour; fail-loud, no silent default).
- Loop-cap reached → operator-facing message; gate holds for approve/reject.
- Unknown `component` on the CLI/MCP surface → reject the request loudly.

## Testing (TDD)

- `preview_gate` disabled → passthrough (graph unchanged). _(regression guard)_
- `regen_images` resume routes to the image block and **not** the writer block;
  text state is byte-identical across the loop.
- `regen_text` resume routes to the writer block and the image block re-runs
  after it (cascade asserted).
- Attempt counters increment; at cap the loop edge is refused.
- CLI/MCP `regen` writes the correct `pipeline_gate_history` event_kind and
  resume payload; unknown component is rejected.
- Stale prior-approval does not auto-pass post-regen content.

## Out of scope (separate work)

- **The vision QA gate already exists — `qa.vision`** (image-relevance +
  rendered-preview-screenshot checks). It is **not** rebuilt here. It currently
  defaults off (`qa_vision_check_enabled=false`, `qa_preview_screenshot_enabled
=false`, and the `vision_gate` qa*gate must be enabled), which is why an
  irrelevant image can still ship. Turning it on + enforcing it is a separate
  **settings change** (DB-first, no build), deferred per operator. Note its
  relevance check catches \_off-topic* images, not _aesthetically weak_ ones
  (e.g. a relevant-but-boring featured image) — that quality judgment is a
  distinct, still-open gap.
- **"Keep images, regen text"** as a literal action — incoherent for inline
  images (see the constraint table); `regen_text` refreshing images is the
  honest version.
