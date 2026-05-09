# Prompt management — UnifiedPromptManager + Langfuse

**Status:** Active. Lane A of `Glad-Labs/poindexter#450` (the OSS migration umbrella) completed 2026-05-09. Production no longer carries inline prompt constants.

## Where prompts live

The single source of truth for every production prompt is **YAML files** under `src/cofounder_agent/prompts/*.yaml`, accessed via the `UnifiedPromptManager` cascade. There are exactly three places a prompt body can come from at runtime:

1. **Langfuse `production` label** — operator's preferred edit surface. Cached in-process for ~60s; edits in the Langfuse UI take effect on the next `get_prompt()` call without a worker restart.
2. **YAML default** — what ships with the repo. The OSS-friendly fallback when Langfuse isn't configured (or the lookup fails). Edits land via PR.
3. **`KeyError`** — if neither source has the key. Per `feedback_no_silent_defaults.md`, an unknown key is a configuration bug, not a quiet fallback.

## The cascade

```
caller: prompt = pm.get_prompt("qa.topic_delivery", topic=t, opening=o)
                    ↓
        UnifiedPromptManager.get_prompt(key, **kwargs)
                    ↓
        ┌──────────────────────────────┐
        │ 1. Langfuse client            │
        │    .get_prompt(name=key,      │
        │     label="production")        │
        │    → returns template if hit  │
        └──────────────────────────────┘
                    ↓ (if None or error)
        ┌──────────────────────────────┐
        │ 2. self.prompts[key]          │
        │    (loaded from YAML at boot) │
        │    → returns template         │
        └──────────────────────────────┘
                    ↓ (if KeyError)
                  raises
```

Both paths feed the same `template.format(**kwargs)` step at the end, so the call-site contract (kwargs in, formatted string out) is identical regardless of which tier wins.

## Available prompt keys

The seven prompts migrated by Lane A are now keys in the YAML files. Plus the prompts that already lived in YAML pre-Lane-A:

### `prompts/system.yaml`

- `system.content_writer` — base persona prompt for blog content generation
- `narrative.system` — persona seed for the deterministic-compositor narrative pass

### `prompts/blog_generation.yaml`

- `blog_generation.initial_draft`
- `blog_generation.seo_and_social`
- `blog_generation.iterative_refinement`
- `blog_generation.blog_system_prompt`
- `blog_generation.blog_generation_request`

### `prompts/content_qa.yaml`

- `qa.content_review`
- `qa.self_critique`
- `qa.topic_delivery` — added by Lane A; checks whether body delivers on the topic the title promised
- `qa.consistency` — added by Lane A; internal-contradiction check across recommendations / facts / principles / code-vs-prose
- `qa.review` — added by Lane A; overall publication-readiness review (the third LLM critic)
- `qa.aggregate_rewrite` — added by Lane A; the rewrite prompt that fires when QA rejects a draft and the writer model gets one chance to fix the issues

### `prompts/research.yaml`

- `research.analyze_search_results`
- `topic.ranking` — added by Lane A; topic-candidate scoring against operator-weighted goals

### `prompts/image_generation.yaml`

- `image.featured_image`
- `image.search_queries`
- `image.decision` — added by Lane A; image director (analyzes article + decides what images go where)

### `prompts/seo_metadata.yaml`

- `seo.generate_title` / `seo.generate_meta_description` / `seo.extract_keywords` / `seo.generate_excerpt` / `seo.match_category` / `seo.extract_tags`

### `prompts/social_media.yaml`

- `social.research_trends` / `social.create_post`

### `prompts/tasks.yaml`

- `task.creative_blog_generation` / `task.qa_content_evaluation` / `task.business_financial_impact` / `task.business_market_analysis` / `task.business_performance_analysis` / `task.automation_email_campaign` / `task.content_summarization` / `task.utility_json_conversion`

### `prompts/atoms.yaml`

- `atoms.narrate_bundle.system_prompt` / `atoms.review_with_critic.system_prompt` / `atoms.pipeline_architect.system_prompt`

