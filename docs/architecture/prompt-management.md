# Prompt management — UnifiedPromptManager + Langfuse

**Status:** Active. Lane A of `Glad-Labs/poindexter#450` (the OSS migration umbrella) completed 2026-05-09 — production no longer carries inline prompt constants. The YAML prompt files were subsequently migrated to agentskills.io `SKILL.md` packs (`Glad-Labs/poindexter#528`); no `prompts/*.yaml` files ship anymore.

## Where prompts live

The single source of truth for every production prompt is a set of **`SKILL.md` packs** under `src/cofounder_agent/skills/<pack>/<skill>/SKILL.md`, accessed via the `UnifiedPromptManager` cascade. There are exactly three places a prompt body can come from at runtime:

1. **Langfuse `production` label** — the operator's preferred edit surface. Cached in-process for ~60s; edits in the Langfuse UI take effect on the next `get_prompt()` call without a worker restart.
2. **`SKILL.md` pack default** — what ships with the repo (the in-memory default loaded at boot). The OSS-friendly fallback when Langfuse isn't configured (or the lookup fails). Edits land via PR.
3. **`KeyError`** — if neither source has the key. Per `feedback_no_silent_defaults.md`, an unknown key is a configuration bug, not a quiet fallback.

> The legacy `prompts/*.yaml` loader (`_initialize_prompts`) still runs at boot for backward compatibility, but the repo ships zero YAML prompt files — every key now comes from a `SKILL.md` pack via `_initialize_skills`. Skills load _after_ YAML so a migrated `SKILL.md` transparently wins over any leftover YAML entry for the same key.

## The cascade

```
caller: prompt = pm.get_prompt("qa.topic_delivery", topic=t, opening=o)
                    ↓
        UnifiedPromptManager.get_prompt(key, **kwargs)
                    ↓
        ┌──────────────────────────────┐
        │ 1. Langfuse client            │
        │    .get_prompt(name=key,      │
        │     label="production")       │
        │    → returns template if hit  │
        └──────────────────────────────┘
                    ↓ (if None or error)
        ┌──────────────────────────────┐
        │ 2. self.prompts[key]          │
        │    (in-memory default, loaded │
        │     from SKILL.md packs at boot) │
        │    → returns template         │
        └──────────────────────────────┘
                    ↓ (if KeyError)
                  raises
```

Both paths feed the same `template.format(**kwargs)` step at the end, so the call-site contract (kwargs in, formatted string out) is identical regardless of which tier wins.

## Available prompt keys

Each `SKILL.md` pack declares the keys it provides in its frontmatter `metadata.prompts` list — **that frontmatter is the authoritative key inventory** (the loader registers exactly those keys). The packs that ship today:

| Pack (`skills/<pack>/<skill>/SKILL.md`)                       | `metadata.category` | Surface                                                                             |
| ------------------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------------- |
| `content/writer`                                              | content             | base content-writer persona + narrative-pass seed                                   |
| `content/two-pass-writer`                                     | content             | TWO_PASS draft/revise prompts                                                       |
| `content/blog-generation`                                     | content             | initial draft / SEO+social / iterative refinement                                   |
| `content/content-qa`, `content/qa`                            | content_qa          | the `qa.*` review, critique, topic-delivery, consistency, aggregate-rewrite prompts |
| `content/research`                                            | research            | search-result analysis + topic-candidate ranking                                    |
| `content/image-generation`                                    | image               | featured-image, search-query, and image-director prompts                            |
| `content/seo-metadata`                                        | seo_metadata        | `seo.*` title / description / keywords / excerpt / category / tags                  |
| `content/social-media`                                        | social              | trend research + post creation                                                      |
| `content/podcast`, `content/video`, `content/video-director`  | media               | media-script and shot-list prompts                                                  |
| `content/atoms`                                               | content             | `atoms.*` system prompts (narrate_bundle, review_with_critic, pipeline_architect)   |
| `content/utility`                                             | utility             | content summarization / JSON conversion helpers                                     |
| `ops/automation`, `ops/business`, `ops/triage`, `ops/hygiene` | ops                 | `task.*` business/automation/ops prompts                                            |

The `narrate_bundle` and `pipeline_architect` templates carry the operator persona as `{site_name}` / `{site_url}` placeholders, rendered from the run-bound `site_config` by the calling atom before the text reaches the model.

## How operators tune prompts

### Tier 1 — Langfuse UI (preferred)

1. Open Langfuse: `<langfuse_host>/project/poindexter/prompts`
2. Find the prompt by key (e.g. `qa.topic_delivery`)
3. Edit the body. Save with the `production` label.
4. Next `get_prompt()` call (within ~60s) picks up the new body.

