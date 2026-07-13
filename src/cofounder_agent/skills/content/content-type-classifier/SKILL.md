---
name: content-type-classifier
description: Classify a published post into brand content-types (multi-label). Populates post_content_types via ClassifyContentTypesJob.
license: Apache-2.0
metadata:
  category: utility
  prompts:
    - key: content.classify_content_type
      output_format: json
      description: Default content-type classifier prompt — basic but functional; a premium pack can ship sharper definitions.
---

# Content-type classifier skill

Labels a published post with zero or more content-types from a DB-configurable
set (`app_settings.content_type_labels`). The `{labels}` set injected at render
time is authoritative — the job drops anything the model returns that isn't in
it. The definitions below describe the default label set; an operator who
customizes the labels should adjust these definitions to match.

## content.classify_content_type

```text
You label a technical blog post with zero or more CONTENT TYPES from this exact set:
{labels}

Definitions (for the default label set):
- ai-ml: AI/ML models, LLM inference, agents, training, prompting, RAG.
- pc-hardware: GPUs, CPUs, cooling, peripherals, physical builds and their performance.
- gaming: games, game performance, frame times, gaming setups.
- software-engineering: coding, CI/CD, databases, architecture, dev practices not centered on AI.
- founder-meta: building in public, the business, indie hacking, the content operation itself.

Assign every type that genuinely applies — a post can have several (a GPU-for-local-LLM
post is ai-ml AND pc-hardware). Assign none if none truly fit; never force a label.

Return ONLY a JSON object — no markdown, no reasoning, no code fences:
{{"labels": [{{"label": "<one of the set above>", "confidence": <0.0-1.0>}}]}}

TITLE: {title}
TAGS (noisy hints, may be wrong): {tags}
EXCERPT:
{excerpt}
```
