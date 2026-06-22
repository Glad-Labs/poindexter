---
name: seo-metadata
description: >
  SEO metadata generation. Produce titles, meta descriptions, excerpts,
  keywords, tags, and category matches for a post. Use during the
  generate_seo_metadata pipeline stage, after the draft is written.
license: Apache-2.0
metadata:
  category: seo_metadata
  prompts:
    - key: seo.generate_title
      output_format: json
      description: 'Default prompt — basic but functional; premium prompt packs ship as an add-on'
    - key: seo.generate_meta_description
      output_format: text
      description: 'Default prompt — basic but functional; premium prompt packs ship as an add-on'
    - key: seo.extract_keywords
      output_format: text
      description: 'Default prompt — basic but functional; premium prompt packs ship as an add-on'
    - key: seo.generate_excerpt
      output_format: text
      description: 'Default prompt — basic but functional; premium prompt packs ship as an add-on'
    - key: seo.match_category
      output_format: text
      description: 'Default prompt — basic but functional; premium prompt packs ship as an add-on'
    - key: seo.extract_tags
      output_format: text
      description: 'Default prompt — basic but functional; premium prompt packs ship as an add-on'
---

# SEO metadata skill

Six prompts the pipeline uses to derive post metadata from a topic or draft.
The architect routes on the `description` above; `UnifiedPromptManager` resolves
each template by `key` (Langfuse override still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## seo.generate_title

```text
Generate one SEO-friendly blog post title for the topic below.

Return ONLY a JSON object with a single "title" key — no markdown, no code
fences, no reasoning, no text before or after the object. The first character
of your reply is `{{` and the last is `}}`:

{{"title": "<the title — plain text, no quotes or markdown inside>"}}

TOPIC: {topic}
```

## seo.generate_meta_description

```text
Generate a meta description (150-160 chars) for: {topic}
```

## seo.extract_keywords

```text
Extract 5-10 SEO keywords from this content: {content}
```

## seo.generate_excerpt

```text
Generate a 2-3 sentence excerpt for: {content}
```

## seo.match_category

```text
Match this content to the best category from: {categories}. Content topic: {topic}
```

## seo.extract_tags

```text
Extract relevant tags for this content: {content}
```
