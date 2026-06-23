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

## video.long_form_narration

```text
Write a voiceover narration script for a long-form video about the article below.

The narration is spoken aloud and must stand on its own as audio. Write it for the ear: explain the subject directly to the listener. Do not refer to any accompanying imagery — the supporting footage is generic and will not match specific visual references, so keep every line meaningful with the eyes closed.
- Tighter and more focused than an audio-only podcast; no 'welcome back' radio filler.
- Open with a brief hook, walk the key points in order, then a natural closing line. Do NOT add a like/subscribe call-to-action — that is appended separately.
- Plain spoken prose. No headings, no stage directions.

TITLE: {title}

ARTICLE:
{content}

NARRATION:
```
