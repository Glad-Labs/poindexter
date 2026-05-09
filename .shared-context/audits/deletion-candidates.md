# Deletion Candidates — running list

> Working doc. Both Matt and Claude append to this. Periodically we
> walk it together and decide each item's fate (delete, keep,
> reframe, or "first migrate X then delete").
>
> Started: 2026-05-09. Maintained by Claude during sessions; Matt
> appends inline as new candidates surface.

## How to use this doc

Add a candidate with this shape:

```
### <path or feature name>

- **Author / when:** Claude / 2026-05-09
- **Size:** ~N LOC, ~N% of <area>
- **Why removable:** <one-paragraph case>
- **Blocker (if any):** what has to land first
- **Confidence:** high / medium / low
- **Decision (filled in later):** keep / delete / reframe / migrate-first
```

Higher-confidence items at the top of each section. Items with
explicit blockers stay until the blocker lands.

---

## Confirmed dead — safe to delete

(Nothing here yet. Items move here from "Probably dead" once a
verification step has been done.)

---

## Probably dead — pending verification

### `services/jobs/backfill_podcasts.py` (+ test)

- **Author / when:** Claude / 2026-05-09
- **Size:** 158 LOC + 176 LOC test
- **Why removable:** Registered in `pyproject.toml` as a `poindexter.jobs`
  entry_point but NOT in the worker's boot list (PluginScheduler loads
  26 jobs; this is one of 7 that fail to register). Only caller is
  the test file. No CLI invocation. Last commit 2026-05-08 was a doc
  touch, not a feature.
- **Blocker:** verify it's failing to register because of a real
  schedule-string error (the scheduler skips silently on bad
  schedules) — if so, it may be a one-line fix, not a deletion.
- **Confidence:** medium
- **Decision:**

### `services/jobs/backfill_videos.py` (+ test)

- **Author / when:** Claude / 2026-05-09
- **Size:** 95 LOC + 191 LOC test
- **Why removable:** Same pattern as backfill_podcasts. Registered
  but unscheduled, only test caller. Video pipeline is dormant per
  the 2026-05-08 services audit (BUSINESS-OS-MODULE-SLOT, future).
- **Blocker:** Matt may want video backfill alive for when the
  module wakes up. Decide: keep dormant (tests stay green) or delete
  and re-add when needed.
- **Confidence:** medium
- **Decision:**

### `services/jobs/check_memory_staleness.py` (+ test)

- **Author / when:** Claude / 2026-05-09
- **Size:** 198 LOC + 175 LOC test
- **Why removable:** Registered, unscheduled, only test caller. Last
  commit 2026-05-01.
- **Blocker:** is "memory staleness" a deferred-but-real concern?
  brain memory is at 30k+ rows; if staleness audit is needed, this
  job is the only implementation.
- **Confidence:** low
- **Decision:**

### `services/jobs/detect_anomalies.py` (+ test)

- **Author / when:** Claude / 2026-05-09
- **Size:** 193 LOC + 143 LOC test
- **Why removable:** Registered, unscheduled, only test caller.
- **Blocker:** anomaly detection might be planned to fire on a brain
  cycle — verify the brain doesn't call this directly via a different
  import path before deleting.
- **Confidence:** low
- **Decision:**

### `services/jobs/regenerate_stock_images.py` (+ test)

- **Author / when:** Claude / 2026-05-09
- **Size:** 199 LOC + 192 LOC test
- **Why removable:** Registered, unscheduled, only test caller.
- **Blocker:** the SDXL image-regeneration story changed during the
  2026-05-04 OSS-migration push; this job may be a stale companion
  to the new pipeline.
- **Confidence:** medium
- **Decision:**

### `services/jobs/prune_orphan_embeddings.py` (+ test)

- **Author / when:** Claude / 2026-05-09
- **Size:** 337 LOC + 259 LOC test
- **Why removable:** NOT registered in pyproject entry_points. NOT
  scheduled. Only test caller. The embedding stack already has
  `collapse_old_embeddings.py` (739 LOC, alive via retention handler)
  and `auto_embed_posts.py` (62 LOC, scheduled). Three prune/collapse
  jobs is overlap; this is the unwired one.
