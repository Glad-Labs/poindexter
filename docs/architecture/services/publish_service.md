# Publish Service

**File:** `src/cofounder_agent/services/publish_service.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_publish_service.py`
**Last reviewed:** 2026-04-30

## What it does

`publish_post_from_task()` is the ONE place where a completed
`content_tasks` row becomes a row in `posts`. It runs from three
entry points (the `/approve` endpoint with `auto_publish=True`, the
explicit `/publish` endpoint, and `TaskExecutor._auto_publish_task`)
and handles everything that should happen exactly once when a post
goes live: parse merged result+metadata, extract title from content,
slugify, resolve author + category + tags, insert the `posts` row,
update the task to `published`, emit a `post.published` webhook, then
fan out a long list of fire-and-forget side effects.

Side effects (each gated by feature toggles or local-mode checks):
sync to cloud DB, embed into pgvector, queue social posts,
cross-post to Dev.to, ISR revalidation on Vercel, static JSON export
to R2, IndexNow + Google sitemap pings, podcast episode generation,
video episode generation, short-form video, R2 media upload + RSS
regen, YouTube upload, newsletter blast, OpenClaw notification.

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
- `internal_api_base_url` (default = `DEFAULT_WORKER_API_URL`) — used
  to fetch the live podcast/video RSS feeds before R2 upload.
- `short_video_post_publish_delay_seconds` (default `180`) — wait
  before generating the short video so the long-form podcast/video
  finish first.
- `media_r2_upload_delay_seconds` (default `240`) — wait before
  uploading media to R2 so generators have time to finish.
- `social_distribution_platforms` (string, e.g. `"x,linkedin,youtube"`) —
  controls whether YouTube upload runs.
- `max_posts_per_day` (default `3`, only when `honor_pacing=True`).
- `publish_spacing_hours` (default `4`, only when `honor_pacing=True`).

Bootstrap-only env var:

- `LOCAL_DATABASE_URL` — presence flips on the local-mode side
  effects (cloud sync, podcast/video gen, R2 upload, newsletter).
  This is one of the ~8 legitimate env vars per GH#93. See
  `_should_run_post_publish_hooks()`.

## Dependencies

- **Reads from:**
  - `content_tasks` (the task arg, plus the existing-slug guard)
  - `tags` table (resolved tag rows for `post_tags` junction)
  - `services.category_resolver.select_category_for_topic`
  - `services.default_author.get_or_create_default_author`
  - `services.site_config.site_config` for IndexNow + URL settings
  - `utils.text_utils.extract_title_from_content` (LLM `# Title` lift)
- **Writes to:**
  - `posts` (the central INSERT)
  - `tags` (upserts each new term — `ON CONFLICT (slug) DO UPDATE`)
  - `post_tags` (via `db_service.create_post`'s `tag_ids` handling)
  - `content_tasks` (status → `published`, result JSON updated)
  - `pipeline_events` indirectly via `emit_webhook_event("post.published", ...)`
  - `audit_log` indirectly via the `[content_published]` log line
- **External APIs (all fire-and-forget, errors swallowed):**
  - Vercel ISR (`trigger_nextjs_revalidation`)
  - IndexNow + Google sitemap ping
  - Cloudflare R2 / S3 (`upload_to_r2`, `upload_podcast_episode`,
    `upload_video_episode`)
  - Dev.to (`DevToCrossPostService`)
  - YouTube upload (`services.social_adapters.youtube`)
  - Internal worker API (`internal_api_base_url`) for RSS regen
  - Newsletter delivery (`send_post_newsletter`)
  - Telegram/Discord via `_notify_openclaw`

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
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_bulk_publish.md`
  — Matt's rule that bulk publishes never bypass per-post approval.
