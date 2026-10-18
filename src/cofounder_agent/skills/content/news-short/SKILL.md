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
