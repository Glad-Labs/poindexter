# Final-publish gate wiring + honest `gates list` — design

**Date:** 2026-07-09
**Status:** Approved (design), pending spec review
**Branch:** `claude/hitl-gating-config-f58605`

## Problem

`poindexter gates list` is misleading in three ways, surfaced when the operator
compared it against what actually gates publication:

```
GATE                         STATE      PENDING
draft_gate                   disabled   0
final_publish_approval       disabled   0
preview_gate                 disabled   0
seo_refresh_gate             enabled    1
```

1. **It omits the primary HITL mechanism.** The gate actually stopping every
   post is the terminal `awaiting_approval` status (per-post sign-off), enforced
   in `post_pipeline_actions._maybe_auto_publish` because
   `auto_publish_threshold=0` and `require_human_approval=true`. It never appears
   in this list.
2. **`final_publish_approval` is shown as a real toggle, but its trigger was
   never wired.** The service layer, CLI, rejection handler, setting, and even
   the `posts` schema columns/index all exist — but nothing ever sets
   `posts.awaiting_gate='final_publish_approval'`, so flipping the setting on does
   nothing today.
3. **Code-defined gates with no setting row are invisible.** `topic_decision`
   (`topic_proposal_service.py`) is a real gate but never shows, because
   `list_gates` only surfaces gates that have a `pipeline_gate_*` row or a live
   paused task.

## Verified publish lifecycle (context)

| #   | Transition                          | Trigger                                                                                                | Human?                     |
| --- | ----------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------- |
| 1   | pipeline → `awaiting_approval`      | `content.evaluate_auto_publish` forces it (`auto_publish_threshold=0` + `require_human_approval=true`) | STOP 1 — operator sign-off |
| 2   | approve → `posts.status='approved'` | `/approve` → `publish_post_from_task(stage_only=True)` — staged, not live                              | STOP 2 — staged            |
| 3   | `approved → published`              | explicit `/publish` or `/go-live`                                                                      | explicit human action      |

The **only** unattended (no-human) promotion is `scheduled_publisher`:
`scheduled → published` when `published_at <= NOW()`. A post only enters
`scheduled` via an explicit operator schedule. `final_publish_approval` was
designed to veto exactly this timer transition.

**Decision (operator):** wire the gate for the **scheduled-timer path only**.
The per-niche auto-publish path (`auto_publish_task`, e.g. `dev_diary`) is out of
scope for this change.

## Approach

**Contract-completion (chosen).** `posts_approval_service` already provides
`pause_post_at_gate` / `approve_publish` / `reject_publish` /
`list_pending_publish` and documents that "the publisher's WHERE clause filters
out anything with `awaiting_gate` set" — but the publisher never implemented its
half. We finish that contract and make `gates list` reflect reality. No schema
migration: `posts.awaiting_gate` / `gate_artifact` / `gate_paused_at` and
`idx_posts_awaiting_gate` already exist in the baseline.

Rejected: spec-derived gate discovery (overkill for ~5 gates; can't see CLI/publish
gates absent from any graph) and a declarative `gates` table (premature schema
change; enable-state already lives in `app_settings.pipeline_gate_*`).

## Part 1 — Wire `final_publish_approval` into `scheduled_publisher`

Two changes to `services/scheduled_publisher.py`, both safe when the gate is off:

1. **Correctness guard (unconditional).** Add `AND awaiting_gate IS NULL` to the
   promote `UPDATE ... WHERE status='scheduled' AND published_at <= NOW()`. This
   makes the publisher honor the "halted" signal `posts_approval_service` already
   assumes. A parked post is never auto-promoted. Independent of this gate.
2. **Park-when-enabled (conditional).** At the top of each tick, when
   `approval_service.is_gate_enabled('final_publish_approval', site_config)`:
   - `SELECT id, slug, title, published_at FROM posts WHERE status='scheduled'
AND published_at <= NOW() AND awaiting_gate IS NULL`
   - For each row, `await posts_approval_service.pause_post_at_gate(post_id=...,
gate_name=FINAL_PUBLISH_GATE, artifact={slug, title, permalink?},
site_config=..., pool=..., notify=True)`. This sets `awaiting_gate`, fires
     the operator notification, and writes the `approval_gate_paused` audit row.
   - The subsequent promote `UPDATE` (now with the `awaiting_gate IS NULL` guard)
     finds them parked and skips them.

**Resume (already built):** operator runs `poindexter gates approve-publish <id>`
→ `approve_publish` clears `awaiting_gate`, leaves `status='scheduled'` → the next
60s tick promotes normally. `reject_publish` moves the post out of `scheduled`
(default `rejected`, per-gate override via
`approval_gate_final_publish_approval_reject_status`) and dispatches the
`final_publish_approval` rejection handler (media regen).

**Ordering / concurrency.** Single worker runs `scheduled_publisher`. The pause
step commits `awaiting_gate` before the promote `UPDATE` runs in the same tick, and
the `UPDATE` re-checks `awaiting_gate IS NULL`, so a due post cannot be both parked
and promoted.

**Artifact.** Minimal operator-review payload: `slug`, `title`, and a `permalink`
built from the public base URL in `site_config` when available (the notify helper
reads `artifact.get('preview_url') or artifact.get('permalink')`).

