# Content-grounded image direction — the writer nominates images, a local model phrases them

**Status:** Design — pending spec review (operator brainstormed 2026-07-10).
**Scope:** `canonical_blog` image generation — the featured (hero) image and every inline image. Touches the writer contract, the image decision agent, the featured-image stage, the style rotation, and the prompt-builder model pin.
**Related, out of scope:** the `qa.vision` relevance gate stays advisory (a separate follow-up); `ImageRebuildService` (`poindexter tasks rebuild-images`, shipping in parallel on `claude/cli-scheduling-time-parse-ff68b6`) is **not** modified here — this spec preserves the atom I/O contracts it consumes and directly benefits its re-plan path.

## Problem

Blog images read as off-topic. Root cause is a capability + information mismatch, not a rendering bug:

- The **writer is now a frontier cloud model** (`pipeline_writer_model = anthropic/claude-sonnet-5`) that understands each article deeply — but the writer skill **forbids** it from any image involvement (`skills/content/blog-generation/SKILL.md`: "Do NOT include image descriptions … Images are handled separately").
- All image direction is delegated to a **small local model that never sees the article body**. `image_decision_agent.py` extracts only heading titles (`re.findall(r'^(#{2,3})\s+(.+)$', …)`), and the `image.decision` prompt receives only `{topic}`, `{category}`, and that heading list.
- The **featured image — the most visible one — is built from the topic string only** (`source_featured_image._build_image_gen_prompt(topic, …)`) plus a randomly-rotated art style.
- The one rail that could catch a mismatch, `qa.vision` (`vision_gate`), is **advisory** (`required_to_pass = false`) — it scores and can page, but never blocks a publish.

Evidence (recent published posts, `featured_image_data->>'image_style'`): a _speculative-decoding_ post rendered in "retro 16-bit pixel art"; a _Prefect concurrency bug_ post got "dramatic dark silhouette." The subject is a small-model free-association off the title; the style is a decoupled random draw. The article is smart; the image direction is not.

A second, narrower complaint: **styles cluster**. The pool has 13 curated styles but selection is `random.choice(pool − last-5)`, so by chance some recur (glassmorphism 4×, silhouette 3×) while others languish (line-art 1×).

## Decisions (operator, 2026-07-10)

1. **The writer nominates the images.** Reverse the ban: the writer (Sonnet) places inline image markers _and_ one hero-image nomination, grounded in the full article. Cloud is the writer only; everything downstream stays local.
2. **The decision agent stays as a body-fed fallback.** When a draft has no writer markers (a writer miss, `dev_diary`, or a rebuild), the local agent still runs — but now reads section **body** text, not just headings.
3. **Local prompt-builder model → `gemma-4-31B-it-qat`.** Replaces `phi4:14b` for both the decision role and the SDXL-prompt phrasing role. Writer-grade, non-reasoning (clean JSON), same family as the old local writer. Freed VRAM: with a cloud writer, no local writer sits resident during image gen, so a ~30B local here is free headroom.
4. **Style stays decoupled from content; rotation becomes least-recently-used.** Forcing a style to match content is unwanted. The fix for clustering is replacing random-from-non-recent selection with **LRU** so the pool cycles fully before any repeat.

## Goal

Every image depicts a concrete, article-grounded subject. The local model's job shrinks from "invent a subject" to "phrase this given subject in the rotated art style" — a much harder task to drift on. Styles cycle without clustering. No new failure modes for the parallel rebuild feature.

## Design

Six components. The engine (atoms, stages, skills, settings) is unchanged in shape; the changes are (a) who supplies the subject and (b) how the local model and style are chosen.

### 1. Writer contract — `skills/content/blog-generation/SKILL.md`

Replace the image-suppression rules with placement instructions. The writer emits:

- **Inline markers**, on their own line where a visual genuinely helps a section: `[IMAGE: <one concrete, specific subject drawn from this section — an object, diagram, scene, or visual metaphor>]`. **No count is stated in the prompt** (`feedback_no_hardcoded_lengths_in_prompts`) — "only where it adds real value; skip code-heavy or thin sections." A downstream cap (`writer_max_inline_images`, default 3) bounds it.
- **One hero nomination**, as the first line of the draft: `[HERO-IMAGE: <one concrete subject representing the whole post>]`.

The writer describes the **subject only** — not the art style (applied downstream), not an SDXL prompt. The existing brand rule stays: no identifiable people, faces, hands, or text in images.

### 2. Marker normalization + hero extraction — `content.plan_image_markers`

A normalization pass runs first, before the existing `[IMAGE-N]` parse:

