# LangGraph Cutover (Lane C)

The content pipeline ran on the legacy chunked StageRunner flow inside
`services/content_router_service.process_content_generation_task`
since 2026-04. The Lane C cutover (`Glad-Labs/poindexter#355`,
`#450`) replaces it with the LangGraph `canonical_blog` template
already shipped in `services/pipeline_templates/__init__.py`.

This doc is the operator runbook for running the cutover. The
implementation seam is small (one app_setting + one INSERT change),
the risk profile is high (touches 99% of production traffic), so the
rollout is staged.

## How dispatch works today

```
poll loop (TaskExecutor)
   │
   ▼
content_router_service.process_content_generation_task
   │
   ├─ template_slug IS NOT NULL ──► TemplateRunner.run(slug, state)
   │                                  └─► pipeline_templates[slug] graph
   │
   └─ template_slug IS NULL    ──► legacy chunked StageRunner flow
                                     (inline in content_router_service)
```

`dev_diary` cron tasks already pass `template_slug='dev_diary'`
explicitly. Every other task creator (`tasks_db.add_task`,
`bulk_add_tasks`, `topic_batch_service`, `topic_proposal_service`,
`topic_discovery`) historically left the column NULL.

## What the Lane C migration adds

A single `app_settings.default_template_slug` row (default `''`).
`tasks_db.add_task` and `bulk_add_tasks` now consult this setting
when the caller doesn't pass `template_slug` explicitly:

```python
template_slug = task_data.get("template_slug")
if not template_slug:
    template_slug = await _resolve_default_template_slug(self.pool)
template_slug = template_slug or None  # '' / falsy → NULL
```

So the resolution order per task is:

1. **Caller-supplied** — `task_data["template_slug"]` (used by
   `dev_diary`, will be used by other purpose-specific creators)
2. **`app_settings.default_template_slug`** — operator's global
   cutover knob
3. **NULL** — legacy chunked StageRunner path runs

## Cutover stages

### Stage 0 — pre-flight (today, 2026-05-10)

- ✅ `canonical_blog` template lives at
  `services/pipeline_templates/__init__.py:79`
- ✅ Dispatcher in `content_router_service` routes by `template_slug`
- ✅ `default_template_slug` setting seeded empty (this migration)
- ✅ `tasks_db.add_task` + `bulk_add_tasks` consult the setting

No behaviour change. Operator can opt-in at any time.

### Stage 1 — single-task smoke (operator-driven)

Pass `template_slug='canonical_blog'` explicitly on a single
known-good topic via the operator dashboard / CLI / REST API.
Verify the resulting post in `awaiting_approval` matches what the
legacy path would have produced (compare against a recent post with
similar topic + length).

### Stage 2 — A/B canary (recommended ~24h)

Flip the global default to `'canonical_blog'`:

```sql
UPDATE app_settings
SET value = 'canonical_blog'
WHERE key = 'default_template_slug';
```

Watch the QA Rails dashboard
([http://localhost:3000/d/qa-rails](http://localhost:3000/d/qa-rails))
for divergence:

- Approval-rate gauge should track the prior week's rate ±5pp
- Per-reviewer table should show the same reviewers running with
  similar score distributions
- Latest-20 table should show clean qa_pass_completed rows with
  reviewer_count matching the legacy chain's reviewer count

If any signal regresses, revert with:

```sql
UPDATE app_settings SET value = '' WHERE key = 'default_template_slug';
```

### Stage 3 — production cutover (after Stage 2 stable)

Leave `default_template_slug='canonical_blog'` indefinitely. Every
new task routes through TemplateRunner.

### Stage 4 — legacy code removal (~7 days post-Stage 3)

Once no NULL-slug tasks have rolled through in a week (verify via
audit_log: `SELECT COUNT(*) FROM pipeline_tasks WHERE template_slug
IS NULL AND created_at > NOW() - INTERVAL '7 days'` → 0):

- Delete the legacy chunked StageRunner block in
  `content_router_service.process_content_generation_task` (the
  `if _template_slug` short-circuit returns become the only path)
- Delete the `try`-block fallback chunks (`_summary1`, `_summary2`,
  `_summary3`, etc.)
- Eventually shrink `task_executor.py` itself once
  `process_content_generation_task` becomes a thin wrapper around
  `TemplateRunner.run`

`task_executor.py` still does pipeline-task polling + heartbeat +
stale-task sweeps + retry logic — none of that goes away in Lane C.
Only the legacy stage-chain dispatch inside
`process_content_generation_task` is replaced.

## Why the dual-write window matters

The LangGraph template should produce byte-identical results to the
legacy chain because each node wraps a registered Stage instance via
`make_stage_node`. But "should" isn't "does" — graph wiring bugs
(missed conditional edge, halt semantics divergence, state-merge
collisions) only surface under production traffic. The Stage 2 canary
catches these before they hit a week's worth of approval throughput.

## Ground truth

- Cutover seam: `services/tasks_db.py:_resolve_default_template_slug`,
  `add_task` (line ~287), `bulk_add_tasks` (line ~470)
- Dispatcher: `services/content_router_service.py:202-261`
- Template: `services/pipeline_templates/__init__.py:79-123`
- Migration: `services/migrations/20260510_044707_seed_default_template_slug.py`
- Tests: `tests/unit/services/test_tasks_db.py` (TestAddTaskTemplateSlug, 5 cases)
- Issues: `Glad-Labs/poindexter#355` (umbrella), `#450` Lane C, `#356` (closed Phase 1 POC)
