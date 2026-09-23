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

## The house style REPLACES the modifier menu

`niche.<slug>.media.house_style` (falling back to `media_house_style`) names
one look every AI shot must open with. It was first added by *prepending* a
"do not vary the modifier" block to the existing style policy — and that did
nothing at all, because the very next paragraph still offered a menu:

> Begin EVERY … prompt with exactly: "retro-tech cyberpunk illustration". **Do
> not vary the modifier between shots.**
>
> … Stylized: **pick a modifier such as** flat vector illustration / isometric
> 3D / line art / cyberpunk neon / low poly / watercolor / paper cutout.

The director read the menu as the operative instruction. Measured on the
2026-09-22 NCCL pair, **0 of 12 AI shots** began with the house style, and
five different modifiers did: cinematic illustration ×3, flat vector
illustration ×2, cyberpunk neon, isometric 3D, and — via the `style_policy:
any` photoreal branch — abstract photorealism.

Two rules come out of that, and both are pinned by test:

* **A house style replaces the menu, it does not precede it.**
  `video_style_policy` returns the house-style block plus a tail that names
  **no modifier at all**, because a list in this prompt is an invitation.
  Whether shots read as illustration or as photography is settled by the
  house-style string itself, so the tail is style-agnostic and keeps only the
  buzzword ban (`8K` / `DSLR` / `hyper-realistic` / `ultra-detailed`), which is
  an AI tell in any style.
* **The worked examples are part of the instruction.** The director's three
  example prompts carried three *different* literal modifiers, two of them in
  the same example shot list — so the examples demonstrated a look per shot,
  which is the thing the house style forbids. They now all render
  `{style_prefix}`, which is the house style when one is set. This is the same
  failure as [the hook prompt's quotable example](../operations/youtube-metadata.md):
  **a model copies an example far more readily than it obeys a rule.**

`{style_prefix}` belongs to the two director sections only. The five prompt
keys share one `SKILL.md` but have different callers, so a variable added for
the director would raise `KeyError` inside the reviewer's `.format()` — and
both call sites catch `Exception` and log "prompt render failed — skipping",
so the failure mode is not a crash but the stage quietly not running. A test
walks every key's section and asserts each placeholder is supplied by that
key's real call site.

**Pexels is still exempt, deliberately.** Stock footage is real, so a house
style cannot apply to it, and a hard rule in the director skill tells it to
mix sources. On the NCCL pair that was 5 of 20 shots, so an "illustration"
video still carries photoreal interludes by construction. That is a product
decision, not a bug.

Related: [media personas](media-personas.md) — a presenter is a face bound to a voice (`persona.<slug>.*`), selected per niche by `niche.<slug>.media.persona`; its `style_policy` must be allowed by the policy above.
