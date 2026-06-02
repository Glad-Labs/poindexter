# Design: gladlabs.io Image-SEO Remediation + Site Audit Fixes

**Date:** 2026-06-02
**Author:** Matt + Claude (brainstorming session)
**Status:** Awaiting review → implementation plan

## Problem

A full audit of gladlabs.io (105 live URLs + the live `poindexter_brain` DB)
surfaced 7 fixable SEO issues. The headline problem (Matt's original concern):
**inline `<img>` alt text describes the image-generation _prompt_, not the
actual rendered image** — many alts claim people/scenes that SDXL was
explicitly told _not_ to render (negative prompt: "no people, no faces"),
so the alt is factually wrong for crawlers and screen readers.

### Audit findings (prioritized)

| #   | Issue                                                                           | Count                | Root cause                                                                 | Severity |
| --- | ------------------------------------------------------------------------------- | -------------------- | -------------------------------------------------------------------------- | -------- |
| 1   | Inline `<img>` alt describes the prompt, not the image                          | ~228 imgs / 29 posts | Alt derived from writer's `[IMAGE-N: …]` placeholder, never the pixels     | High     |
| 2   | dev_diary posts have no meta description AND no excerpt                         | 27                   | `dev_diary` template chain skips `generate_seo_metadata` + sets no excerpt | High     |
| 3   | Residual alt leak artifacts (`:mi`, `:people`, `gadgetspexels:screens with`)    | handful              | #84 scrubber stripped `\|\|…\|\|` but missed `:hint` tails                 | Medium   |
| 4   | Batch/debug suffixes leaked into live titles (`…(2026-05-11 17:48 batch C #5)`) | 2                    | Test-harness topic suffix reached `posts.title`                            | Medium   |
| 5   | `" \| Blog"` suffix pushes ~17 titles past 65 chars (SERP truncation)           | ~17                  | `buildSEOTitle` appends low-value `" \| Blog"`                             | Medium   |
| 6   | 4 legal pages missing `<link rel=canonical>`                                    | 4                    | `/legal/*` pages don't set canonical                                       | Low      |
| 7   | 3 static pages missing `og:image` (`/about`, `/posts`, `/archive/1`)            | 3                    | No featured image + no site-default OG fallback                            | Low      |

### Healthy (no action)

0 HTTP errors, 0 missing titles/h1, 0 JSON-LD parse errors, 0 broken links,
all pages < 3s, valid sitemap + robots.txt, BlogPosting/Breadcrumb schema on
all posts, all 91 posts have `og:image:alt` (= title), all `<img>` have an
`alt` attribute.

## Decisions (locked in brainstorming)

- **Alt-text method:** vision model — local `qwen3-vl:30b` describes the actual
  image. Only approach that makes alt truly accurate.
- **Scope:** all 7 issues.
- **Forward + backfill:** fix the pipeline so new posts are correct AND backfill
  existing posts.
- **Title suffix (#5):** append `" | Glad Labs"` only when total ≤ 60 chars,
  else use the bare title (drop the generic `" | Blog"`).
- **Sequencing:** one combined effort — build all code, run backfill at the end.
- **Featured images:** vision-caption featured images too; store in
  `posts.metadata->>'featured_image_alt'`; wire the frontend `og:image:alt` to
  use it with a title fallback.

## Architecture

One shared vision-captioning library, consumed by both the pipeline (forward
fix) and the backfill script (existing posts) — mirrors the existing
`services/alt_text.py` pattern (one lib shared by the stage + the #84 backfill),
so there is no logic drift.

```
              services/image_captioner.py  (NEW shared lib)
              caption_image(*, image_url|bytes, topic, budget, site_config) -> str
              · fetch bytes (httpx)  · qwen3-vl:30b via gpu.lock  · sanitize_alt_text()
                       /                                          \
       PIPELINE (forward fix)                          BACKFILL (existing posts)
   new node `stage.caption_images`                scripts/backfill-image-alt-vision.py
   in canonical_blog graph_def                    · re-caption only "bad" alts
   (after replace_inline_images +                 · update posts.content (+ metadata
    source_featured_image)                          featured_image_alt, + pipeline_versions)
                                                   · export_post(slug) -> R2
```

**Production data flow (critical):** `posts` (DB) → `static_export_service`
pushes `static/posts/index.json` (list metadata) + `static/posts/{slug}.json`
(full content incl. inline `<img>`) to R2 → the Next.js site reads those files.
A DB-only change is invisible until the static export is regenerated. Every
fix therefore ends with **DB update → `export_post(slug)` (or full rebuild) →
R2**.

## Components

### Track A — Image alt text (issues #1, #3) + featured-image alt

**A1. `services/image_captioner.py` (new shared lib)**

- `async caption_image(*, image_url=None, image_bytes=None, topic, budget, site_config) -> str`
- Fetches bytes when given a URL (httpx, short timeout). base64-encodes.
- Calls `qwen3-vl:30b` **through the dispatcher** (`dispatch_complete`) so the
  provider stays swappable (Matt's directive — no direct Ollama coupling).
  Passes the image as an OpenAI-style content block
  (`{"type":"image_url","image_url":{"url":"data:image/png;base64,<b64>"}}`),
  which the active `litellm` provider translates to the Ollama `images` field.
  Wrapped in `gpu.lock("ollama", model="qwen3-vl:30b", phase="caption_image")`.
  Verified empirically (2026-06-02): qwen3-vl produces accurate, concise,
  pixel-grounded captions with no "image of" filler.
- Prompt: _"Write alt text for this image, which appears in a blog article
  about '{topic}'. Describe only what is actually visible in the image —
  factual, concise, one sentence, ≤{budget} chars. Do NOT begin with 'image
  of'/'photo of'. Do NOT invent details that aren't visible."_
- Post-process through existing `services.alt_text.sanitize_alt_text(...,
budget=..., topic=...)` so all the leak-token / mid-word / SDXL-prompt-shape
  guards still apply.
- **Fail-soft:** on any fetch/inference error, return `None` so callers keep the
  existing alt (never blank a post). Logs a WARN (no silent default).
- New DB-driven settings (defaults seeded): `vision_alt_model`
  (`qwen3-vl:30b`), `vision_alt_enabled` (`true`), reuse `alt_text_budget`.

**A2. Pipeline node `stage.caption_images`**

- New stage class in `services/stages/caption_images.py`; registered in the
  stage registry and added to `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF`
  immediately after `source_featured_image` (so both inline + featured images
  exist).
- Reads `context["content"]`, iterates `<img>` tags via
  `services.alt_text.iter_img_alts` + the `_IMG_ALT_RE` regex, replaces each
  alt with `caption_image(image_url=<src>, topic=...)` (fail-soft keeps the
  prior alt). Captions the featured image (`context["featured_image_url"]`) and
  overwrites `context["featured_image_alt"]` with the vision result.
- Re-seed migration `YYYYMMDD_HHMMSS_add_caption_images_node.py` updates the
  active `pipeline_templates.graph_def`. Must import only light modules
  (migrations-smoke env) — see [[migrations-smoke-light-env]].
- `requires`/`produces` declared so `build_graph_from_spec` reachability passes.

**A3. Backfill `scripts/backfill-image-alt-vision.py` (new)**

- Modeled on `scripts/backfill-alt-text.py` (same DB-URL resolution, dry-run,
  `--post-id`).
- For each published post (and `pipeline_versions` for pending tasks) with
  inline `<img>`:
  - Parse each `<img src alt>` and **re-caption every inline image.** Empirical
    verification (2026-06-02) found even clean-looking alts are inaccurate
    (they describe prompt intent, not the abstract image SDXL rendered), so
    text-pattern targeting would skip the dangerous "reads-clean-but-lies"
    cases. Re-caption all; don't try to pre-judge from the text.
  - **Idempotency via marker, not text-shape:** after processing a post, stamp
    `posts.metadata->>'alt_vision_backfilled_at'`. Re-runs skip stamped posts
    unless `--force` (vision is non-deterministic, so we don't want re-runs to
    churn already-corrected alts). Still only UPDATEs rows whose content changed.
  - Replace the alt in `posts.content`.
  - Vision-caption the featured image and set `posts.metadata` →
    `featured_image_alt`.
  - After DB writes, call `static_export_service.export_post(pool, slug,
site_config=...)` per touched slug to push fresh R2 JSON.
- `--dry-run` prints every before→after alt for review. `--limit N` for a
  staged rollout. Only UPDATEs rows whose content actually changes.
- One orchestrator with `--mode {alt,seo-desc,titles}` (`alt` default) — the
  same script runs the Track-B backfills (B1 `seo-desc`, B2 `titles`) so all
  DB-touching backfills share DB-URL resolution, dry-run, and R2 re-export.

**A4. Static export + frontend wiring for featured alt**

- `static_export_service._fetch_post_by_slug` / `_fetch_published_posts`:
  add `metadata->>'featured_image_alt' AS featured_image_alt` to the SELECT and
  include it in the emitted JSON.
- `web/public-site/lib/posts.ts`: add `featured_image_alt?: string` to `Post`.
- `web/public-site/app/posts/[slug]/page.tsx::generateMetadata`: set
  `openGraph.images[].alt = post.featured_image_alt || post.title` (hero stays
  `alt=""` — intentional WCAG decorative).

### Track B — SEO content gaps (issues #2, #4, #5)

**B1. dev_diary meta descriptions (#2)**

- Forward: add the existing `generate_seo_metadata` stage to the `dev_diary`
  template node chain in `services/pipeline_templates/__init__.py` (it derives
  title/description/keywords generically from content+topic). Also set an
  `excerpt`.
- Backfill: run the same SEO generator over the 27 existing dev_diary posts'
  `content` → write `posts.seo_description` (+ `excerpt`) → re-export
  `index.json` + per-slug. Implemented as a `--mode seo-desc` pass in the one
  backfill orchestrator script (see A3 — modes: `alt` default, `seo-desc`,
  `titles`).

**B2. Batch-suffix title cleanup (#4)**

- Backfill: strip the trailing `(YYYY-MM-DD … batch X #N | overnight … | #N)`
  pattern from `posts.title` for the 2 contaminated rows (regex, reviewed in
  dry-run via `--mode titles`). Re-export.
- Forward guard: a publish-time assertion / sanitizer that strips test/batch
  suffixes from the title before it lands on a published `posts` row (in
  `publish_service`), so harness suffixes can never reach a live title again.

**B3. `buildSEOTitle` suffix (#5)** — `web/public-site/lib/seo.js`

- Change suffix logic: build `"{title} | Glad Labs"`; if ≤ 60 chars use it,
  else return the bare `title`. Drop the generic `" | Blog"`. Update
  `lib/seo.test.js` boundary cases. Frontend-only, no DB.

### Track C — Structural polish (issues #6, #7)

**C1. Legal canonicals (#6)** — add `alternates: { canonical }` to the metadata
of the 4 `/legal/*` pages.

**C2. Static-page og:image (#7)** — add a site-default OG image
(`/og-image.jpg`, already referenced as a fallback) to the metadata of
`/about`, `/posts`, `/archive/[page]` (and ideally a root default in
`app/layout.js`).

## Error handling

- Vision failures never blank an alt or fail a pipeline task — fail-soft, keep
  prior value, log WARN. Per `feedback_no_silent_defaults`: log, don't silently
  default.
- Backfill is dry-run-first, idempotent, scoped (`--post-id`/`--limit`), and
  only writes changed rows.
- `vision_alt_enabled=false` is a kill switch for the pipeline node (degrades to
  the current template alt, which still passes `assert_alt_text_clean`).

## Testing

- **Unit:** `image_captioner` (mock Ollama vision → asserts sanitization,
  fail-soft None on error, "image of" prefix stripped, budget respected);
  `buildSEOTitle` boundary (≤60 keeps brand, >60 drops it); the title-suffix
  stripper regex; the "bad alt" detector for the backfill.
- **Integration:** backfill `--dry-run` over 2-3 real posts → assert
  before/after; `stage.caption_images` in a pipeline run; static export emits
  `featured_image_alt`.
- **Verification:** re-run `.audit/seo_audit.py` after the fix → issue counts
  for #1–#7 should drop to ~0. Confirm a sample live post's per-slug R2 JSON
  carries the corrected inline alt + meta description.

## Out of scope (deferred)

- Per-image structured data (`ImageObject` in JSON-LD) — low SEO gain.
- Image sitemap.
- Rewriting the `dev_diary` template into the graph_def (it intentionally stays
  on the legacy `TEMPLATES` factory).

## Rollout

One combined branch/PR for all code (captioner lib + pipeline node + migration +
frontend + guards + tests), reviewed and merged. Then run the backfill
(`--dry-run` → review → live) against prod `poindexter_brain`, which re-exports
to R2. Verify with a fresh `seo_audit.py` pass. Worker deploy = update the
bind-mounted checkout + restart — see [[worker-deploy-bind-mount]].
