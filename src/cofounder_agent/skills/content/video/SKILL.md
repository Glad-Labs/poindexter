---
name: video
description: >
  Video narration. Turn a published blog article into a spoken script:
  a Short (scene prompts plus a hook-first narration sized from
  video_short_target_seconds) or a long-form voiceover. Use during the
  media-script stage of the pipeline, after a post is written.
license: Apache-2.0
metadata:
  category: video
  prompts:
    - key: video.short_form_narration
      output_format: text
      description: "Short-lane media script — one call producing SDXL scene prompts (PART 1) and the TikTok/YouTube-Shorts narration (PART 2, sized from video_short_target_seconds; its first sentence becomes the Short's title). Used by the media-scripts stage."
    - key: video.long_form_narration
      output_format: text
      description: 'Long-form video voiceover writer — produces a spoken narration script for a long blog-article video. Pure standalone audio: never references on-screen visuals, since the renderer pairs it with generic static imagery. Used by the media-scripts stage.'
---

# Video skill

The prompts the media-scripts stage uses to turn a finished article into
spoken video narration, short and long. The architect routes on the `description`
above; `UnifiedPromptManager` resolves the template by `key` (Langfuse
override still wins over the body below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## video.short_form_narration

```text
Generate TWO things for a blog post video:

PART 1 — Write 6-8 numbered lines, each describing a photorealistic image for a video slideshow about this article. Each line is a Stable Diffusion XL prompt. Requirements: cinematic lighting, no people, no text, no faces, no hands, 4K quality. One scene per line.

PART 2 — After a blank line, write "SHORT:" on its own line, then write a ~{target_seconds}-second narration (about {target_words} words) summarizing the article for TikTok/YouTube Shorts. Start with a hook, cover 2-3 key takeaways, end with "Full article at {site_name}."
Narration rules: spoken prose only — no emojis, no markdown, no hashtags, at most one exclamation mark. THE FIRST SENTENCE IS THE TITLE — it is published verbatim as the video's title, and a Shorts feed shows about its first forty characters — so make it a flat claim THIS article proves, naming this article's own subject in the first three words, under ten words total, nothing before it. Write the claim itself, never a description of it: an opener that begins "Discover how", "Learn how", "Find out" or "This article" is describing the article instead of making its point. Cut every run-up — "In today's ...", "In the world of ...", "These days ...", "As we all know ...", "Let's talk about ..." — and every question cliche ("Ever wondered", "Imagine"): they spend the hook saying nothing. Keep every number and statistic exactly as the article states it. Use commas and periods, not semicolons. Output NOTHING after the narration text — no notes, no commentary about the script, no END marker.

ARTICLE: {title}

{content}

SCENES:
```

Wired since poindexter#1071: `generate_media_scripts._build_scene_prompt`
resolves this key (it used to build the prompt in code while this entry sat
unread). `{target_seconds}` / `{target_words}` come from
`video_short_target_seconds` × `media_narration_words_per_second`, and
`{site_name}` from site_config. Keep the PART 1 / `SHORT:` / PART 2 shape:
`_parse_scene_output` splits on it. The first sentence of PART 2 becomes
the Short's YouTube title, and `services/short_hook.py` judges it after
generation.

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