- **Blocker:** verify embedding orphan accumulation isn't a real risk
  the system needs handled (probably collapse_old_embeddings already
  covers it).
- **Confidence:** medium-high
- **Decision:**

### `services/jobs/prune_stale_embeddings.py` (+ test)

- **Author / when:** Claude / 2026-05-09
- **Size:** 260 LOC + 270 LOC test
- **Why removable:** Same as prune_orphan_embeddings — unregistered,
  unscheduled, only test caller. Likely an early-iteration job
  superseded by the retention-policy mechanism.
- **Blocker:** see prune_orphan_embeddings — same story.
- **Confidence:** medium-high
- **Decision:**

### `webhook_events` table + `WebhookDeliveryService` + `emit_webhook_event` helper

- **Author / when:** Claude / 2026-05-09
- **Size:** ~190 LOC service + 1 table + ~5 emit call sites
- **Why removable:** The whole pipeline is dark by config. The
  delivery service starts only if `openclaw_webhook_url` is set —
  it's empty in app_settings, so the loop logs "No OPENCLAW_WEBHOOK_URL
  configured, webhook delivery disabled" at every boot. Meanwhile
  `content_router_service` / `task_executor` / `publish_service`
  keep emitting events into `webhook_events` (3,795 rows pending,
  oldest 2026-03-29). The whole table is a write-only queue feeding
  nothing. Notification to Discord/Telegram already goes through
  `notify_operator()` (which in turn fans out to the
  `webhook_endpoints` outbound rows — a _different_, working
  pipeline). The OpenClaw direction looked like Phase-1 plumbing that
  got superseded.
- **Blocker:** is OpenClaw's webhook ingestion still planned? If
  yes, set `openclaw_webhook_url` and let the queue drain. If
  no, drop emit calls + service + table + the formatter map at
  `webhook_delivery_service.py:148` (which is the last consumer of
  the per-event-type message strings, including the dead
  `task.needs_review` formatter).
- **Confidence:** medium-high (data: 3,795 undelivered rows, empty
  config row, no consumer)
- **Decision:**

### Resolved: `task.needs_review` event 30-day silence is NOT a bug

- **Author / when:** Claude / 2026-05-09
- **Note (not a deletion candidate, recording the resolution):** Audit
  finding flagged this fired last 2026-04-09. Investigation showed
  the emit was deliberately removed during a refactor that routed
  the awaiting-approval notification through `notify_operator()`
  directly (Discord + Telegram + alerts.log + stderr) instead of
  through `webhook_events` → OpenClaw. That refactor pre-dates the
  OpenClaw URL going empty. The dashboard panel that surfaced "30d
  silence" was effectively monitoring a dead path. Either remove
  the panel or fold it into the broader `webhook_events` audit
  above.

### Orphan CLI modules under `poindexter/cli/` not registered in `app.py`

- **Author / when:** Claude / 2026-05-09
- **Size:** 9 modules — `approval.py`, `migrate.py`, `publish_approval.py`,
  `qa_gates.py`, `retention.py`, `schedule.py`, `stores.py`, `taps.py`,
  `webhooks.py`
- **Why removable:** These files exist in `poindexter/cli/` but no
  `add_command(...)` line in `cli/app.py` registers them. Running
  `python -m poindexter <subcommand>` for any of these returns
  "No such command". Either the wiring was lost in a refactor, or
  these were superseded by the operator MCP tools / web routes and
  nobody pruned the source files. Worth a one-by-one walk to decide
  per module: re-register, or delete.
- **Blocker:** none — pure source-tree cleanup once we know which
  surfaces are still wanted as CLIs.
- **Confidence:** medium (some are probably worth re-registering, especially
  `taps`, `retention`, and `qa_gates` which are the operator's
  manual-trigger seam)
- **Decision:**

### `routes/webhooks.py` + `services/integrations/webhook_dispatcher.py`

