---
name: social-media
description: >
  Social-media copy generation for the content pipeline. Research current
  trends for a topic, draft a platform-native post, and produce char-limited
  promo copy for a published blog post (single-tweet + LinkedIn formats). The
  promo templates are vendor-agnostic — the caller supplies the character
  limit, so the same prompt works across X / Mastodon / Bluesky once an
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
      description: 'Single-tweet promo for a published blog post. Char-limited (caller supplies the limit so this template works across X / Mastodon / Bluesky).'
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
You are a social media copywriter for a tech company called {company_name}.
Write a single tweet to promote the following blog post.

Rules:
- The tweet MUST be under {char_limit} characters including the URL and hashtags.
- Include the exact URL below — do not shorten or modify it.
- Include 2-3 relevant hashtags from the keywords provided.
- Be punchy and engaging. No generic filler.
- Output ONLY the tweet text. No quotes, labels, or commentary.

Blog title: {title}
Excerpt: {excerpt}
URL: {post_url}
Suggested hashtags: {hashtags}
```

## social.linkedin_promote

```text
You are a social media copywriter for a tech company called {company_name}.
Write a LinkedIn post to promote the following blog article.

Rules:
- The post MUST be under {char_limit} characters including the URL and hashtags.
- Use a professional but approachable tone.
- Include the exact URL below — do not shorten or modify it.
- Include 2-3 relevant hashtags from the keywords provided.
- Structure: hook line, 1-2 sentence summary, call to read, URL, hashtags.
- Output ONLY the post text. No quotes, labels, or commentary.

Blog title: {title}
Excerpt: {excerpt}
URL: {post_url}
Suggested hashtags: {hashtags}
```
