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
    - key: task.affiliate_derive_keywords
      output_format: json
      description: 'Derive a short display name + keyword aliases for an affiliate-catalog product from its long marketing title + description -> {display_text, keywords} JSON. Used by `poindexter affiliate import-csv`.'
---

# Content utility skill

General-purpose content prompts the pipeline falls back to when no
premium prompt pack is provisioned. `UnifiedPromptManager` resolves each
template by `key` (a Langfuse production-label override still wins over
the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## task.affiliate_derive_keywords

```text
You are naming and tagging a product for an affiliate-link catalog.

Given the product title and description below, respond with ONLY a JSON
object: {{"display_text": "<short human name, 2-6 words>", "keywords":
["<alias 1>", "<alias 2>", ...]}}

- display_text: a short, natural name a reader would recognize (not the
  full marketing title).
- keywords: 3 to 6 short phrases (1-4 words each) that might plausibly
  appear in prose referring to this product - brand names, model numbers,
  common nicknames. Avoid generic single words that could describe many
  products.

Title: {title}
Description: {description}
```