Langfuse versions every change. Roll back by promoting an older version to `production`.

### Tier 2 — `SKILL.md` edit (when Langfuse isn't an option)

1. Open the pack that owns the key (e.g. `skills/content/seo-metadata/SKILL.md` for `seo.*`).
2. Edit the body in that key's `## <key>` section.
3. Commit. The container picks up the new body on next deploy / restart.
4. The prompt contract tests (`tests/unit/services/test_prompt_*.py`, `test_prompt_manager_skills.py`, and per-surface tests like `test_multi_model_qa_prompts.py`) pin rendered bodies — update the affected expectation in the same PR.

The contract tests serve two purposes:

- **Drift detection.** A prod-side Langfuse edit that strays from the `SKILL.md` default still lands on the dashboard but ALSO trips the test in CI, forcing a conscious revert-or-update.
- **Migration safety.** When prompts moved (f-string → YAML → `SKILL.md`), the tests verified the rendered body stayed byte-for-byte identical, so format-string gotchas (`{{`/`}}` doubling, trailing newlines) couldn't sneak through.

**Inline-fallback resilience (`test_prompt_fallback_drift.py`).** Several resolvers keep an inline `_*_FALLBACK` copy so the pipeline survives a prompt-registry outage. A parametrized guard drives each resolver with the registry up (`SKILL.md` path) and down (inline path) and asserts they agree — so a fired fallback can never silently serve stale text. When a fallback DOES fire, the resolver logs at `error` (per `feedback_self_heal_not_suppress`: self-heal by serving the byte-identical inline copy, but surface the registry outage loudly rather than swallow it).

## How callers fetch prompts

```python
from services.prompt_manager import get_prompt_manager

pm = get_prompt_manager()  # cached singleton

prompt = pm.get_prompt(
    "qa.topic_delivery",
    topic="Why local LLMs win",
    opening="In 2026, the case for local AI...",
)
```

**Sync API** — even though Langfuse is involved, the call path doesn't await. The Langfuse client caches in-process and the call returns the cached version immediately.

**Format string semantics** — kwargs are substituted via `str.format(**kwargs)`. Literal braces in the prompt body must be doubled (`{{` / `}}`); the `SKILL.md` bodies preserve the doubling.

## `SKILL.md` prompt format

A prompt-bearing pack is a single `SKILL.md`: YAML frontmatter declares the keys, the body holds one `## <key>` section per prompt with the template in a fenced block.

```markdown
---
name: seo-metadata
description: >
  SEO metadata generation. Produce titles, meta descriptions, excerpts, ...
license: Apache-2.0
metadata:
  category: seo_metadata
  prompts:
    - key: seo.generate_title
      output_format: text # 'json' | 'text' — for downstream parsing
      description: 'Default prompt — premium packs ship as an add-on'
---

# SEO metadata skill

## seo.generate_title

\`\`\`
Write an SEO title for the following article.

TOPIC: {topic}
\`\`\`
```

The loader (`prompt_manager._initialize_skills`) lives _inside_ the package (`<pkg>/skills/`) so a package-relative path resolves identically on the host (`src/cofounder_agent/skills`) and in the worker container (`/app/skills`). Operator _action_ skills (the repo-root `skills/poindexter/` pack that wraps the CLI/MCP) are a different layer — they carry no `metadata.prompts` and are not loaded here.

## Why Langfuse + `SKILL.md`, not just one

- **`SKILL.md` alone:** edits require a PR + CI + deploy. Operators tuning prompts at 11pm on a Saturday don't want to touch the repo.
- **Langfuse alone:** an OSS fork operator who hasn't set up Langfuse has nothing to fall back on.
- **Both:** the pack default ships as the OSS-friendly baseline; Langfuse is the operator's edit surface; contract tests prevent silent drift.

## Related docs

- `Glad-Labs/poindexter#450` — OSS migration umbrella (Lane A)
- `Glad-Labs/poindexter#528` — YAML → `SKILL.md` pack migration
- `Glad-Labs/poindexter#47` — the original UnifiedPromptManager migration
- [`docs/architecture/business-os-endgame.md`](./business-os-endgame) — the agentskills.io pack model and why prompts live as skills
- [`docs/reference/app-settings.md`](../reference/app-settings.md) — `langfuse_host`, `langfuse_public_key`, `langfuse_secret_key`, `langfuse_tracing_enabled`
- [`docs/architecture/cost-tier-routing.md`](./cost-tier-routing) — Lane B's sibling migration (model selection, not prompt selection)
