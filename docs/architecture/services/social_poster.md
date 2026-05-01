# Social Poster

**File:** `src/cofounder_agent/services/social_poster.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_social_poster.py`
**Last reviewed:** 2026-04-30

## What it does

`social_poster` generates platform-specific social media copy
(currently Twitter/X and LinkedIn) when a blog post publishes, using
the local Ollama LLM. It then both (a) notifies the operator via
Telegram + Discord (`#ops` channel via OpenClaw) so a human can
copy-paste/post manually, and (b) optionally fires the generated text
through enabled social-platform adapters (Bluesky, Mastodon today;
LinkedIn / Reddit / YouTube exist as `NotImplementedError` stubs per
GH-40) for actual API posting.

It runs as a fire-and-forget background task spawned from
`publish_service` after a successful publish. Generation failures are
non-fatal — the publish completes regardless. Adapter failures (a
platform API rejection, a stub raising `NotImplementedError`, an
unexpected exception) are wrapped per-call so one bad adapter never
takes down distribution to the others (GH-36).

## Public API

- `await generate_social_posts(title, slug, excerpt, keywords=None, ollama=None) -> list[SocialPost]` —
  pure generation. Builds prompts, calls Ollama, returns
  `[SocialPost(platform=..., text=..., post_url=..., posted=False)]`
  for each platform. Returns an empty / partial list on generation
  failure (logged, not raised).
- `await generate_and_distribute_social_posts(title, slug, excerpt, keywords=None, ollama=None) -> list[SocialPost]` —
  end-to-end: generate, notify operator on Telegram + Discord, post
  to enabled platform adapters. This is the function `publish_service`
  calls.

### `SocialPost` dataclass

`platform` (`"twitter"` or `"linkedin"`), `text`, `post_url`,
`created_at` (UTC default), `posted` (always `False` today — flips
when adapter posting is wired more broadly).

## Key behaviors / invariants

- **Ollama-only for copy generation.** Uses `OllamaClient` with
  `temperature=0.8` and the model from
  `social_poster_model` (default `ollama/llama3:latest`). No cloud
  fallback — if Ollama is down, no copy is generated, and a
  `[Social Poster] Failed to generate social posts for: <title>`
  notification fires.
- **Hard truncation as a safety net.** The prompts include the
  character limit, but the LLM occasionally exceeds it. If the
  generated text is over the limit, it's truncated at the last
  whitespace before the limit and `...` is appended, with a warning
  log.
- **Wrapping quote stripping.** If the LLM returns
  `"my tweet text"` (with literal quotes), the wrapping pair is
  stripped before character counting.
- **Notification path is dual.** Every generated post is sent to BOTH
  Telegram (direct bot API) and Discord (via OpenClaw `/hooks/agent`
  endpoint). Telegram bot token + chat_id come from
  `services.telegram_config`; Discord channel is read lazily via
  `_sc.require("discord_ops_channel_id")` so import-time test
  collection doesn't crash without a DB.
- **Adapter loop is exception-isolated.** `_safe_call_adapter` wraps
  every adapter call: returns `{"success": False, "post_id": None,
"error": "..."}` for any exception (including
  `NotImplementedError`), increments per-platform Prometheus counters
  with `outcome={success,failure,error,skipped}`. Distribution
  continues across remaining platforms.
- **Generic adapters use Twitter copy.** For Bluesky / Mastodon /
  Reddit, the Twitter post text (shortest, fits everywhere) is the
  default. LinkedIn uses the LinkedIn-specific copy if available,
  else falls back to the Twitter text.
- **Module-level constants captured at import.** `SITE_BASE_URL`,
  `_OPENCLAW_URL`, `_OPENCLAW_TOKEN`, `_SOCIAL_MODEL`,
  `TWITTER_CHAR_LIMIT`, `LINKEDIN_CHAR_LIMIT` are all read from
  `site_config` at import time. Settings changes require a
  process restart to take effect on these. (See "Status callout"
  below.)

## Status: module-level config caching

Six values are captured at module import time rather than per-call,
which means `poindexter set social_poster_model gemma3:27b` doesn't
take effect until the worker restarts. This contradicts the
project's "DB-first, runtime-tunable" stance but is the current
shape — a future refactor should turn these into per-call lookups.
The character limits being captured at import is the most surprising
of the six; if Twitter changes its limit again, you'll need to bounce
the worker after the setting update.

## Configuration

All from `app_settings` via `services.site_config`:

- `social_poster_model` (default `ollama/llama3:latest`) — model used
  for generation. The `ollama/` prefix is stripped before being
  passed to `OllamaClient`.
- `social_poster_max_tokens` (default `300`) — max_tokens cap for
  Ollama generation. Twitter copy rarely needs more than ~100 tokens.
- `social_twitter_char_limit` (default `280`) — Twitter post cap.
- `social_linkedin_char_limit` (default `700`) — LinkedIn cap (note:
  LinkedIn's actual limit is 3000, but the prompt asks for
  newsletter-friendly brevity).