1. **Extract the hero:** match the first `[HERO-IMAGE: …]` (case-insensitive), capture the subject onto the returned state as `featured_image_subject`, and strip the line from `content`.
2. **Number inline markers:** convert each `[IMAGE: desc]` → `[IMAGE-N: desc]` sequentially in document order (writer emits unnumbered so it can't fumble numbering/dedup). Enforce the `writer_max_inline_images` cap — keep the first N, strip the remainder.
3. **Fork:** if numbered `[IMAGE-N:]` markers now exist → **writer-primary path** (parse into `image_plans`, existing logic). If none → **fallback** to the body-fed decision agent (§3), exactly as today.

**Idempotency / reset-awareness:** the pass tolerates being re-run on content that has no markers (e.g. `ImageRebuildService` strips `<img>` and calls this atom on bare text) — no markers → fallback, unchanged behavior. The return contract is preserved and only _added to_: `{content, image_plans, featured_image_plan?}` plus the additive `featured_image_subject` key (unknown-key-safe for existing consumers).

### 3. Decision agent as body-fed fallback — `image_decision_agent.py` + `image.decision` skill

When it runs (no writer markers), it now grounds on body text: for each heading, capture the paragraph(s) up to the next heading, truncated to `image_decision_section_body_chars` (default 500), and build `section_list` as title + excerpt. The `image.decision` prompt is updated to include the excerpts and instruct grounding in them. **JSON output shape is unchanged** (`featured` + `inline[]`) — `plan_image_markers` and `ImageRebuildService` depend on it. `gemma-4-31B-it-qat` (non-reasoning) keeps the JSON clean.

### 4. Featured stage grounded on the hero subject — `source_featured_image.py`

`_build_image_gen_prompt(topic, …)` → `_build_image_gen_prompt(subject, …)`. Subject precedence in the stage:

1. `context.get("featured_image_prompt")` — explicit verbatim override, existing seam, unchanged (used as the SDXL prompt directly).
2. else `context.get("featured_image_subject")` — the writer's hero nomination (§2).
3. else `context.get("featured_image_plan", {}).get("prompt")` — the decision agent's featured plan (fallback path).
4. else `topic` — last resort.

The `image.featured_image` skill gains a `{subject}` field (grounded) alongside `{style}`/`{style_tags}`; it depicts the subject _in_ the chosen style. The style is selected via §5.

### 5. Style rotation → least-recently-used — `source_featured_image.py`

Replace `random.choice(pool − last-5)` with LRU:

- Build `style → most-recent published_at` over published posts' `featured_image_data->>'image_style'` (this key is reliably populated; see the `_load_recent_published_styles` history note). Styles never used → treated as infinitely old (top priority).
- Exclude the in-memory `ImageStyleTracker.recent()` set (same-worker short-term dedup); among the rest pick the **oldest** last-use. If the in-memory set excludes everything, ignore it and pick the global LRU. Ties (e.g. multiple never-used) break randomly.
- Record the pick in the tracker + `on_style_picked` (unchanged audit-log emit).

This deterministically spreads across all 13 styles before repeating, keeping style fully decoupled from content. `image_style_dedup_window` becomes vestigial for featured selection (LRU needs no window); left in place, noted as deprecated for this path.

### 6. Local model swap + skill wording — `settings_defaults.py` + live `app_settings`

- `inline_image_prompt_model`: `ollama/phi4:14b` → `ollama/gemma-4-31B-it-qat:latest` (drives both the inline `_try_image_gen` prompt build and the featured `_build_image_gen_prompt`; exact tag per `ollama list`).
- `model_role_image_decision`: `ollama/phi4:14b` → `ollama/gemma-4-31B-it-qat:latest`.
- **Two-step** (`feedback_seed_data_in_baseline_not_new_migrations`): update the defaults in `settings_defaults.py` (fresh installs) **and** `UPDATE` the live `app_settings` rows — the boot seeder is `ON CONFLICT (key) DO NOTHING` and will not touch an existing prod value.
- `image.inline_illustration` skill: minor wording so it depicts the given `{search_query}` subject in `{style}`; signature unchanged.

## Interaction with `ImageRebuildService` (parallel work — contract preservation)

`ImageRebuildService` (`modules/content/image_rebuild_service.py`) strips all `<img>` blocks and re-plans from bare text, so it **always** hits the body-fed decision agent (§3) — meaning this spec's §3 change directly upgrades rebuilt images from heading-only to body-grounded planning. To avoid breaking that consumer, the following are a **stable contract**, guarded by new contract tests:

| Contract                                | Shape to preserve                                                                                                                                  |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `content.plan_image_markers.run(state)` | accepts `{content, topic, category?, site_config}`; returns `{content, image_plans, featured_image_plan?}` (+ additive `featured_image_subject`)   |
| `content.generate_images.run(state)`    | returns `image_results[{num, url, alt_text, source}]`, `source ∈ {"image_gen","pexels","none"}` — the rebuild gate keys on `source == "image_gen"` |
| `content.inject_images.run(state)`      | accepts `{content, image_results, task_id, database_service}` → `{content}`                                                                        |
| `_try_image_gen` / `_try_pexels`        | signatures unchanged (rebuild's `_gen_featured` calls both directly)                                                                               |
| injected inline HTML                    | `<img …>` + optional `<figcaption>` — the rebuild strip regex (`_IMG_BLOCK_RE`) matches this                                                       |

**Accepted divergence (v1):** the rebuild path builds its featured image from `featured_image_plan.prompt` via `_try_image_gen` (the _inline_ builder), so it does **not** pick up the featured LRU rotation (§5) or the writer's hero subject (§4). After this spec it is still body-grounded (via §3) and uses the better model (§6); only the style _treatment_ differs. Unifying is a follow-up, not v1 scope.

**Sequencing:** land `rebuild-images` first; rebase this work onto it; run the contract tests against the real consumer.

## Data / state

- **No schema changes.**
- New `app_settings` (defaults in `settings_defaults.py`): `writer_max_inline_images` (default `3`), `image_decision_section_body_chars` (default `500`). Model pins updated (two-step, §6).
- New pipeline state key: `featured_image_subject`, threaded `plan_image_markers` → `source_featured_image`.

## Error handling / integration risks

- **Markers now appear in early stages.** Writer-placed `[IMAGE:]`/`[HERO-IMAGE:]` are present at `normalize_draft` (5), `writer_self_review` (7), and `quality_evaluation` (11) — before `plan_image_markers` (13) consumes them. Mitigations: `writer_self_review` prompt must **preserve** markers (instruct: do not remove `[IMAGE:]`/`[HERO-IMAGE:]`); `normalize_draft` must not mangle the marker lines; the pattern scorer ignores marker text (strip for scoring, or accept negligible impact). Verify `_cleanup_leaked_descriptions` does not match bracket markers (it targets italic scene text — safe).
- **Writer places no markers** → body-fed decision-agent fallback covers it; no empty-image regression.
- **Writer over-produces** → capped at `writer_max_inline_images`.
- **Hero missing** → featured precedence falls through to decision-agent plan or `topic`.

## Scope

**In:** writer contract; `plan_image_markers` normalization + hero extraction; body-fed decision agent; featured subject grounding; LRU style rotation; local model swap; skill wording; contract tests for the rebuild-consumed atoms; docs (`anti-hallucination.md`, image docs, CLAUDE.md image narrative).

**Out (separate work):**

- Hard-gating `qa.vision` (`vision_gate.required_to_pass = true` + optional regen-on-low-score) — the natural follow-up once grounding proves out.
- Unifying `ImageRebuildService`'s featured path onto the shared LRU + hero-subject builder.
- `dev_diary` writer contract — it has no marker step and rides the body-fed fallback unchanged.
- Retroactive backfill of already-published posts.
- Persisting the writer's image subjects for regen re-hydration — **dropped**: the body-fed decision agent grounds the regen/rebuild paths more simply.

## Testing (TDD)

- **Writer contract:** skill-render assertion that the writer prompt instructs marker + hero placement and forbids a fixed count.
- **Normalization:** `[IMAGE: x]` → `[IMAGE-1: x]`; hero extracted and stripped from body → `featured_image_subject`; cap enforced (extras dropped); no-marker input → fallback invoked; re-run on marker-free content is a no-op fork.
- **Body-fed decision agent:** section extraction includes body excerpts truncated to budget; the rendered `image.decision` prompt contains the excerpts; JSON shape unchanged.
- **Featured subject:** `featured_image_subject` used as the subject; precedence order honored (`featured_image_prompt` verbatim wins; then hero; then plan; then topic).
- **LRU rotation:** given a published-style history, picks the least-recently-used; never-used first; excludes in-memory recent; property test — full pool cycles before any repeat.
- **Model pins:** `settings_defaults` has `gemma-4-31B-it-qat` for both keys.
- **Contract tests (rebuild guard):** the three atoms' I/O shapes + `_try_image_gen`/`_try_pexels` signatures + injected `<img>`/`<figcaption>` HTML shape.
- **Integration:** writer-placed markers survive self-review + scoring and are consumed into `<img>` by node 15.

## Follow-ups

- Flip `vision_gate.required_to_pass = true` (± regen-on-low-score) once grounded images prove out — turns detection into prevention.
- Unify the featured-image builder so `ImageRebuildService` also gets LRU + hero subject.
- Revisit persisting writer subjects only if regen/rebuild consistency becomes a felt need.

## Open items

- None blocking. Confirm defaults: `writer_max_inline_images = 3`, `image_decision_section_body_chars = 500`.