**Edge case (documented).** If the gate is switched off while posts are parked,
they remain parked (the guard skips them) until the operator clears them via
approve/reject-publish. Parked is an explicit halt, not a timer state. The honest
`gates list` surfaces the parked count so they are never invisible.

## Part 2 — Make `gates list` honest

### Gate catalog

A hand-maintained `GATE_CATALOG` in `services/gate_machinery.py` (the shared
gate-infra module both approval services build on). One entry per known gate:

| gate                     | mechanism       | wired_into            | default |
| ------------------------ | --------------- | --------------------- | ------- |
| `draft_gate`             | graph-node      | canonical_blog        | off     |
| `preview_gate`           | graph-node      | canonical_blog        | off     |
| `seo_refresh_gate`       | graph-node      | seo_refresh           | on      |
| `topic_decision`         | imperative-hold | topic proposals (CLI) | off     |
| `final_publish_approval` | imperative-hold | scheduled_publisher   | off     |

**Mechanism follows execution context, not preference.** A gate is a
`graph-node` (LangGraph `interrupt()` atom) only when the transition it guards
happens _inside a live graph run_ — those pause + checkpoint the running graph
and resume via `pipeline resume`. `imperative-hold` gates (`pause_at_gate` /
`pause_post_at_gate` writing the `awaiting_gate` columns) guard transitions that
happen _outside_ any live graph: `topic_decision` holds a task before the flow
claims it (pre-graph); `final_publish_approval` holds a post before the
`scheduled_publisher` timer promotes it (post-graph). This is precisely why
`final_publish_approval` cannot be a graph node — there is no running graph to
`interrupt()` when the timer fires. The imperative gates bookend the graph; the
graph-node gates live inside it.

`awaiting_approval` is represented separately as the always-on **default** gate,
not a catalog toggle. A comment instructs: new gates get a catalog entry so
`gates list` stays honest.

### `list_gates` changes (`services/approval_service.py`)

- Merge `GATE_CATALOG` (so code-defined gates like `topic_decision` appear even
  with no setting row) with the existing `pipeline_gate_*` settings scan.
- Count pending from **both** `pipeline_tasks.awaiting_gate` **and**
  `posts.awaiting_gate` (final_publish_approval parks on `posts`; today's list
  counts only `pipeline_tasks` and would under-report).
- Emit each row with added `mechanism` and `wired_into` fields alongside the
  existing `gate_name` / `enabled` / `pending_count` (superset — backcompat
  preserved).
- Add a synthetic default-gate entry: `awaiting_approval`, `mechanism='default'`,
  pending = `COUNT(*) FROM pipeline_tasks WHERE status='awaiting_approval'`, plus
  the auto-publish posture (global `auto_publish_threshold`,
  `require_human_approval`, and armed niches — a scan of app_settings for
  `*_auto_publish_threshold > 0 AND *_auto_publish_dry_run='false'`).

### CLI renderer (`poindexter/cli/approval.py`)

```
DEFAULT PUBLISH GATE — every post requires per-post sign-off
  awaiting_approval        always-on    2 pending
  auto-publish: OFF globally (threshold 0, require_human_approval=true) · armed niches: dev_diary

CONFIGURABLE GATES
  GATE                    STATE       WIRED INTO            PENDING
  draft_gate              disabled    canonical_blog        0
  preview_gate            disabled    canonical_blog        0
  seo_refresh_gate        enabled     seo_refresh           1
  topic_decision          disabled    topic proposals       0
  final_publish_approval  disabled    scheduled_publisher   0
```

- `WIRED INTO` (where the gate fires) is the operator-facing column; the
  `graph-node` vs `imperative-hold` `mechanism` is carried in `--json`.
- State meaning is carried by the `enabled/disabled` text, never color alone
  (operator is colorblind).
- `--json` output is a superset of today's shape.

## Testing

- **Publisher (unit, extend `scheduled_publisher` tests):** gate-off promotes as
  today; gate-on parks due-and-unparked posts (calls `pause_post_at_gate`, does
  not promote); the `awaiting_gate IS NULL` guard excludes an already-parked post
  from promotion; approve-then-next-tick promotes.
- **`list_gates` (unit, extend `test_approval_service` / posts variant):** catalog
  gates with no setting row appear; pending counts union both tables; the
  default `awaiting_approval` row is present with the right count; `--json`
  retains `gate_name`/`enabled`/`pending_count`.
- **CLI (extend `test_approval` CLI tests):** grouped rendering (default section +
  table with WIRED INTO), colorblind-safe text states.

## Docs

- `docs/operations/cli-reference.md` — update the `gates` section (new columns,
  default-gate line, `final_publish_approval` now live on the scheduled path).
- HITL/anti-hallucination doc note: `final_publish_approval` gates the
  `scheduled → published` timer transition when enabled.

## Out of scope

- Gating the per-niche auto-publish path (`auto_publish_task` / `dev_diary`).
- Gating the manual `/publish` + `/go-live` paths (already human actions).
- A declarative `gates` DB table (catalog stays in code; can graduate later).
- Spec-derived gate discovery.

## No migration

`posts.awaiting_gate`, `posts.gate_artifact`, `posts.gate_paused_at`, and
`idx_posts_awaiting_gate` already exist in `0000_baseline.schema.sql`. No new
`app_settings` key is required — `pipeline_gate_final_publish_approval` is already
seeded (`off`).
