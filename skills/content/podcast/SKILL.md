---
name: podcast
description: >
  Podcast script generation. Rewrite a finished blog article into a
  single-narrator, TTS-ready podcast script — translating every
  written/visual convention (URLs, "see below", headings, abbreviations)
  into natural spoken English. Use after a post is written, when
  generating the audio/podcast surface for it.
license: Apache-2.0
metadata:
  category: podcast
  prompts:
    - key: podcast.script_rewrite
      output_format: text
      description: 'Podcast script rewriter — turns a blog article into a single-narrator TTS-ready podcast script with all written/visual conventions translated to spoken English. Used by podcast_service'
---

# Podcast skill

One prompt the pipeline uses to turn a published blog article into a
single-narrator, text-to-speech-ready podcast script. The architect routes on
the `description` above; `UnifiedPromptManager` resolves the template by `key`
(Langfuse override still wins over the body below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## podcast.script_rewrite

```text
Rewrite the following blog article as a podcast script for a single narrator.

RULES:
- This is for text-to-speech, so write exactly what should be spoken aloud
- Convert ALL written/visual conventions to natural spoken English
- Remove ALL URLs, links, image references, photo credits, and attribution lines
- Remove any "Suggested Resources", "External Links", or reference sections at the end
- Replace "this post", "this article", "this blog" with "this episode" or "today's episode"
- Replace "see below", "shown below", "scroll down" with "coming up next" or "in a moment"
- Replace "read on" with "stay with us" or "let's continue"
- Replace "as shown above" with "as we discussed"
- Don't read section headings as-is — weave transitions naturally ("Let's now turn to..." or "Next, let's explore...")
- Expand abbreviations: "e.g." → "for example", "i.e." → "that is", "etc." → "and so on"
- Spell out acronyms on first use if not commonly known
- Don't include any markdown formatting, asterisks, brackets, or special characters
- Keep the same depth, arguments, and structure — don't summarize or shorten
- Write in a warm, conversational but authoritative tone
- NEVER use first person ("I", "my", "I think", "what I call") — the narrator is presenting facts, not opinions
- Use "we" sparingly and only when the original article does — prefer impersonal phrasing ("the industry is seeing", "developers are finding")
- Do NOT add any meta-commentary like "Here's the script:" — just output the script text directly

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content}

PODCAST SCRIPT:
```
