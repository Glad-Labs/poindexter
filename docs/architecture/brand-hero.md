# Brand hero — composed, not generated

**Status:** shipped 2026-08-09 (poindexter#1002 follow-up).

A hero for a post about Poindexter wants the brand mark. A mark means type.
**Diffusion cannot set type.**

Asked for a branded hero, image-gen produced the words "Poindexter Philosophy"
in mangled letterforms across the top of the frame, over two human figures with
six-fingered hands — breaking the no-text rule _and_ the no-people rule from
[`blog-generation/SKILL.md`](../../src/cofounder_agent/skills/content/blog-generation/SKILL.md)
in a single image. The image-gen OCR gate exists because this keeps happening.

So don't roll the dice. Render it.

## How it works

[`services/brand_hero.py`](../../src/cofounder_agent/services/brand_hero.py)
lays out real HTML with the real brand tokens and screenshots it in headless
chromium — the same one `services/preview_screenshot.py` drives for the
`qa.vision` rail and the screenshot provider.

```text
HeroSpec(title, tagline, stages, …)
      ↓ render_hero_html          ← brand tokens, escaped operator text
   a standalone HTML document
      ↓ capture_preview_screenshot(file://…)
   PNG bytes (1200×630)
      ↓ PostEditService.brand_hero → _upload_image → replace_image(featured)
   featured_image_url
```

Consequences worth the trade:

- **Type renders as type.** No garbled glyphs, and the OCR gate is irrelevant —
  the text is intentional.
- **Deterministic.** Same spec, same image. No seed roulette.
- **No GPU.** No VRAM contention, no cold model load, no hard-unload race. The
  diffusion hero took three attempts and ~11 minutes of wall clock across two
  CUDA OOMs; this takes under a second.
- **~15 KB** versus ~470 KB for the diffusion equivalent.

## Usage

```bash
poindexter tasks brand-hero <task_id>
poindexter tasks brand-hero <task_id> --tagline "Every draft clears a gate."
```

Also `POST /api/tasks/{id}/brand-hero` with `{"tagline": …, "title": …}`.

Like `replace-image --which featured`, it reaches **published** posts: it writes
`posts.featured_image_url` and triggers a static-export rebuild. It does **not**
bust the Vercel ISR cache — revalidate the post's cache tag separately or the
live page keeps serving the old image.

## The spec

`HeroSpec` is what varies between heroes:

| Field                          | Default                                                          | Notes                                                                            |
| ------------------------------ | ---------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `title`                        | `Poindexter`                                                     | The wordmark.                                                                    |
| `accent_chars`                 | `4`                                                              | Leading characters rendered in cyan. Clamped to the title length.                |
| `tagline`                      | "An open-source AI content pipeline. Every draft clears a gate." |                                                                                  |
| `tagline_accent`               | `gate`                                                           | First occurrence gets the amber span. Absent substring is a no-op, not an error. |
| `stages`                       | Research → Draft → Review → **Gate** → Publish                   | `(label, state)`; state is `done` \| `gate` \| `pending`.                        |
| `eyebrow_org` / `eyebrow_mark` | `Glad Labs` / `GL`                                               | Console-style header.                                                            |
| `footer_left` / `footer_right` | repo / site                                                      |                                                                                  |

Edges _after_ the gate render dim — nothing has flowed past it. That is the
post's argument in one glyph: work goes in, and not all of it comes out.

All operator-supplied text is HTML-escaped; the CLI flags reach the template
directly, so this is a real injection seam and is pinned by tests.

## Colours and type

Palette mirrors
`packages/brand/src/tokens/colors.css`
— cyan `#00e5ff` primary, amber `#ffb74d` categorical, deep navy `#070a0f`
base. Cyan-and-amber rather than green-and-red keeps the gate legible for
red-green colourblind readers, which is the tokens file's own stated rule.

That CSS is a JS-package asset outside `src/cofounder_agent`, so it is **not**
on the worker container's filesystem (only `src/cofounder_agent` mounts at
`/app`) and cannot be read at runtime. The palette is mirrored into Python and
`test_palette_matches_brand_tokens` parses the real CSS wherever the repo
exists (host + CI) to catch drift.

> That guard originally computed the repo root one directory too high and
> skipped silently instead of failing — the exact fail-open-while-reporting-
> success shape it was written to prevent. If you touch the path, confirm the
> test still _runs_, not just that the suite is green.

Typeface is **JetBrains Mono**, the brand's `--gl-font-mono`. Space Grotesk
(`--gl-font-display`) is not installed in the worker image, and mono is the
better fit regardless: it is the face the operator console uses, so the hero
looks like the product rather than like stock art.

## Related

- [`screenshot-image-provider.md`](screenshot-image-provider.md) — the sibling
  answer for _inline_ images on posts about the system
- [`services/preview_screenshot.py`](../../src/cofounder_agent/services/preview_screenshot.py) — the shared capture helper
