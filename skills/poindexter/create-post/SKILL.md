---
name: create-post
description: Create a new blog post or content task about a given topic. Use when the user says "write a post about", "create content about", "draft an article on", or similar.
---

# Create Post

Creates a new content task via `POST /api/tasks`. Pending tasks are dispatched by
**Prefect** (`services/flows/content_generation.py`) and run through the
`canonical_blog` pipeline — an 18-node `graph_def` stored in the
`pipeline_templates` table (authored in `services/canonical_blog_spec.py`,
compiled by `services/pipeline_architect.py`):

1. `verify_task` — task record exists and is processable
2. `generate_content` — writer model drafts the post (may use a RAG mode)
3. `writer_self_review` — writer re-reads and tightens its own draft
4. `resolve_internal_link_placeholders` — closes leaked `[posts/<slug>]` markers
5. `quality_evaluation` — early pattern-QA gate (scoring + truncation detection)
6. `url_validation` — live-check external URLs cited in the draft
7. `replace_inline_images` — generates each inline image via the SDXL host server
8. `source_featured_image` — sources the featured image (falls back to Pexels)
9. **qa.\* rail block** — `qa.critic` → `qa.deepeval` → `qa.guardrails` → `qa.ragas` → `qa.aggregate`: multi-model QA (adversarial critic + DeepEval + guardrails-ai + Ragas rails); `qa.aggregate` makes the gate decision. Replaced the deleted `cross_model_qa` stage (#355)
10. `generate_seo_metadata` — title, description, slug, keywords
11. `generate_media_scripts` — podcast script + video scene breakdown
12. `generate_video_shot_list` — per-scene video shot list
13. `capture_training_data` — snapshot for the feedback loop
14. `finalize_task` — mark `awaiting_approval` (or auto-publish if score ≥ threshold)

The writer model is **DB-configurable** via cost tiers
(`cost_tier.{free,budget,standard,premium}.model` in `app_settings`), not hardcoded.

Tasks that fail QA are rejected — `qa.aggregate` halts the graph on reject (no
automatic rewrite loop as of #355) and the post lands with a loud reason on the
`/pipeline` dashboard.

## Usage

```bash
scripts/run.sh "topic" [category] [target_audience] [primary_keyword]
```

## Parameters

- **topic** (string, optional): the subject of the post. Be specific — vague topics produce mediocre content and are more likely to trip the brand filter. Required unless `seed_url` is provided. Length: 3–200 characters.
- **seed_url** (string, optional): a URL to seed the topic from. When present, the handler fetches the URL, extracts the title and opening paragraph, uses the title as the topic if `topic` is omitted, and injects a "Source article" attribution block into the writer's research context. The brand filter is bypassed for URL-seeded tasks. One of `topic` or `seed_url` is required (HTTP 422 otherwise). If the fetch fails (404, login wall, timeout, oversize response, non-HTML content type), the API returns HTTP 400 with an `error: "seed_url_fetch_failed"` body and a human-readable `reason`. Max length: 2048 characters.
- **category** (string, optional): `technology`, `business`, `marketing`, etc. Defaults to `general`.
- **target_audience** (string, optional): `developers`, `founders`, `AI enthusiasts`. Defaults to `general`.
- **primary_keyword** (string, optional): SEO keyword. The SEO stage will use it as the preferred anchor.

The fetch behavior is tunable via `app_settings`: `seed_url_fetch_timeout_seconds` (default 10s), `seed_url_user_agent`, and `seed_url_max_bytes` (default 1 MiB).

## Output

Returns the task record with its `id` (UUID), `status: pending`, and metadata. When `seed_url` was provided, the URL is preserved on the task (and round-tripped via `task_metadata.seed_url`) so the writer can attribute the source. Track progress with `list-tasks` or the `/pipeline` dashboard.
