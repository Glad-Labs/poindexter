---
name: utility
description: >
  General content-utility prompts — the writer-persona system prompt,
  content summarization, and free-form content-to-JSON conversion. Use
  for the writer system role, condensing content, or coercing content
  into structured JSON.
license: Apache-2.0
metadata:
  category: utility
  prompts:
    - key: system.content_writer
      output_format: markdown
      description: 'Default writer-persona system prompt — basic but functional; production-quality prompt packs ship as a premium add-on'
    - key: task.content_summarization
      output_format: text
      description: 'Summarize content concisely — basic but functional; production-quality prompt packs ship as a premium add-on'
    - key: task.utility_json_conversion
      output_format: json
      description: 'Convert free-form content into structured JSON — basic but functional; production-quality prompt packs ship as a premium add-on'
---

# Content utility skill

General-purpose content prompts the pipeline falls back to when no
premium prompt pack is provisioned. `UnifiedPromptManager` resolves each
template by `key` (a Langfuse production-label override still wins over
the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## system.content_writer

```text
You are a content writer. Write in a {style} style for {target_audience}.
Domain: {domain}. Tone: {tone}. Target length: ~{target_length} words.
Format output as clean Markdown with proper headings.
```

## task.content_summarization

```text
Summarize this content concisely: {content}
```

## task.utility_json_conversion

```text
Convert this content to structured JSON: {content}
```