## How operators tune prompts

### Tier 1 — Langfuse UI (preferred)

1. Open Langfuse: `<langfuse_host>/project/poindexter/prompts`
2. Find the prompt by key (e.g. `qa.topic_delivery`)
3. Edit the body. Save with the `production` label.
4. Next `get_prompt()` call (within ~60s) picks up the new body.

Langfuse versions every change. Roll back by promoting an older version to `production`.

### Tier 2 — YAML edit (when Langfuse isn't an option)

1. Edit `src/cofounder_agent/prompts/<file>.yaml`.
2. Commit. The container picks up the new body on next deploy / restart.
3. The snapshot test for that prompt will fail if the body changed — update the snapshot in the same PR.

The Lane A snapshot tests (in `tests/unit/services/test_*_prompts.py`) pin every rendered body byte-for-byte. They serve two purposes:

- **Drift detection.** A prod-side Langfuse edit that strays from the YAML default lands on the dashboard but ALSO causes the snapshot test to fail in CI. The operator is forced to either revert the Langfuse edit or update the snapshot consciously.
- **Migration safety.** When Lane A migrated f-string prompts to YAML, the snapshot test verified the YAML-rendered body matches the f-string-rendered body byte-for-byte. Format-string contract gotchas (literal `{{` `}}` doubling, trailing newlines) couldn't sneak through.

## How callers fetch prompts

```python
from services.prompt_manager import get_prompt_manager

pm = get_prompt_manager()  # cached singleton

prompt = pm.get_prompt(
    "qa.topic_delivery",
    topic="Why local LLMs win",
    opening="In 2026, the case for local AI..."
)
```

**Sync API** — even though Langfuse is involved, the call path doesn't await. The Langfuse client caches in-process and the call returns the cached version immediately. First-call latency is the only awaited piece, and that's wrapped synchronously inside the manager.

**Format string semantics** — kwargs are substituted via `str.format(**kwargs)`. Literal braces in the prompt body must be doubled (`{{` / `}}`); the YAML files preserve the doubling. f-strings in source code happen to use the same doubling rule, which is why the migration snapshots came out identical.

## YAML format

```yaml
- key: qa.topic_delivery # dot-namespaced; first segment = surface
  category: content_qa # matches the file's surface
  description: 'QA gate — does the body deliver on the topic the title promised'
  output_format: json # 'json' | 'text' — for downstream parsing
  template: | # block scalar; preserves newlines verbatim
    You are a strict editor checking whether an article
    delivers on its topic. ...

    REQUESTED TOPIC: {topic}

    ARTICLE OPENING (first ~1000 words):
    {opening}

    Respond with ONLY valid JSON:
    {{"delivers": true/false, "score": NUMBER 0-100, "reason": "..."}}
```

Use `|` (literal block) — not `>` (folded) — when the prompt has meaningful newlines (almost always). Use `|-` if you need to strip the trailing newline (matches a Python triple-quoted string that doesn't end with `\n`). Snapshots will tell you which form to use; pick whichever matches the source byte-for-byte.

## Why Langfuse + YAML, not just one

- **YAML alone:** edits require a PR + CI + deploy. Operators tuning prompts at 11pm on a Saturday don't want to touch Python.
- **Langfuse alone:** an OSS fork operator who hasn't set up Langfuse has nothing to fall back on.
- **Both:** YAML ships as the OSS-friendly default; Langfuse is the operator's edit surface; snapshot tests prevent silent drift.

## Related docs

- `Glad-Labs/poindexter#450` — OSS migration umbrella (Lane A)
- `Glad-Labs/poindexter#47` — the original UnifiedPromptManager migration
- `docs/reference/app-settings.md` — `langfuse_host`, `langfuse_public_key`, `langfuse_secret_key`, `langfuse_tracing_enabled`
- `docs/architecture/cost-tier-routing.md` — Lane B's sibling migration (model selection, not prompt selection)
