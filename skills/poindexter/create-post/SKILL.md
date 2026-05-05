---
name: create-post
description: Create a new blog post or content task about a given topic. Use when the user says "write a post about", "create content about", "draft an article on", or similar.
---

# Create Post

Creates a new content task via `POST /api/tasks`. The task is immediately picked up
by the worker loop (task_executor.py) and run through the full multi-stage pipeline
defined in `services/content_router_service.py`:

1. `verify_task` — task record exists and is processable
2. `generate_content` — writer model drafts the post (currently `gemma3:27b`)
3. `quality_evaluation` — early pattern-QA gate (regex validator + scoring)
4. `url_validation` — live-check external URLs cited in the draft
5. `replace_inline_images` — SDXL Lightning generates each inline image via the host server on port 9836
6. `source_featured_image` — SDXL generates the featured image (falls back to Pexels if SDXL is down)
7. `cross_model_qa` — multi-model QA aggregator: `programmatic_validator`, `ollama_critic` (`qwen3.5:35b`), `topic_delivery`, `internal_consistency`, `image_relevance`, `rendered_preview`
8. `generate_seo_metadata` — title, description, slug, keywords
9. `generate_media_scripts` — podcast script + video scene breakdown
10. `capture_training_data` — snapshot for the feedback loop
11. `finalize_task` — mark as `awaiting_approval` and notify Discord

Tasks that fail the cross_model_qa gate get up to `qa_max_rewrites` (default 2) automatic rewrite attempts before being rejected with a loud reason on the `/pipeline` dashboard.

The upfront brand filter in `task_executor.py` rejects off-brand topics (not matching AI/ML, gaming, or PC hardware keywords) before the pipeline starts — expect some topics to be rejected immediately with an "off-brand" message.

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
