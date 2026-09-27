# Publish Service

**File:** `src/cofounder_agent/poindexter/services/publish_service.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_publish_service.py`
**Last reviewed:** 2026-04-30

## What it does

`publish_post_from_task()` is the ONE place where a completed
`pipeline_tasks` row becomes a row in `posts`. It has three exits
(see `reference_publish_post_from_task_three_exits` in project
memory): the `stage_only` short-circuit (the default operator flow —
approve stages a post at `status='approved'` and returns early), the
`_promote_or_skip_existing` short-circuit (a later go-live call
promotes the staged row in place), and the immediate-publish tail
(reached only by a direct/immediate publish, rare since ~2026-06-24).
Each exit handles everything that should happen exactly once when a
post goes live: parse merged result+metadata, extract title from
content, slugify, resolve author + category + tags, insert (or
promote) the `posts` row, update the task status, and — on the
promote and tail exits — fan out a list of fire-and-forget side
effects.

Side effects (each gated by feature toggles or local-mode checks):
sync to cloud DB, embed into pgvector, cross-post to Dev.to, ISR
revalidation on Vercel, static JSON export to R2, IndexNow + Google
sitemap pings, newsletter blast, operator notification. Podcast/video
generation, delivery to R2, and RSS feed rebuilds are NOT done here —
they're owned by the Stage-2/3 render pipeline and
`jobs/podcast_distribute.py` / `jobs/media_distribute.py`
(task-keyed, Gate-2-approval-driven). A prior fire-and-forget R2-upload
hook on the immediate-publish tail (`_upload_media_to_r2_bg`, "phase
11e") was retired 2026-09-25: it operated on a post-keyed file
convention nothing in the pipeline had produced since the task-keyed
delivery cutovers, and — being on the tail — was unreachable from the
default flow besides.

The pacing scheduler (`_calculate_scheduled_publish_time`) is opt-in
via `honor_pacing=True`. Default is immediate publish because the
human reviewer is already the throttle.

## Public API

- `await publish_post_from_task(db_service, task, task_id, *, publisher="operator", trigger_revalidation=True, queue_social=True, draft_mode=False, honor_pacing=False, background_tasks=None) -> PublishResult` —
  the single canonical entry point.
- `PublishResult(success, post_id, post_slug, published_url, post_title, revalidation_success, error)` —
  return value with `to_dict()` for HTTP responses.

The pacing helper is internal:

- `_calculate_scheduled_publish_time(db_service)` — returns `None`
  (publish now) or a future UTC `datetime`. Reads `max_posts_per_day`
  - `publish_spacing_hours` from `app_settings`.

## Configuration

All from `app_settings` via `site_config`:

- `site_url` (REQUIRED, no default — `site_config.require()` raises if
  missing) — used for IndexNow + sitemap pings + YouTube descriptions.
- `indexnow_key` (default `""`) — IndexNow ping API key. Empty key
  still sends the ping; setting `indexnow_ping_url=""` disables.
- `indexnow_ping_url` (default `https://api.indexnow.org/indexnow`) —
  set to `""` to skip IndexNow entirely.
- `google_sitemap_ping_url` (default `https://www.google.com/ping`) —
  set to `""` to skip Google sitemap ping.
- `max_posts_per_day` (default `3`, only when `honor_pacing=True`).
- `publish_spacing_hours` (default `4`, only when `honor_pacing=True`).

Bootstrap-only env var:

- `DEPLOYMENT_MODE` — `"worker"` flips on the local-mode side effects
  (cloud sync, Dev.to cross-post, newsletter); `"coordinator"` (the
  default) skips them all. Read directly from the environment rather
  than via `site_config`, deliberately, so the check is consistent
  with how `main.py` decides which mode to start in. See
  `_should_run_post_publish_hooks()`. (Read `LOCAL_DATABASE_URL`
  until 2026-05-08 — a stale signal no container actually set, which
  silently disabled every hook for 8 days.)

## Dependencies

- **Reads from:**
  - `pipeline_tasks` (the task arg, plus the existing-slug guard)
  - `tags` table (resolved tag rows for `post_tags` junction)
  - `services.category_resolver.select_category_for_topic`
  - `services.default_author.get_or_create_default_author`
  - `site_config` (from AppContainer or DI) for IndexNow + URL settings
  - `utils.text_utils.extract_title_from_content` (LLM `# Title` lift)
- **Writes to:**
  - `posts` (the central INSERT)
  - `tags` (upserts each new term — `ON CONFLICT (slug) DO UPDATE`)
  - `post_tags` (via `db_service.create_post`'s `tag_ids` handling)
  - `pipeline_tasks` (status → `published`, result JSON updated)
  - `webhook_events` indirectly via `emit_webhook_event("post.published", ...)` (the helper's actual target — earlier docs miscalled it `pipeline_events`; that unrelated table was dropped 2026-05-04 in poindexter#366)
  - `audit_log` indirectly via the `[content_published]` log line
- **External APIs (all fire-and-forget, errors swallowed):**
  - Vercel ISR (`trigger_nextjs_revalidation`)
  - IndexNow + Google sitemap ping
  - Cloudflare R2 / S3 (`upload_to_r2`, via `static_export_service` for
    the post's JSON — NOT for podcast/video, which this file no longer
    touches; see "What it does" above)
  - Dev.to (`DevToCrossPostService`)
  - Media distribution — podcast delivery via
    `services/jobs/podcast_distribute.py`, video/YouTube/Postiz via the
    `publishing_adapters` surface (`services/jobs/media_distribute.py`)
    — neither is called from this file; they run on their own schedule
    against the `media_assets` / `media_approvals` rows this file's
    upstream pipeline produced
  - Newsletter delivery (`send_post_newsletter`)
  - Telegram/Discord via `notify_operator`

## Failure modes

- **Missing content or topic** — short-circuits with
  `PublishResult(success=False, error="Missing content or topic — cannot create post")`.
- **Duplicate task already published** — slug-suffix idempotency guard
  finds an existing post with `slug LIKE '%' || task_id[:8]`. Returns
  `PublishResult(success=True, ...)` pointing at the original. Does
  not insert a duplicate. Visible in logs as
  `Post already exists for task ... — skipping duplicate`.
- **`db_service.create_post` raises** — returns
  `PublishResult(success=False, error="Failed to create post: ...")`.
  No fire-and-forget side effects fire.
- **Side-effect failure** — every fire-and-forget block is wrapped in
  `try/except Exception` and logged at debug or warning. Publish
  succeeds even if every side effect fails. This is intentional;
  losing a sitemap ping must not block a post going live.
- **`site_url` not set** — `site_config.require("site_url")` raises
  `RuntimeError` BEFORE the search-engine pings would run. The post
  is already in the DB at that point, but the function will bubble
  the error and revalidation/notification step won't complete. Set
  `site_url` in `app_settings` before publishing.
- **ISR revalidation failure** — non-fatal; `revalidation_success=False`
  comes back on the `PublishResult` and is logged as a warning.

## Common ops

- **Force a duplicate publish** — change the `task_id` (the
  idempotency guard keys on `task_id[:8]` in the slug suffix).
- **Disable social fan-out for one publish:** pass
  `queue_social=False`.
- **Publish as draft (skip live distribution side effects):** pass
  `draft_mode=True`. The post lands as `status='draft'`,
  `distributed_at` stays NULL.
- **Schedule pacing for a backlog:** pass `honor_pacing=True` and tune
  `max_posts_per_day` + `publish_spacing_hours`. Otherwise the human
  reviewer is the throttle.
- **Re-trigger ISR for a stuck cache:**
  `await trigger_nextjs_revalidation(["/posts/<slug>"], ["post:<slug>"])`
  via the FastAPI shell or a one-off script.
- **Audit recent publishes:**
  `SELECT id, slug, published_at, status FROM posts ORDER BY published_at DESC LIMIT 20;`

## See also

- `docs/architecture/services/content_router_service.md` — upstream
  pipeline that produced the task.
- `docs/operations/disaster-recovery.md` — cleanup steps when a
  publish goes wrong.
- `feedback_no_bulk_publish` (operator design note)
  — Matt's rule that bulk publishes never bypass per-post approval.
