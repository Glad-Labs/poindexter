# Community draft assistant

An on-demand, draft-only assistant that generates **native, community-appropriate,
founder-voice** posts for engagement surfaces (Reddit today; IndieHackers is a
planned follow-up). It never auto-posts — the operator reviews each draft and
posts it manually — so there is zero account-ban surface. This PR ships the
Reddit generator plus the shared profile/draft/CLI substrate.

## Why a separate surface

The existing social distribution (`social_post_drafts` + the Postiz promo atom)
produces link-drop promo copy — "check out my new post". That is exactly what
gets an account shadow-banned on Reddit and ignored on IndieHackers. Those
communities reward genuine, specific value, and every subreddit has different
rules, self-promotion tolerance, and post-type norms. The community draft
assistant is a different content model (native value-posts, not link-drops) with
a different lifecycle (manual post, blog-post-optional), so it gets its own
tables rather than bending the Postiz publish-gate machinery.

## Data model — two tables

**`subreddit_profiles`** — the per-community config that makes a draft fit:

| column                 | notes                                                         |
| ---------------------- | ------------------------------------------------------------- |
| `subreddit`            | unique, no `r/` prefix                                        |
| `enabled`              | excluded from suggestions when false                          |
| `content_types`        | `text[]` — the classifier labels this sub accepts (match key) |
| `post_type`            | `text` \| `link` \| `either`                                  |
| `self_promo`           | `strict` \| `moderate` \| `ok` (advisory)                     |
| `flair`                | default flair to set (advisory, nullable)                     |
| `min_karma`            | advisory gate (nullable)                                      |
| `min_account_age_days` | advisory gate (nullable)                                      |
| `rules_summary`        | rules + culture, fed to the LLM                               |
| `tone_notes`           | how to write for this sub, fed to the LLM                     |
| `cadence_cap_days`     | advisory "don't post here more than once per N days"          |

The gates (`self_promo`, `min_karma`, `min_account_age_days`, `cadence_cap_days`)
are **advisory** — surfaced to the operator as warnings on the draft, never
enforced (the operator is the poster). `content_types` is the match key against
the published-post content-type axis (`post_content_types`).

**`community_post_drafts`** — the draft store (lifecycle distinct from
`social_post_drafts`):

| column           | notes                                                    |
| ---------------- | -------------------------------------------------------- |
| `target`         | `reddit:<sub>` \| `indiehackers`                         |
| `title` / `body` | the draft content                                        |
| `post_type`      | resolved from the profile                                |
| `source_post_id` | FK → `posts(id)` `ON DELETE SET NULL` (nullable)         |
| `warnings`       | `text[]` — advisory constraints surfaced to the operator |
| `status`         | `draft` \| `posted` \| `discarded`                       |
| `posted_url`     | the permalink the operator pastes back after posting     |
| `model`          | which model generated it (provenance)                    |

There is no idempotency key: on-demand generation is a deliberate operator
action, so a second draft for the same post + subreddit is a _new take to
compare_, not a duplicate to suppress.

## Generation

`services/community_drafts.py::generate_reddit_draft` loads the published post
(title + content) and its content-type labels, loads the target profile, renders
the SKILL.md prompt, calls the local writer model for the post, and stores a
draft. Three things are computed **deterministically** rather than left to the
LLM, which keeps them testable and predictable:

- **Title / body split** (`split_title_and_body`) — a Reddit post has two fields,
  so the prompt has the model lead with a `Title:` line; the parse lifts that
  into the draft's `title` column and strips it from the body, giving the
  operator two clean, ready-to-paste fields instead of a title buried in the
  body. Falls back to the source post's title (body untouched) when the model
  emits no recognizable title line, so a genuine first line of prose is never
  eaten.
- **Warnings** (`compute_warnings`) — derived from the profile:
  `self_promo=strict` → "post as native text, no blog link"; a set `flair` →
  "set flair: …"; `min_karma` / `min_account_age_days` → gate reminders;
  `cadence_cap_days` → a cadence reminder.
- **Blog link** (`maybe_append_blog_link`) — the canonical post URL is appended
  **only** when the subreddit's `post_type` is `link`/`either` **and** a base
  URL is configured. In a text-only sub it is never added, and an unconfigured
  base URL never produces a broken link. The LLM is instructed to lead with a
  `Title:` line then the body, and never to write a URL.

- **Model:** the local writer model (prose, so writer-grade — not the
  structured-extraction model). Pin an override with `community_draft_model`.
- **Prompt:** the SKILL.md pack `skills/community/reddit-value-post/SKILL.md`
  (key `community.reddit_value_post`), edited in-repo like every prompt.

## Content-type matching

`community draft reddit <post>` with no `--subreddit` runs a deterministic match
(no LLM for routing): enabled profiles whose `content_types` array **overlaps**
(`&&`) the post's `post_content_types` labels. The operator picks from the
suggested set — the tool never auto-fans-out across every match. A post the
classifier hasn't labelled yet suggests nothing, so the operator passes
`--subreddit` explicitly.

## Settings (`app_settings`)

| key                               | default | meaning                                             |
| --------------------------------- | ------- | --------------------------------------------------- |
| `community_draft_model`           | `""`    | model pin for generation; empty → the writer model. |
| `community_draft_timeout_seconds` | `180`   | per-call LLM timeout for draft generation.          |

Subreddit targeting is structured multi-field config, so it lives in the
`subreddit_profiles` table (operator-curated), not a scalar setting. The table
ships **empty** — an operator adds their own profiles.

## CLI (`poindexter community`)

The primary interface — thin adapters over the service layer (no inline SQL):

- `community profiles list|show|add|edit|enable|disable|rm|import-csv|export-csv` —
  full CRUD plus a spreadsheet round-trip. `import-csv` is insert-only by
  default (skips existing rows) unless `--force` (updates in place); a malformed
  row is reported as an error and the batch continues. `content_types` is a
  `;`-delimited cell so it survives one CSV column; `export-csv` uses the same
  layout, so the loop is export → edit in a spreadsheet → re-import.
- `community draft reddit <post> [--subreddit X]` — generate a value-post for a
  published post; without `--subreddit`, print the content-type-matched
  suggestions and stop.
- `community drafts list|show|edit|mark-posted|discard` — review, then record the
  permalink once posted manually.

## Planned follow-up

An IndieHackers build-log generator (`community draft ih`) sourced from real
system state (shipped changes, decisions, metrics over a lookback window) reuses
this table + CLI group + review path. It is intentionally out of scope here.
