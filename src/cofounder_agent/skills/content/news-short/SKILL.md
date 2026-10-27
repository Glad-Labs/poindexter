---
name: news-short
description: >
  News Short narration. Turn one news story's page into the spoken script of
  a 30-45 second YouTube Short, with no blog post behind it. Every number is
  checked in code against the story's page. Use in the news_short pipeline,
  after the story's page is read.
license: Apache-2.0
metadata:
  category: video
  prompts:
    - key: news_short.script
      output_format: text
      description: "Writes a news Short's narration from the story's headline ({title}), page text ({source}) and the niche's topic subject ({subject}), sized {min_words}-{max_words} words around {target_words}. Checked in code for length, spoken prose (no links, markdown or emoji), a claim as the first sentence, and every number present in the source. Used by services.news_shorts.write_script."
    - key: news_short.script_fix
      output_format: text
      description: 'The one corrective retry for news_short.script: carries the reason code rejected the first reply ({reason}) and the word range ({min_words}, {max_words}). Used by services.news_shorts.write_script.'
    - key: news_short.hook_pick
      output_format: json
      description: 'Picks which of several in-scope news stories ({stories}, one "[label] headline — summary" per line) makes the strongest Short for a channel about {subject}: the one with a concrete hook, a named company, product or person plus a specific number, record, incident or reversal. Returns JSON {pick, reason}; code checks the pick is one of the labels and falls back to the best feed score. Used by services.news_shorts.pick_story.'
---

# News Short skill

The prompts the `news_short` pipeline uses to write a YouTube Short from one
news story (`services/news_shorts.py`, `services/news_short_spec.py`). The
architect routes on the `description` above; `UnifiedPromptManager` resolves
the template by `key`.

The script is narrated as it is written, so it is plain spoken prose. A
like-and-subscribe line is appended after it by the narration step
(`media.cta.video_short`), so the script ends on the story. The Short's
YouTube title and description lead are written later, from this script, by
`video.short_title` / `video.short_description`.

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## news_short.script

```text
Write the narration for a YouTube Short about this news story.

The Short is the story told aloud in under a minute, to someone scrolling who
has not read it. The narration is read by a text-to-speech voice over
pictures, so it is plain spoken sentences.

HEADLINE: {title}

THE STORY (the text of its page; it may include navigation or other leftovers
from the page, which you leave out):
{source}

Write {min_words} to {max_words} words, about {target_words}, that:
- open with the news itself in one short sentence: who did what, or what changed
- say why it matters to the channel's viewers, who follow: {subject}
- give the two or three facts from the story that carry it: names, figures, dates
- use only facts and numbers the story states, writing numbers as digits
- end on a sentence that completes the thought, such as what happens next or
  what is still unknown

Write it as one paragraph of spoken sentences.

Output ONLY the narration.
```

## news_short.script_fix

```text
FIX: {reason}. Write the narration again: {min_words} to {max_words} words of
plain spoken sentences in one paragraph, opening with the news itself, using
only facts and numbers the story states.

Output ONLY the narration.
```

## news_short.hook_pick

```text
You choose which news story becomes a 40-second YouTube Short for a channel about {subject}.

Choose the story with the most concrete hook, the one a viewer understands from a single line. A strong hook names a specific company, product or person and gives a specific number, record, incident or reversal: a price, a benchmark result, a count, a theft, a shutdown, a broken record.

Prefer a story where something happened over a story about an opinion, a trend or advice.

Stories:
{stories}

Return JSON only:
{{"pick": "<label of the story you choose>", "reason": "<one sentence naming its hook>"}}
```

> **Why a hook pick (2026-10-27).** The first 21 Shorts on the channel showed a
> clear pattern: the best by views named a company or product with a concrete
> number or event (ASML EUV bottleneck 108 views, "probed OpenAI with 16,000
> requests" 82, "67% hallucinations" 76, a broken overclocking record 64), and
> the worst were abstract (6, 3, 1). The pool's feed score measures popularity
> where the story was posted, not whether it makes a Short.
