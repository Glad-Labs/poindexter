---
name: qa
description: >
  Content quality evaluation. Score a draft for quality and return a
  structured verdict. Use after a draft is written, to gate it before
  publication.
license: Apache-2.0
metadata:
  category: content_qa
  prompts:
    - key: task.qa_content_evaluation
      output_format: json
      description: 'Evaluate content quality -> overall_score, readability, accuracy, suggestions — basic but functional; production-quality prompt packs ship as a premium add-on'
---

# Content QA skill

The default content-quality evaluation prompt the pipeline falls back to
when no premium prompt pack is provisioned. `UnifiedPromptManager`
resolves the template by `key` (a Langfuse production-label override
still wins over the body below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## task.qa_content_evaluation

```text
Evaluate this content for quality. Return JSON with keys: overall_score, readability, accuracy, suggestions.
Content: {content}
```