- `social_distribution_platforms` (default empty) — comma-separated
  set of platforms to actually post to via adapters. Recognized
  values: `bluesky`, `mastodon`, `linkedin`, `reddit`. Leave empty
  to skip adapter posting and rely on operator copy-paste from the
  Discord/Telegram notification.
- `site_url` — used to build the `post_url` in tweet text.
- `company_name` — injected into the prompt as the speaker.
- `openclaw_gateway_url` (default `DEFAULT_OPENCLAW_URL`) — Discord
  notification webhook target.
- `openclaw_webhook_token` (default `"hooks-gladlabs"`) — Bearer
  auth for the OpenClaw call.
- `discord_ops_channel_id` — required at notify time (not import
  time). Fails loud (`RuntimeError`) if unset when a publish
  triggers notification.
- Telegram bot token + chat ID come from `services.telegram_config`,
  which itself reads from `site_config`.

## Dependencies

- **Reads from:**
  - `services.site_config.site_config` — all configuration above.
  - `services.bootstrap_defaults.DEFAULT_OPENCLAW_URL` — fallback URL.
  - `services.telegram_config.TELEGRAM_BOT_TOKEN` /
    `TELEGRAM_CHAT_ID` — re-exported convenience.
  - `services.ollama_client.OllamaClient` — generation backend.
- **Writes to:**
  - `prometheus_client` Counter registry — best-effort metric
    increments (`poindexter_social_adapter_posts_total`,
    `poindexter_social_adapter_errors_total`).
- **External APIs:**
  - Ollama (local) — copy generation.
  - Telegram Bot API (`https://api.telegram.org/bot<TOKEN>/sendMessage`).
  - OpenClaw (`POST /hooks/agent`) — Discord delivery.
  - Per-adapter APIs (Bluesky, Mastodon, ...) — only when listed in
    `social_distribution_platforms`.
- **Callers:**
  - `services.publish_service` (post-publish hook section "Queue
    social media post generation"). Spawns
    `generate_and_distribute_social_posts` either as a FastAPI
    `BackgroundTasks` task or a free-standing asyncio task.

## Failure modes

- **Ollama unreachable / model missing** — `OllamaClient.generate`
  raises, caught in `_generate_social_text`, logged as error,
  returns `""`. The empty post is logged (`"... generation failed —
empty result"`) and skipped. If both platforms come back empty,
  `generate_and_distribute_social_posts` sends a `[Social Poster]
Failed to generate social posts for: <title>` Telegram + Discord
  notification.
- **Telegram or Discord notification fails** — `_notify` catches and
  logs as warning. Distribution continues — adapter posting is
  attempted regardless.
- **`discord_ops_channel_id` unset at notify time** —
  `_get_discord_ops_channel()` calls `_sc.require()` which raises
  `RuntimeError`. The notification fails loud — operator sees the
  error, not silent skip.
- **Adapter raises** — `_safe_call_adapter` catches everything,
  returns the failure dict, bumps `social_adapter_errors_total{platform}`
  and `social_adapter_posts_total{platform,outcome="error"}`.
- **Adapter returns non-dict** — treated as a failure with
  `error="adapter returned non-dict"`. Adapter contract is "always
  return a `{success, post_id, error}` dict."
- **Stub adapter (LinkedIn / Reddit / YouTube before API wire-up)**
  — raises `NotImplementedError`, wrapper catches, logs INFO,
  bumps `outcome="skipped"` metric. The operator notification still
  went through Telegram + Discord, so manual posting is still
  possible.
- **Generated text wraps in straight quotes** — stripped by the
  startswith/endswith check. Smart quotes (`"`/`"`) are NOT
  stripped, so a stylized model can leak them through.

## Common ops

- **Enable Bluesky + Mastodon posting:**
  ```bash
  poindexter set social_distribution_platforms "bluesky,mastodon"
  ```
  Make sure the corresponding adapter credentials are configured
  in `app_settings` first.
- **Switch to a different Ollama model for social copy:**
  ```bash
  poindexter set social_poster_model "ollama/qwen3:8b"
  ```
  Restart the worker (see status callout — module-level
  capture means no hot reload).
- **Check adapter health:** Prometheus counters
  `poindexter_social_adapter_posts_total{platform,outcome}` and
  `poindexter_social_adapter_errors_total{platform}` — visible on
  the social/ops Grafana dashboard.
- **Test generation without distribution:** import
  `generate_social_posts` and call it directly — it doesn't notify
  or hit adapters, just returns the `SocialPost` list.
- **See what would be posted for an existing slug:** call
  `generate_social_posts(title=..., slug=<existing-slug>,
excerpt=..., keywords=[...])` from a one-off shell.

## See also

- `docs/architecture/services/publish_service.md` — the caller that
  triggers social posting after publish.
- `services.social_adapters.*` — per-platform posting modules
  (Bluesky, Mastodon real; LinkedIn/Reddit/YouTube stubs per GH-40).
- `services.ollama_client` — local LLM client used for generation.
- `services.telegram_config` — Telegram bot token + chat plumbing.
