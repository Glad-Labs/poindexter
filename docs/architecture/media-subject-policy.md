# Media subject and style policy (per niche)

Whether AI-generated images and video shots may show **people**, and whether
they may be **photoreal**, is one policy resolved per niche — not a rule copied
into six prompts and validators.

## Settings

| Key | Values | Default | Meaning |
| --- | --- | --- | --- |
| `media_human_subjects` | `allow` · `stylized_only` · `none` | `allow` | people in AI media: fine in any permitted style · fine but stylized only · never (route human subjects to real footage) |
| `media_style_policy` | `stylized` · `any` | `stylized` | AI prompts must name a stylized medium · photoreal allowed when it serves the subject |
| `media_negative_prompt_human_terms` | CSV | `face, person, human, hands, fingers` | appended to the image negative prompt only when people are forbidden |

Per-niche overrides win over the global value:

```
niche.<slug>.media.human_subjects
niche.<slug>.media.style_policy
```

```bash
poindexter settings set niche.dev_diary.media.human_subjects none
poindexter settings set niche.glad-labs.media.style_policy any
```

An unknown value logs a warning and falls through to the next level; it never
silently picks something.

## Where the policy is applied

`services/media_subject_policy.py::resolve_media_policy(site_config, niche_slug)` is the
only reader. Every surface renders from it:

- **Video director and reviser prompts** (`skills/content/video-director/SKILL.md`):
  `{human_subject_policy}`, `{style_policy}`, `{human_subject_rule}`.
- **Blog writer** (`blog_generation.initial_draft`): `{image_subject_rule}` for the
  `[IMAGE:]` subject line.
- **Image prompt writers** (`image.featured_image`, `image.inline_illustration`,
  `image.decision`): `{people_sentence}` / `{people_rule}`.
- **Negative prompt**: `image_negative_prompt` no longer carries human terms;
  `negative_prompt(policy, base)` strips them from any older row and appends them
  only for `none`. Used by the inline and featured image paths, the image
  providers' fallback, and the post-edit image commands.
- **Shot-list validator** (`schemas/video_shot_list.py`): the human-noun scan runs
  only when the policy is `none`; the director and reviser pass the policy through
  pydantic's validation context. A caller that passes no context stays strict.
- **Video QA** (`atoms/media_qa.py`): the photoreal-human frame check is skipped
  when the niche allows photoreal people (`human_detection = "policy_allows"`).

## Why the default changed (2026-09-14)

The blanket no-people rule was written when diffusion output produced melted
faces and six-fingered hands. The current image models (Flux 2 Klein, Qwen Image,
Wan 2.2 14B) do not, the director prompt had already reversed the ban for
stylized people, and the writer prompt and negative prompt had not — the rule
had drifted apart across surfaces and could not be tuned per vertical. People
are now allowed by default; the house style stays stylized until a niche opts
into `any`.
