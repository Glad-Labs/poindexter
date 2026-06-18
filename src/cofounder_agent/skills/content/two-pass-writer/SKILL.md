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
Revise the following draft. For each [EXTERNAL_NEEDED: ...] marker, substitute
the corresponding external fact below and link it inline to its source URL.
Leave all other content as-is.

Return the COMPLETE revised post exactly once. Do not repeat, duplicate, or
append a second copy of any section, and do not pad the length — the revision
should be about as long as the original and end on a complete sentence.

If revision exposes a new claim that needs outside support, mark it
[EXTERNAL_NEEDED: ...] again so the next pass can fill it.

Original draft:
{draft}

External facts:
{aug_block}
```

## atoms.two_pass_writer.generate_with_context

```text
Write a blog post on the topic: "{topic}" with this angle: "{angle}".

{instructions}

Background context — your own prior work, posts, and research notes. Lead with
what you (the publisher) have actually built, run, or found here; use outside
sources to corroborate, not to carry the whole article:
{snippet_block}

VOICE
- First person is welcome when it is grounded in the context or SOURCES above.
  Use "we"/"our" for work, tests, or results that actually appear there
  ("we ran this on a 32GB card and saw..."). Never claim work that is not in a
  source — if you cannot ground a "we" statement, drop it.
- Write to the reader as "you". Take a clear position; don't hedge with
  "both have merits".

CITE YOUR SOURCES (do this — don't avoid the numbers)
- Use the specific numbers, prices, and named results from your sources, and
  link each one inline to its exact URL: "it sustains [142 fps at 4K](https://...)".
- Any claim that names a publication, person, or statistic needs an inline
  markdown link to its source URL. With no URL, state the point plainly and
  drop the attribution — never write a bare "[]", "(url)", or "According to
  [Source]" with no link.

STRUCTURE
- Use real "## " H2 headings ("### " for subsections). Never use a bold line as
  a fake heading, and don't open the body with an H1 — the title is added
  separately.
- Cover each point once. Do NOT repeat or restate a section to fill length. If
  you find yourself rewriting something you already covered, move to the
  conclusion instead.
- Finish with a complete concluding paragraph. Never stop mid-sentence.

STYLE
- Vary sentence and paragraph length. Avoid "delve", "testament", "tapestry",
  "navigating the landscape", "multifaceted", "at its core", "at the heart of",
  "It's not just X, it's Y", "In conclusion", "In summary".

Return the full post body once, in Markdown — no preamble, no image
descriptions, and no placeholder tokens other than [EXTERNAL_NEEDED: ...].
```
