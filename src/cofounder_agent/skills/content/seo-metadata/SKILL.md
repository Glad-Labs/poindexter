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
    - key: seo.post_summary
      output_format: text
      description: "The 'In brief' summary of a published post: its title and full text in; two or three plain sentences out that state what it found, answer-first, for readers and AI answer engines. Code checks the length, that it is not about itself, and that every number appears in the post; one retry carries the reason. Used by services.post_summaries.summarize_post."
---

# SEO metadata skill

Six prompts the pipeline uses to derive post metadata from a topic or draft.
The architect routes on the `description` above; `UnifiedPromptManager` resolves
each template by `key` (Langfuse override still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## seo.generate_title

```text
You are titling a finished blog article. Read the article material below and
write one compelling, SEO-friendly title that describes what the article
actually says.

The TOPIC line is the internal assignment label that seeded the article. It
may be vague, ambiguous, or an internal directive (e.g. "Expand coverage of
X"). Never copy the topic verbatim and never title the topic alone — ground
the title in the ARTICLE text: its claims, subject matter, and terminology.
When the topic and the article seem to disagree, trust the article.

Include the primary keyword "{primary_keyword}" only when it fits the
article's actual content naturally.

Let the article's own specifics choose the title's shape. The most memorable
title is usually the one carrying this article's concrete detail — the number
that surprised you, the thing that broke, the claim a reader would argue
with — so reach for that detail before reaching for a familiar headline
frame. A title indistinguishable from the last twenty on the site tells a
returning reader nothing about which article this is. Accuracy still outranks
novelty: a title the article does not support is worse than a familiar one.

Name the searchable thing. A reader finds this article by typing a concrete
noun into a search box — the product, model, tool, library, standard, number,
or exact technical term the article is about — so the title must contain at
least one, spelled the way people type it ("RTX 5090", "FastAPI", "GGUF
Q4_K_M", "Chatterbox"). A title that is only a mood or a mystery ("The Gap
Nobody Names", "The five days nobody was watching") has no query to match and
is never found; keep the intrigue if you like, but anchor it to the thing.

Return ONLY a JSON object with a single "title" key — no markdown, no code
fences, no reasoning, no text before or after the object. The first character
of your reply is `{{` and the last is `}}`:

{{"title": "<the title — plain text, no quotes or markdown inside>"}}

TOPIC (assignment label — context only): {topic}

ARTICLE:
{content}
```

## seo.post_summary

```text
Write the "In brief" summary for the blog post below: two or three plain sentences a reader could quote on their own.

Lead with the answer or the finding, not with the question or the story. Say what the post found, recommends or measured, with its most important specific: a model, a part, a number, a result. Use only what the post itself says, and copy every number exactly as the post writes it. Write as a statement about the subject, not about the post ("The RTX 5090 runs 70B models at...", never "This article explains..."). Keep it under {max_chars} characters. No headings, lists, markdown or quotation marks.
{feedback}
Title: {title}

{content}

Reply with the summary only.
```
