# Content-type classification

An additive, **multi-label** axis describing _what a published post is about_,
independent of the niche taxonomy (which drives topic generation) and the
`tags` folksonomy (LLM-generated, noisy). Each published post is labelled with
0..N content-types from a DB-configurable set, stored in a dedicated table, and
consumed by distribution gates and dashboards.

## Why a separate axis

`niches` model _where a post belongs in the generation pipeline_ and are
coarse; `tags` are free-form and inconsistent. Neither can reliably answer "is
this post about AI/ML vs. gaming vs. hardware?" — a question distribution
surfaces need. Content-types are a clean, closed, DB-tunable vocabulary layered
_on top of_ published posts, so they stay descriptive (a label) rather than
becoming another generation driver.

## Data model

`post_content_types` (one row per post per label):

| column         | notes                                        |
| -------------- | -------------------------------------------- |
| `post_id`      | FK → `posts(id)` `ON DELETE CASCADE`         |
| `content_type` | a label from `content_type_labels`           |
| `confidence`   | 0..1 (classifier's self-reported confidence) |
| `source`       | `classifier` \| `manual` \| ...              |

`UNIQUE (post_id, content_type)` keeps it a set. Multi-label: a post about
running LLMs on a specific GPU is both `ai-ml` **and** `pc-hardware`.

## The classifier job

`services/jobs/classify_content_types.py::ClassifyContentTypesJob` — a
post-publish sweep (`every 6 hours`). Each run classifies up to `batch_size`
(default 10) published posts that have **no** `post_content_types` row yet, so
it backfills the existing corpus and keeps up with new posts through one code
path. Idempotent — a labelled post is never re-fetched.

- **Model:** local, resolved via `structured_extraction_model` (JSON output, so
  never a reasoner). Pin an override with `content_type_classifier_model`.
- **Prompt:** the SKILL.md pack `skills/content/content-type-classifier/SKILL.md`
  (key `content.classify_content_type`), edited in-repo like every prompt.
- **Input:** post title + a bounded content excerpt + the post's existing tags
  (a _hint_ — never the source of truth).
- **Output guardrail:** deterministic. The LLM's JSON is parsed and filtered to
  keep **only** labels in `content_type_labels`; unknown labels are dropped,
  confidences clamped. A post the model can't classify gets **zero** rows — no
  silent default label.

## Settings (`app_settings`)

| key                              | default                                                      | meaning                                           |
| -------------------------------- | ------------------------------------------------------------ | ------------------------------------------------- |
| `content_type_labels`            | `ai-ml,pc-hardware,gaming,software-engineering,founder-meta` | the allowed label set (classifier output).        |
| `content_type_classifier_model`  | `""`                                                         | model pin; empty → `structured_extraction_model`. |
| `classify_content_types_enabled` | `true`                                                       | master switch for the job.                        |

Customizing `content_type_labels` should be paired with an edit to the
SKILL.md definitions so the classifier's guidance matches the new set.

## Consumers

- **Dev.to selective syndication** (`services/jobs/crosspost_to_devto.py`) —
  filters candidates on `devto_syndicate_content_types` (a CSV allowlist) via a
  `post_content_types` EXISTS, so an operator can syndicate e.g. `ai-ml` +
  `founder-meta` and exclude the rest. A post with no content-type row is
  fail-closed out (never syndicated). See the job's docstring.
- **Grafana** — a "Content-type distribution" row on the Cost & Analytics board
  (`/d/cost-analytics`): posts-per-label table, labelled-post count, and the
  unlabelled-published backlog (the classifier's remaining work).

## Adding a consumer

Read `post_content_types` directly. For "posts of type X", join
`EXISTS (SELECT 1 FROM post_content_types pct WHERE pct.post_id = <post> AND pct.content_type = ANY(...))`.
The table makes per-type facets (RAG filters, per-type analytics,
topic-balancing) cheap — none are wired yet by design.
