# Social Poster

**File:** `src/cofounder_agent/services/social_poster.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_social_poster.py`
**Last reviewed:** 2026-07-10

## What it does

`social_poster` is a **pure copy generator**. It produces platform-specific
social-media copy (X/Twitter, LinkedIn, and the X-style short-form variants
Bluesky + Mastodon) for a published blog post using the local LLM.

It does **not** distribute anything. `generate_social_posts` returns
`SocialPost` objects that the `social.generate_drafts` pipeline atom turns into
`social_post_drafts` rows. Those drafts are reviewed (`poindexter social list`
/ `poindexter social approve`) and pushed to each platform through **Postiz**
(`services.social_drafts` → `services.integrations.postiz_client`).

> The legacy in-process distribution path — `generate_and_distribute_social_posts`,
> the per-platform `services/social_adapters/*` dispatch, and the Telegram/Discord
> "social post ready" notifications — was retired 2026-06-29 when Postiz became the
> distribution mechanism (PR #2001).

## Public API

- `await generate_social_posts(title, slug, excerpt, keywords=None, ollama=None, *, site_config) -> list[SocialPost]`
  — the only public entry. Builds prompts, calls the LLM once for the Twitter
  copy (reused for Bluesky + Mastodon) and once for LinkedIn, and returns a
  `SocialPost` per platform. `site_config` is a **required keyword arg** (#272
  Phase-2e — there is no module-level singleton). A platform whose generation
  fails is logged and omitted from the returned list (non-fatal).

### `SocialPost` dataclass

`platform` (`"twitter"` | `"linkedin"` | `"bluesky"` | `"mastodon"`), `text`,
`post_url` (the blog URL being promoted), `created_at` (UTC default), `posted`
(`False` until a Postiz draft for it is posted).

## Key behaviors

- **The promoted URL is the PREDICTED publish URL, verified at approve.**
  On `canonical_blog` the posts row does not exist when drafts are generated
  (`content.persist_task` produces `post_slug=None` — posts are created on
  approve), so the `social.generate_drafts` atom derives the final slug
  through `publish_service.derive_publish_identity` — the exact chain
  `publish_post_from_task` runs at publish time (H1 from content → title →
  topic, sanitized, + `-{task_id[:8]}`). `SocialDraftsService.approve_draft`
  then enforces correctness at the last gate before the copy goes public:
  it refuses to push unless the linked post exists **and is live**
  (`posts.status='published'`, resolved via the
  `posts.metadata->>'pipeline_task_id'` seam), rewrites any stale/dead
  `…/posts/…` URL in the copy to the live slug, links `post_id`, and stamps
  `approved_at`. (Before 2026-07-10 the atom read the always-`None`
  `post_slug` from state, so every draft promoted the dead `…/posts/`
  index URL — and approve pushed it as-is.)
- **Twitter copy is reused for Bluesky + Mastodon.** Both are X-style
  short-form (Bluesky 300, Mastodon 500 chars), so the ≤280-char tweet fits
  both — no separate prompt or extra LLM call. The `social.generate_drafts`
  atom filters the returned list down to whatever `social_draft_platforms`
  actually requests.
- **Draft creation is idempotent per `(pipeline_task_id, platform,
subreddit)`** (poindexter#833). Finalize re-runs (preview_gate regen loops,
  checkpoint restore, task retry) used to stack a fresh draft per platform on
  every pass — one task posted the same Bluesky promo three times. Now the
  `social.generate_drafts` atom pre-filters keys that already carry an active
  (`pending`/`failed`) or `posted` draft before generating any copy (a replay
  spends zero LLM calls), and `SocialDraftsService.create_draft` enforces the
  same rule DB-side: a `NOT EXISTS` guarded insert with the
  `ux_social_post_drafts_active_key` partial unique index (active rows only)
  as the race backstop, returning the existing draft's id on skip. A
  `rejected`-only key deliberately inserts fresh — reject + regen means "offer
  new copy". `RetryFailedSocialDraftsJob` is untouched: the index spans
  `pending`+`failed` together, so its failed→pending reset never collides.
- **Per-call config reads.** Every tunable (`social_twitter_char_limit`,
  `social_linkedin_char_limit`, `social_poster_max_tokens`, the model) is read
  at call time from the injected `SiteConfig`, so a `poindexter settings set`
  takes effect without a worker restart (#185 removed the old import-time
  capture).
- **Prompts are DB-configurable.** `_build_twitter_prompt` /
  `_build_linkedin_prompt` resolve `social.twitter_promote` /
  `social.linkedin_promote` through `UnifiedPromptManager`, falling back to the
  inline constants if the manager is unavailable (per
  `feedback_prompts_must_be_db_configurable`).
- **Production path routes through `dispatch_complete`.** When the injected
  `SiteConfig` carries a DB pool, generation goes through the configured LLM
  provider (cost tracking, Langfuse traces, provider-swappability). A
  test/bootstrap fallback delegates to `OllamaClient` directly so the suite
  runs without a live DB.
- **`think=False` + `<think>` stripping.** Social copy is short, so the
  reasoning phase is disabled and any residual `<think>...</think>` block is
  stripped — the draft must never surface the model's analysis.
- **Deterministic copy repair (`_polish_social_copy`).** Every generated
  draft passes through a repair net before it becomes a row: strip a dangling
  ellipsis trail-off, lift the post URL out of the text and re-append it LAST
  (so a trim can never eat the link — the 2026-08-16 Cosine-Similarity
  regression), then fit the prose to `char_limit − len(url) − 1`. The fit
  prefers the **last sentence boundary** that fits and only falls back to a
  word-boundary cut when no whole sentence fits — an overrunning draft loses
  its final sentence whole instead of shipping a mid-clause fragment
  ("…larger memories automatically create <URL>", the 2026-08-26
  truncated-drafts report). Wrapping straight quotes the model adds are
  stripped before counting. The prompts also hand the model the prose budget
  explicitly (`{url_chars}` / `{prose_budget}`): slugged URLs run ~90 chars —
  a third of a tweet — and models reliably fail to derive that subtraction
  from "under 280 including the URL" (roughly half of all drafts overran
  before the budget was spelled out).

## Configuration

All from `app_settings` via the injected `SiteConfig`:

- `social_poster_fallback_model` — model used for generation. **Fail-loud**: if
  unset, `_resolve_social_model` notifies the operator and raises (per
  `feedback_no_silent_defaults`). The `ollama/` prefix is stripped before the
  call. (The old `cost_tier.*` tier fallback was removed.)
- `social_poster_max_tokens` (default `300`) — `num_predict` cap. Social copy
  rarely needs more than ~100 tokens.
- `social_twitter_char_limit` (default `280`) — Twitter/Bluesky/Mastodon cap.
- `social_linkedin_char_limit` (default `700`) — LinkedIn cap (LinkedIn's
  actual limit is 3000; the prompt asks for newsletter-friendly brevity).
- `site_url` — used to build the `post_url`.
- `company_name` — injected into the prompt as the speaker.

## Dependencies

- **Reads from:**
  - the injected `SiteConfig` — all configuration above.
  - `services.llm_providers.dispatcher.dispatch_complete` — production
    generation path.
  - `services.ollama_client.OllamaClient` — test/bootstrap fallback path.
  - `services.prompt_manager` (`UnifiedPromptManager`) — DB-configurable prompts.
  - `services.llm_providers.thinking_models.strip_think_blocks` — reasoning-leak
    defense.
- **Callers:**
  - the `social.generate_drafts` pipeline atom — builds a run-bound `SiteConfig`
    from the container and turns the returned `SocialPost`s into
    `social_post_drafts` rows.

## See also

- `services.social_drafts` / `services.integrations.postiz_client` — the
  distribution path (drafts → approve → Postiz).
- [`docs/architecture/social-post-scheduling.md`](../social-post-scheduling.md)
  — the local schedule queue that times those drafts (manual + auto-drip),
  and why it isn't Postiz's queue.
- `docs/architecture/services/publish_service.md` — the publish flow that
  precedes social-draft generation.
- `services.ollama_client` — local LLM client used on the fallback path.