- **Author / when:** Claude / 2026-05-09
- **Size:** 40 LOC route + 227 LOC dispatcher = 267 LOC
- **Why removable:** `routes/webhooks.py` has zero importers in
  `src/` and is NOT registered in `utils/route_registration.py`
  (only `routes.external_webhooks` is). Its only caller of
  `webhook_dispatcher` is itself. The live webhook stack is:
  `routes/external_webhooks.py` (Lemon Squeezy + Resend sinks) →
  `services/webhook_delivery_service.py` (7 callers). This pair is
  vestigial.
- **Blocker:** none identified.
- **Confidence:** high
- **Decision:**

---

## Pending migration — delete after the migration lands

### `services/workflow_executor.py` chain

- **Author / when:** services audit 2026-05-08
- **Size:** ~3,500 LOC across `workflow_executor.py`,
  `phase_registry.py`, `phase_mapper.py`, `phases/*`,
  `custom_workflows_service.py`, `template_execution_service.py`,
  `workflow_validator.py`
- **Why removable:** 0% production traffic per audit. Path A of the
  three orchestration tracks; legacy stack survives because
  `content_router_service` falls through to it when `template_slug`
  is null.
- **Blocker:** Phase 3 of #356 — must build `pipeline_templates/canonical_blog.py`
  mirroring today's 12-stage pipeline, flip
  `content_router_service.py:214` to default to
  `template_slug='canonical_blog'`, run a week of dual-write to diff
  outputs. Audit estimates ~7 days focused work.
- **Confidence:** high (post-migration)
- **Decision:**

### `services/social_poster.py` + bluesky/mastodon stack

- **Author / when:** Claude / 2026-05-09
- **Size:** 505 LOC poster + 207 LOC adapters
- **Why removable:** social_posts table = 0 rows; pipeline_distributions
  has 46 rows ALL on `target='gladlabs.io'`. Distribution has been
  dark since the dlvr.it retirement (GH-36).
- **Blocker:** **#72 diagnosis first** — find why bluesky doesn't
  fire (config IS set, credentials seeded, but `[SOCIAL]` log line
  absent for 24h+ across 5 publishes). If we can light it up, this
  becomes a "keep" item; if it's truly unwanted, deletion is the
  call.
- **Confidence:** depends on the diagnosis outcome
- **Decision:**

### Module-level `services/site_config.py:226` workaround section in CLAUDE.md

- **Author / when:** Claude / 2026-05-09
- **Size:** ~7 lines of CLAUDE.md docs (the "Production migration
  pattern" section)
- **Why removable:** the singleton sweep landed today (commit
  a90853d3); the workaround is GONE. CLAUDE.md still documents the
  alias-via-attribute pattern as a valid migration step.
- **Blocker:** none — already partially handled in commit a90853d3,
  but I want to do a re-read pass to make sure no stale "use the
  alias trick" guidance survived.
- **Confidence:** high
- **Decision:**

---

## Reframe — keep but document better

### Pure-learning scaffolds (`deepeval_rails`, `ragas_eval`, `guardrails_rails`, `rag_engine`)

- **Author / when:** services audit 2026-05-08
- **Size:** 148 + 142 + 157 + 445 = 892 LOC
- **Why kept:** intentional learning scaffolds per
  `feedback_learning_is_primary_goal` ("Adopt mature OSS even when
  'overkill' at current scale. Tool adoption IS the deliverable").
  Zero production traffic but they're not dead — they're
  pre-deployment.
- **Reframe action:** add `_scaffold/` prefix or directory marker
  per audit Phase 4 so the "scaffold" status is visible from the
  filename, not buried in a docstring. Decision: **keep** (do not
  delete).
- **Decision:** keep / move to `_scaffold/` later

---

## Rejected — discussed and kept

(Nothing here yet.)

---

## Notes / cross-references

- 2026-05-08 services audit: `.shared-context/audits/2026-05-08-services-folder-audit.md`
- 2026-05-08 self-healing audit: `.shared-context/audits/2026-05-08-self-healing-and-backups-audit.md`
- 2026-05-07 holistic review: `.shared-context/audits/2026-05-07-holistic-architectural-review.md`
- Yesterday's deletion sweep landed as commits 2077999b (caches +
  .gitea), and earlier the model_router trio + agents/content_agent +
  pipeline_flow + voice_agent_webrtc + phases/example_workflows +
  taps/gitea_issues all came out across the 2026-05-08 push.
