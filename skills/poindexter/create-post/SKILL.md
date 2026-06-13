---
name: create-post
description: Create a new blog post or content task about a given topic. Use when the user says "write a post about", "create content about", "draft an article on", or similar.
---

# Create Post

Creates a new content task via `POST /api/tasks`. Pending tasks are dispatched by
**Prefect** (`services/flows/content_generation.py`) and run through the
`canonical_blog` pipeline — a 36-node `graph_def` stored in the
`pipeline_templates` table (authored in `services/canonical_blog_spec.py`,
compiled by `services/pipeline_architect.py`). The chain runs linearly through
six blocks:

1. **verify** — `verify_task`: task record exists and is processable.
2. **writer** — `generate_draft` → `generate_title` → `check_title_originality`
   → `normalize_draft` → `draft_gate` (optional HITL, seeded off) →
   `writer_self_review` → `resolve_internal_link_placeholders` →
   `reconcile_citations` (deterministic citation repair, #765).
3. **quality + images** — `quality_evaluation` (pattern-QA gate) →
   `url_validation` → `plan_image_markers` → `generate_images` →
   `inject_images` → `source_featured_image` → `caption_images` (vision alt-text).
4. **qa.\* rail block** (12 atoms) — `qa.programmatic` → `qa.critic` →
   `qa.deepeval` → `qa.ragas` → `qa.vision` → `qa.topic_delivery` →
   `qa.citations` → `qa.unlinked_attribution` → `qa.consistency` →
   `qa.self_consistency` → `qa.web_factcheck` → `qa.aggregate`: multi-model QA
   delegating to `multi_model_qa.py`; `qa.aggregate` makes the gate decision
   (halts on reject, no rewrite loop). Replaced the deleted `cross_model_qa`
   stage (#355).
5. **seo + media** — `seo.generate_all_metadata` (single structured call, #734)
   → `generate_media_scripts` → `generate_video_shot_list` →
   `capture_training_data`.
6. **finalize** — `compile_meta` → `persist_task` → `record_pipeline_version` →
   `evaluate_auto_publish`: mark `awaiting_approval` (or auto-publish if
   score ≥ threshold).

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
