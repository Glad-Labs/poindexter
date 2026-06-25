# Postiz Social Distribution — Design Spec

**Date:** 2026-06-25
**Status:** Approved — pending implementation plan
**Author:** brainstorming session (Matt + Claude)

---

## Context

The current social pipeline generates X/Twitter and LinkedIn copy via LLM but does not post to any platform. Generated copy is forwarded to Telegram/Discord as a manual-posting notification. The only wired adapter is Mastodon (disabled). YouTube is wired for the `media` surface but handles video upload, not social text.

The goal is to replace the notify-only path with actual platform posting, expand platform coverage (Instagram, TikTok, Reddit, Bluesky, etc.), and eliminate per-platform OAuth maintenance burden — while keeping all draft review and approval within Poindexter's own surfaces (CLI, API, MCP), not a third-party UI.

**Chosen tool:** [Postiz](https://github.com/gitroomhq/postiz-app) — open-source (AGPL-3.0), TypeScript/Node.js, 33 platforms, weekly releases, PostgreSQL-backed, ships a public REST API and native MCP server. Selected over Mixpost (slower cadence, PHP/Laravel, ~10 platforms) and native adapters (would own OAuth maintenance per platform).

---

## Architecture

```
canonical_blog pipeline
  └─ social.generate_drafts atom  (new — runs after content.persist_task)
       └─ stores rows in social_post_drafts table

Blog post → awaiting_approval
  └─ Review + approval via any surface:

     CLI:  poindexter social list [--post-id X] [--status pending]
           poindexter social approve <draft-id>
           poindexter social reject <draft-id>
           poindexter social edit <draft-id> --content "..."
           poindexter social retry <draft-id>
           poindexter social setup

     API:  GET   /api/social/drafts?post_id=X&status=pending
           POST  /api/social/drafts/{id}/approve
           POST  /api/social/drafts/{id}/reject
           PATCH /api/social/drafts/{id}

     MCP:  list_social_drafts(post_id)
           approve_social_draft(draft_id)
           reject_social_draft(draft_id)
           edit_social_draft(draft_id, content)

  All three surfaces delegate to services/social_drafts.py
  └─ SocialDraftsService.approve_draft(draft_id, pool, site_config)
       └─ PostizClient.create_post(integration_id, content, platform_type, settings)
       └─ marks row posted / failed
       └─ Telegram critical alert on failure

Postiz (Docker sidecar — port 3003)
  ├─ owns OAuth credentials for all platforms (auto-refreshes tokens)
  ├─ handles rate limiting, retries, API versioning per platform
  └─ Poindexter is source of truth — Postiz never holds a pending draft
```

**Transport adapter contract (ADR 2026-06-10):** `SocialDraftsService` is the service layer. The CLI subcommand, route handler, and MCP tool are thin adapters — no business logic or SQL lives in any of the three adapters.

**Media distribution** reuses the existing `media_distribute.py` + `publishing_adapters` table pattern. The media approval gate already serves as the review step. Postiz fires after approval via a new `publishing.postiz_video` handler — same behaviour as the existing `youtube_main` adapter.

---

## Data Model

New table `social_post_drafts`:

```sql
id               UUID        PRIMARY KEY DEFAULT gen_random_uuid()
pipeline_task_id UUID        NOT NULL REFERENCES pipeline_tasks(task_id)
post_id          UUID        REFERENCES posts(id)   -- null until blog published; backfilled by publish_service
platform         TEXT        NOT NULL               -- "twitter", "linkedin", "reddit", "mastodon", "tiktok", "instagram_reels", "x_video", "linkedin_video"
content          TEXT        NOT NULL               -- generated copy
platform_config  JSONB       NOT NULL DEFAULT '{}'  -- {"subreddit": "r/LocalLLaMA"} for Reddit, {} elsewhere
status           TEXT        NOT NULL DEFAULT 'pending'
                                                    -- pending | approved | rejected | posted | failed
postiz_post_id   TEXT                               -- null until Postiz confirms the post
error            TEXT                               -- populated on failure
retry_count      INT         NOT NULL DEFAULT 0
last_retry_at    TIMESTAMPTZ
created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
approved_at      TIMESTAMPTZ
posted_at        TIMESTAMPTZ
```

Status lifecycle:

- `pending` — created by `social.generate_drafts` atom
- `approved` — Matt approves; Postiz call fires immediately
- `posted` — Postiz confirmed the post; `postiz_post_id` set
- `failed` — Postiz call or platform rejected; `error` populated; eligible for retry
- `rejected` — Matt rejected; terminal, no retry

`post_id` backfill: drafts are created with `pipeline_task_id` set and `post_id` null. `publish_service.publish_post_from_task` backfills `post_id` on matching drafts after the blog post is published (same pattern as `posts.metadata->>'pipeline_task_id'` backfill from `20260528_021920`).

---

## Pipeline Integration

### New atom: `social.generate_drafts`

Location in `canonical_blog` graph_def (atom-cutover #355):

```
... → content.persist_task → social.generate_drafts (new) → content.record_pipeline_version → content.evaluate_auto_publish
```

Atom responsibilities:

1. Reads `social_draft_platforms` from `app_settings` (comma-separated, e.g. `"twitter,linkedin,mastodon"`)
2. Reads `social_reddit_subreddits` from `app_settings` (comma-separated, e.g. `"r/LocalLLaMA,r/ArtificialIntelligence,r/selfhosted,r/homelab,r/Python,r/opensource"`) — generates one draft per subreddit
3. Calls existing `social_poster.generate_social_posts()` for each text platform (generation only, no distribution)
4. Generates subreddit-aware Reddit copy (subreddit name passed as prompt context via `social.reddit_promote` prompt key)
5. Writes one `social_post_drafts` row per platform/subreddit with `status=pending`

A typical `canonical_blog` run produces **~9 drafts**: 1 X + 1 LinkedIn + 1 Mastodon + 6 Reddit (one per subreddit). `list_social_drafts` groups by platform for readability.

### Backwards compat / cutover flag

The existing `generate_and_distribute_social_posts` call in `post_pipeline_actions` is guarded by:

```python
if not site_config.get_bool("social_drafts_enabled", False):
    await generate_and_distribute_social_posts(...)   # old notify path
```

When `social_drafts_enabled=false` (current default), the old Telegram/Discord notification path stays active. Flip to `true` after Postiz is running and integration UUIDs are configured. The atom is a no-op until then — so pipeline changes, data model migration, and Postiz Docker setup can land in separate PRs without breaking production.

---

## Postiz Client + Adapters

### `services/integrations/postiz_client.py`

Thin `httpx.AsyncClient` wrapper. Credentials read from `app_settings` at call time (never captured at import).

```python
class PostizClient:
    # Constructed per-call or injected; base URL from postiz_api_url app_setting

    async def create_post(
        integration_id: str,       # UUID from app_settings: postiz_integration_id_{platform}
        content: str,
        platform_type: str,        # Postiz __type: "x", "linkedin", "reddit", "mastodon", "tiktok"
        platform_settings: dict,   # {"subreddit": "r/LocalLLaMA"} for Reddit; {} elsewhere
        upload_ids: list[str],     # empty for text posts; Postiz upload IDs for video
    ) -> dict                      # {"success": bool, "post_id": str|None, "error": str|None}

    async def upload_from_url(video_url: str) -> str  # → Postiz upload ID
```

Postiz API payload (self-hosted: `{postiz_api_url}/public/v1/posts`):

```json
{
  "type": "now",
  "date": "<ISO8601>",
  "shortLink": false,
  "tags": [],
  "posts": [
    {
      "integration": { "id": "<uuid>" },
      "value": [{ "content": "copy text", "image": [] }],
      "settings": { "__type": "x" }
    }
  ]
}
```

Reddit example:

```json
"settings": { "__type": "reddit", "subreddit": "r/LocalLLaMA" }
```

Video example: `image` array contains `[{ "id": "<postiz-upload-id>" }]` from `upload_from_url`.

### Integration UUID lookup

One `app_settings` key per connected account (set once after Postiz account setup via `poindexter social setup`):

```
postiz_integration_id_twitter     → UUID  # used for text posts AND X video (same account)
postiz_integration_id_linkedin    → UUID  # used for text posts AND LinkedIn video (same account)
postiz_integration_id_mastodon    → UUID
postiz_integration_id_reddit      → UUID
postiz_integration_id_tiktok      → UUID
postiz_integration_id_instagram   → UUID  # used for Instagram Reels
```

One UUID per connected social account — Postiz doesn't distinguish text vs video at the integration level. The `__type` field and presence of `upload_ids` in the payload distinguish text posts from video posts. The `postiz_video` handler strips the `_video` suffix from the `publishing_adapters.platform` column (e.g. `x_video` → looks up `postiz_integration_id_twitter`) to find the right UUID.

These are plain config rows (not secrets — just UUIDs). Postiz owns the OAuth token refresh lifecycle; Poindexter's config never changes when tokens rotate.

### `SocialDraftsService` (services/social_drafts.py)

```python
create_draft(pipeline_task_id, platform, content, platform_config, pool) → UUID
approve_draft(draft_id, pool, site_config) → dict   # fires PostizClient, updates row
reject_draft(draft_id, pool) → None
edit_draft(draft_id, content, platform_config, pool) -> None
list_drafts(post_id, pipeline_task_id, status, pool) → list[SocialDraftRow]
retry_draft(draft_id, pool, site_config) → dict     # re-fires approve_draft logic
backfill_post_id(pipeline_task_id, post_id, pool) → None
```

### Publishing adapters — media surface

New rows in `publishing_adapters` (surface=`media`), dispatched by `media_distribute.py` after media approval:

| name                     | platform          | handler        | target                          |
| ------------------------ | ----------------- | -------------- | ------------------------------- |
| `postiz_tiktok`          | `tiktok`          | `postiz_video` | TikTok — `video_short`          |
| `postiz_instagram_reels` | `instagram_reels` | `postiz_video` | Instagram Reels — `video_short` |
| `postiz_x_video`         | `x_video`         | `postiz_video` | X video — `video_short`         |
| `postiz_linkedin_video`  | `linkedin_video`  | `postiz_video` | LinkedIn video — `video`        |

YouTube stays on its existing `youtube_main` adapter.

`publishing.postiz_video` handler: `upload_from_url(r2_video_url)` → upload ID → `create_post(...)`.
Caption comes from the existing media script in the dispatch payload; no new LLM call needed.

---

## Infrastructure

| Dependency | Status            | Notes                                                   |
| ---------- | ----------------- | ------------------------------------------------------- |
| PostgreSQL | Already running   | New `postiz` database on existing container             |
| R2 / S3    | Already have R2   | Postiz uses same storage credentials                    |
| **Redis**  | **New container** | Postiz Bull job queue — `redis:7.2-alpine`, ~50 MB idle |

New containers added to **both** `docker-compose.local.yml` and `docker-compose.consumer.yml`:

```yaml
postiz-redis:
  image: redis:7.2-alpine
  volumes:
    - gladlabs-postiz-redis:/data

postiz:
  image: ghcr.io/gitroomhq/postiz-app:latest
  ports:
    - '3003:3000' # 3000=Grafana, 3002=Uptime Kuma already taken
  depends_on:
    - postiz-redis
    - postgres
  environment:
    DATABASE_URL: postgresql://.../<postiz_db>
    REDIS_URL: redis://postiz-redis:6379
    MAIN_URL: http://localhost:3003
    FRONTEND_URL: http://localhost:3003
    NEXT_PUBLIC_BACKEND_URL: http://localhost:3003
    JWT_SECRET: <from bootstrap.toml — new key>
    # R2 credentials: same keys as existing storage_* app_settings
```

Postiz UI at `http://localhost:3003` — used once per platform to connect OAuth accounts and copy integration UUIDs. After initial setup, all interaction is through Poindexter's CLI/API/MCP.

`poindexter social setup` — new CLI command guiding through: copy UUIDs from Postiz → `poindexter settings set postiz_integration_id_* <uuid>` → flip `social_drafts_enabled=true`.

New entry added to CLAUDE.md local services table:

```
| Postiz | http://localhost:3003 | Social media scheduler — OAuth + posting engine |
```

---

## Error Handling

| Scenario                                            | Behaviour                                                         |
| --------------------------------------------------- | ----------------------------------------------------------------- |
| Postiz unreachable at approval time                 | Draft → `failed`, error stored, Telegram critical alert           |
| Platform rejects post (rate limit, rules violation) | Draft → `failed`, Postiz error message in `error` column          |
| Video upload timeout (R2 → Postiz)                  | `failed` + alert; 90s timeout on `upload_from_url`                |
| Pipeline fails after drafts generated               | Drafts stay `pending`; visible in CLI + Grafana panel for cleanup |
| Max retries hit                                     | Draft stays `failed`; no further auto-retry; Telegram alert       |

**Retry job:** `retry_failed_social_drafts` — runs hourly. Picks up `failed` rows where `retry_count < social_draft_max_retries` (app_settings, default 3). Increments `retry_count`, updates `last_retry_at`, re-fires `approve_draft`. Manual retry always available via `poindexter social retry <draft-id>`.

---

## Grafana

New "Social" row added to the Pipeline dashboard (same commit as feature — per `feedback_grafana_everything`):

| Panel                 | Type            | Source                                                                 |
| --------------------- | --------------- | ---------------------------------------------------------------------- |
| Drafts by status      | Stat (5 panels) | `social_post_drafts` counts by status                                  |
| Posts fired (24h)     | Time series     | `poindexter_social_drafts_total{status="posted"}` by platform          |
| Postiz API error rate | Time series     | `rate(poindexter_postiz_api_errors_total[1h])` by platform             |
| Pending drafts        | Table (SQL)     | `social_post_drafts WHERE status='pending'` — surfaces orphaned drafts |

New Prometheus metrics (module-level singletons, same pattern as `SOCIAL_ADAPTER_POSTS_TOTAL`):

```
poindexter_social_drafts_total{platform, status}           # incremented on each status transition
poindexter_social_draft_approve_latency_seconds{platform}  # histogram: draft created → approved
poindexter_postiz_api_errors_total{platform}               # Postiz API call failures
```

---

## New app_settings keys

| Key                        | Type | Default              | Notes                                                        |
| -------------------------- | ---- | -------------------- | ------------------------------------------------------------ |
| `social_drafts_enabled`    | bool | `false`              | Feature flag — flip after Postiz is configured               |
| `social_draft_platforms`   | str  | `""`                 | Comma-separated: `twitter,linkedin,mastodon`                 |
| `social_reddit_subreddits` | str  | `""`                 | Comma-separated: `r/LocalLLaMA,r/ArtificialIntelligence,...` |
| `social_draft_max_retries` | int  | `3`                  | Max auto-retry attempts for failed drafts                    |
| `postiz_api_url`           | str  | `http://postiz:3000` | Internal Docker URL                                          |
| `postiz_integration_id_*`  | str  | `""`                 | One per platform — set via `poindexter social setup`         |

---

## Implementation order (suggested)

1. Migration — `social_post_drafts` table
2. `SocialDraftsService` + `PostizClient`
3. CLI surface (`poindexter social *`)
4. API routes (`/api/social/drafts/*`)
5. MCP tools
6. `social.generate_drafts` atom + graph_def wiring + `post_pipeline_actions` flag guard
7. Docker — Postiz + Redis containers; `poindexter social setup` command
8. `publishing.postiz_video` handler + `publishing_adapters` rows
9. Prometheus metrics + Grafana Social row
10. `retry_failed_social_drafts` job
11. CLAUDE.md update (local services table, `social_post_drafts` in key tables list)
