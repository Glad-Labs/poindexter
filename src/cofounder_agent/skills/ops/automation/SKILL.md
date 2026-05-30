---
name: automation
description: >
  Marketing-automation prompts — generate an email campaign for a topic.
  Use when an operator-facing automation surface needs a structured
  campaign draft.
license: Apache-2.0
metadata:
  category: utility
  prompts:
    - key: task.automation_email_campaign
      output_format: json
      description: 'Create an email campaign for a topic -> subject, preview_text, body, cta, audience_segment — basic but functional default prompt'
---

# Automation skill

Default marketing-automation prompt. `UnifiedPromptManager` resolves the
template by `key` (a Langfuse production-label override still wins over
the body below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## task.automation_email_campaign

```text
Create an email campaign for: {topic}
Return JSON with keys: subject, preview_text, body, cta, audience_segment.
```
