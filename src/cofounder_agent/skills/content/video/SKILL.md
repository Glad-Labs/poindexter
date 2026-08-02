---
name: video
description: >
  Short-form video narration. Turn a published blog article into a
  ~150-word spoken script for TikTok / YouTube Shorts — hook first,
  two or three takeaways, a closing call to action. Use during the
  media-script stage of the pipeline, after a post is written.
license: Apache-2.0
metadata:
  category: video
  prompts:
    - key: video.short_form_narration
      output_format: text
      description: 'Short-form vertical-video narration writer — produces a ~150-word TikTok/YouTube-Shorts script summarising a blog article. Used by video_service short-form pipeline'
    - key: video.long_form_narration
      output_format: text
      description: 'Long-form video voiceover writer — produces a spoken narration script for a long blog-article video. Pure standalone audio: never references on-screen visuals, since the renderer pairs it with generic static imagery. Used by the media-scripts stage.'
---

# Video skill

One prompt the pipeline uses to turn a finished article into a spoken
short-form video narration. The architect routes on the `description`
above; `UnifiedPromptManager` resolves the template by `key` (Langfuse
override still wins over the body below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## video.short_form_narration

```text
Write a 60-second video narration (about 150 words) summarizing this article.

RULES:
- Start with a compelling hook that grabs attention in the first 5 seconds
- Cover the 2-3 most important takeaways
- End with a call to action inviting viewers to read the full article at {site_name}
- Conversational, energetic tone — this is for TikTok/YouTube Shorts
- No URLs, no markdown, no special characters
- Write ONLY the narration text, nothing else

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content}

NARRATION:
```

The long-form prompt's `{target_seconds}` / `{target_words}` placeholders are
substituted from `video_long_target_seconds` (words = seconds × 2.5 WPS) — the
same one-canonical-target pattern as the short lane, so the narration ask, the
director's visual plan, and the runaway-trim ceiling can never disagree.
(Prose must sit OUTSIDE the `## <key>` → fence pair: `extract_section` matches
a fence immediately after the heading, so a paragraph between them makes the
key unresolvable and the stage falls back to its in-code default.)

## video.long_form_narration

```text
Write a voiceover narration script for a long-form video about the article below.

The narration is spoken aloud and must stand on its own as audio. Write it for the ear: explain the subject directly to the listener. Do not refer to any accompanying imagery — the supporting footage is generic and will not match specific visual references, so keep every line meaningful with the eyes closed.
- Aim for a ~{target_seconds}-second narration (about {target_words} words of spoken prose).
- COLD OPEN: start mid-thought on the article's strongest concrete fact or tension. Never open with a greeting or a scene-setting frame — no "Welcome", "In today's", "Let's explore", "Imagine", "deep dive".
- Close on the article's final insight in one natural sentence. Never "In conclusion", "In summary", "To wrap up". Do NOT add a like/subscribe call-to-action — that is appended separately.
- Keep every number, dollar figure, and statistic exactly as the article states it — the numbers are the substance.
- Banned words and phrases: delve, tapestry, testament, game-changer, revolutionize.
- Plain spoken prose. Commas and periods, not semicolons. No headings, no stage directions, no emojis, no markdown.

TITLE: {title}

ARTICLE:
{content}

NARRATION:
```
