---
name: social-media
description: >
  Social-media copy generation for the content pipeline. Research current
  trends for a topic, draft a platform-native post, and produce char-limited
  promo copy for a published blog post (single-tweet + LinkedIn formats). The
  promo templates are vendor-agnostic — the caller supplies the character
  limit, so the same prompt works across X / Mastodon once an
  adapter exists. Use after a post is published, during distribution.
license: Apache-2.0
metadata:
  category: social_media
  prompts:
    - key: social.research_trends
      output_format: json
      description: 'Default prompt — basic but functional; production-quality prompt packs ship as a premium add-on.'
    - key: social.create_post
      output_format: json
      description: 'Default prompt — basic but functional; production-quality prompt packs ship as a premium add-on.'
    - key: social.twitter_promote
      output_format: text
      description: 'Single-tweet promo for a published blog post. Char-limited (caller supplies the limit so this template works across X / Mastodon).'
    - key: social.linkedin_promote
      output_format: text
      description: 'LinkedIn-format promo for a published blog post — professional but approachable tone, hook → summary → CTA → URL → hashtags structure.'
---

# Social media skill

Four prompts the pipeline uses to generate social copy. The `research_trends`
and `create_post` prompts feed topic discovery / drafting; the `twitter_promote`
and `linkedin_promote` prompts produce char-limited promo copy for a published
post (the caller supplies `char_limit`, reading `app_settings.social_*_char_limit`,
so the same template stays vendor-agnostic). The architect routes on the
`description` above; `UnifiedPromptManager` resolves each template by `key`
(Langfuse override still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## social.research_trends

```text
Research current social media trends for: {topic}
Return JSON with keys: trends (list), hashtags (list), angles (list).
```

## social.create_post

```text
Create a social media post for {platform} about: {topic}
Return JSON with keys: text, hashtags (list), call_to_action.
```

## social.twitter_promote

```text
You write the social account of {company_name} — an engineer-run publication sharing what it learns in public.
Write a single tweet promoting the blog post below to technical readers.

Method:
1. Find the most concrete, surprising detail in the title and excerpt — a number, a cost, a named tool, a lived scene — and open with it, stated plainly.
2. Add one sentence on what the article shows or why that detail matters.
3. Close with the exact URL.

Rules:
- The tweet MUST be under {char_limit} characters including the URL and any hashtags. The URL alone is {url_chars} characters, so everything you write besides it must fit in {prose_budget}.
- Include the exact URL below — do not shorten or modify it.
- Specifics carry the post: use the article's own numbers, names, and scenes rather than adjectives, questions, or hype.
- Vary the opener across posts — a detail, a claim, or a scene. Stock openers (“Stop guessing…”, “Ever wondered…?”) read as spam.
- Hashtags: none by default; at most one from the suggested list, only when it reads naturally.
- At most one emoji, only where it adds meaning; open with words.
- Output ONLY the tweet text. No quotes, labels, or commentary.

Blog title: {title}
Excerpt: {excerpt}
URL: {post_url}
Suggested hashtags: {hashtags}
```

## social.linkedin_promote

```text
You write the company page of {company_name} — an engineer-run publication sharing what it learns in public.
Write a LinkedIn post promoting the blog article below to a technical professional audience.

Method:
1. Open with the article's most concrete, surprising detail — a number, a cost, a named tool, a lived scene — stated plainly in one line.
2. Follow with two or three short sentences on what the article shows and who it helps.
3. Invite the click in one plain sentence, then the exact URL.

Rules:
- The post MUST be under {char_limit} characters including the URL and any hashtags. The URL alone is {url_chars} characters, so everything you write besides it must fit in {prose_budget}.
- Include the exact URL below — do not shorten or modify it.
- Specifics carry the post: use the article's own numbers, names, and scenes rather than adjectives, questions, or hype.
- Hashtags: at most two from the suggested list, placed at the very end, only when they read naturally.
- Output ONLY the post text. No quotes, labels, or commentary.

Blog title: {title}
Excerpt: {excerpt}
URL: {post_url}
Suggested hashtags: {hashtags}
```
