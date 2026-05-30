---
name: two-pass-writer
description: >
  Two-pass blog writer for niche-driven content. Drafts a post from
  topic + angle + background context, then revises it to substitute
  [EXTERNAL_NEEDED: ...] markers with verified external facts. Use as
  the canonical_blog writer during content generation and revision.
license: Apache-2.0
metadata:
  category: blog_generation
  prompts:
    - key: atoms.two_pass_writer.revise_prompt
      output_format: markdown
      description: 'Two-pass revision-stage prompt — rewrites a draft, substituting [EXTERNAL_NEEDED: ...] markers with the corresponding external facts.'
    - key: atoms.two_pass_writer.generate_with_context
      output_format: markdown
      description: 'Generic RAG-writer prompt — builds a blog draft from topic, angle, optional extra instructions, and background-snippet context. Used by ai_content_generator.generate_with_context (the draft-stage call inside atoms.two_pass_writer).'
---

# Two-pass writer skill

Two prompts the canonical_blog pipeline uses to draft and revise niche-driven
posts. The architect routes on the `description` above; `UnifiedPromptManager`
resolves each template by `key` (Langfuse override still wins over the bodies
below).

Keys follow the dotted shape `atoms.<atom_name>.<purpose>`. Variables use
`{name}` placeholders matched against `str.format` kwargs; curly literals inside
JSON-shaped templates must be doubled (`{{` and `}}`).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## atoms.two_pass_writer.revise_prompt

```text
Revise the following draft. For each [EXTERNAL_NEEDED: ...] marker,
substitute the corresponding external fact provided below. Keep everything else unchanged.
If revision exposes a new claim that needs outside support, mark it [EXTERNAL_NEEDED: ...]
again so the next pass can fill it.

Original draft:
{draft}

External facts:
{aug_block}
```

## atoms.two_pass_writer.generate_with_context

```text
Write a blog post on the topic: "{topic}" with this angle: "{angle}".

{instructions}

Background context (cite where relevant):
{snippet_block}

Return the full post body in Markdown.
```
