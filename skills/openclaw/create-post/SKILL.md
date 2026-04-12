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

- **topic** (required): the subject of the post. Be specific — vague topics produce mediocre content and are more likely to trip the brand filter.
- **category** (optional): `technology`, `business`, `marketing`, etc. Defaults to `general`.
- **target_audience** (optional): `developers`, `founders`, `AI enthusiasts`. Defaults to `general`.
- **primary_keyword** (optional): SEO keyword. The SEO stage will use it as the preferred anchor.

## Output

Returns the task record with its `id` (UUID), `status: pending`, and metadata. Track progress with `list-tasks` or the `/pipeline` dashboard.
