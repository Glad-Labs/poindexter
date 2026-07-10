# `poindexter tasks rebuild-images` — rebuild all images for a draft

**Status:** Design — approved in principle (operator, 2026-07-09), pending spec review.
**Scope:** `canonical_blog` drafts at `awaiting_approval`. One CLI command that rebuilds every image on a draft.
**Related, out of scope:** the component-scoped `preview_gate` regen (`poindexter pipeline regen --images`) — same _intent_, but only works on a task paused mid-graph at `preview_gate` (seeded disabled). This design serves the already-finished `awaiting_approval` draft, which has no live checkpoint to resume.

## Problem

An operator reviewing a draft often finds the images wrong while the text is fine. The two existing paths do not cover "rebuild every image, without typing prompts, on a finished draft":

| Mechanism                                                      | All images at once?        | No prompt typed?       | Works on an `awaiting_approval` draft?                |
| -------------------------------------------------------------- | -------------------------- | ---------------------- | ----------------------------------------------------- |
| `poindexter tasks regen-image` (`PostEditService.regen_image`) | ❌ one `--which` at a time | ❌ requires `--prompt` | ✅                                                    |
| `poindexter pipeline regen --images` (`regen_at_gate`)         | ✅ re-plans from text      | ✅                     | ❌ requires a live pause at `preview_gate` (disabled) |

The motivating case: the "Breaking Out of the RAG Echo Chamber…" draft (`task_id f274dc44-…`, `awaiting_approval`). All 4 of its images — 1 featured + 3 inline — are **Pexels stock photos** whose `alt` text describes the stock photo ("Drafting compass on blueprint", "pink-and-white cubes", "Close-up of a fly…"), not the article. So "reuse the existing prompt" would regenerate a fly, not something about RAG.

### Why the images were stock in the first place

`content.generate_images` tries **image-gen (SDXL/Z-Image) first** and falls back to **Pexels only when image-gen returns nothing** (`content_generate_images.py:82-116`). The RAG post went all-Pexels because image-gen produced nothing at generation time (server down/degraded — a recurring failure) and it **silently** fell back. That silent fallback is exactly what this feature must not repeat (`feedback_no_silent_defaults`).

## Decisions (operator, 2026-07-09)

1. **Prompt source = re-plan from the article.** Reuse the pipeline's own image logic to derive fresh, article-relevant image descriptions from the current text. Not the stale alt text; not one shared prompt.
2. **Command surface = a new `poindexter tasks rebuild-images <task_id>`.** Not an `--all` mode on `regen-image`; not an extension of `pipeline regen`.
3. **Stock fallback = fail-loud by default, `--allow-stock` to opt in.** If any slot would fall back to a Pexels stock photo, abort and change nothing unless `--allow-stock` is passed.

## Goal

One command rebuilds **all** images on an `awaiting_approval` draft — the featured image and every inline image — re-planning each prompt from the article text, preferring generated (SDXL/Z-Image) images, with **zero prompts typed** and **no silent stock fallback**.

```
poindexter tasks rebuild-images f274dc44
→ stripped 4 old images (1 featured + 3 inline)
→ re-planned 3 inline + featured from the article
→ generated 4 images (SDXL) — 0 stock fallbacks
→ draft f274dc44 updated (pipeline_versions v1)
```

## Design

All orchestration lives in a **content-module service**; the CLI is a thin adapter (`feedback_cli_first`, transport-adapter contract). The service reuses the three existing image atoms rather than reimplementing image logic (`feedback_no_wheel_reinvention`).

### Service — `modules/content/image_rebuild_service.py`

`ImageRebuildService.rebuild_all_images(task_id, *, allow_stock: bool) -> RebuildResult`

Constructed by the HTTP route from `app.state` deps (`pool`, `site_config`, `image_service`, `database_service`, `platform` / `kernel_platform`) — exactly as the route builds `PostEditService` today. No separate upload step is needed: `_try_image_gen` (used for both inline and featured) already uploads to R2 and returns the final servable URL, and the service writes `content` + `featured_image_url` together in a single `pipeline_versions` UPDATE.

Flow:

1. **Load & guard.** Read the latest `pipeline_versions` row (`content`, `version`, `featured_image_url`). Require `pipeline_tasks.status = 'awaiting_approval'` — drafts only, matching `PostEditService`'s scope. Any other status fails loud with a clear message.
2. **Strip.** Remove existing inline `<img …>` tags (and any Pexels `<figcaption>` siblings) from the body so it is marker-free; discard the old featured URL. Without this, `plan_image_markers` (which only plans when no `[IMAGE-N]` markers exist) would add markers _alongside_ the surviving `<img>` tags.
3. **Re-plan (no typing).** Run `content.plan_image_markers` with `{content, topic, category, site_config}` (`category` from the task's niche, defaulting to the atom's own `"technology"` default). It injects `[IMAGE-N: desc]` markers into the body and returns `image_plans = [{num, desc}, …]` plus an optional `featured_image_plan`. This is the Image Decision Agent — the "no prompt typed" source.
4. **Generate.** Run `content.generate_images` for the inline plans (image-gen first, Pexels fallback). Generate the featured image from `featured_image_plan` with the **same two-strategy generation** — `_try_image_gen` then `_try_pexels` — so the featured yields the same `source ∈ {image_gen, pexels, none}` signal as an inline slot, and the fail-loud gate below treats every slot uniformly. (This is why the featured does _not_ reuse `regen_image`'s image-gen-only path, which has no fallback and would make `--allow-stock` a no-op for the featured.)
5. **Fail-loud gate — generate, then gate, then persist.** After all generation, inspect the `source` of **every slot (featured + each inline)**. If `allow_stock` is false and any slot's `source != "image_gen"` (Pexels fallback _or_ nothing produced), **abort before any persist** — the draft is byte-unchanged — and report which slots would be stock. With `--allow-stock`, proceed with whatever was produced (still fail loud only if a slot produced _nothing at all_, since an empty slot cannot be persisted). Generate-then-gate is unavoidable (image-gen health is unknowable until attempted); R2 uploads / `media_assets` rows orphaned by an abort are harmless (see Error handling).
6. **Inject & persist.** Run `content.inject_images` (`[IMAGE-N]` → `<img>`), then write the new `content` and `featured_image_url` back to the **same latest** `pipeline_versions` row in place (matching `PostEditService`). Write one `audit_log` row (`event_type = "post_images_rebuild"`) recording per-slot sources, counts, `allow_stock`, and the attempt number. Bump `pipeline_tasks.regen_images_attempts` for observability.

### Route + CLI — `poindexter tasks rebuild-images <task_id> [--allow-stock] [--json]`

Follows the **same HTTP-adapter shape as the sibling `tasks regen-image` / `replace-image` commands**, not the in-process `pipeline` pattern. `rebuild-images` re-runs three atoms _fresh_ — it never resumes a LangGraph checkpoint — so it has none of the checkpointer constraint that forces `pipeline` commands in-process, and putting an in-process command inside the otherwise-HTTP `tasks` group would deepen the `tasks`↔`pipeline` seam-blur.

- **Route:** `POST /api/tasks/{task_id}/rebuild-images` (body `{allow_stock: bool}`) in `routes/task_publishing_routes.py`, next to `regen_task_image`. It constructs `ImageRebuildService` from `app.state` deps and returns the `RebuildResult` as JSON.
- **CLI:** a thin `_post_edit`-style client (like `tasks regen-image`) with its own tunable client timeout `post_edit_rebuild_images_timeout_s` (default generous, e.g. 600s — the multi-image run is longer than a single `regen-image`, which already uses `post_edit_regen_image_timeout_s`). Image-gen is `await`ed, so the worker stays non-blocking during the long request.

Business logic stays entirely in the service, so the adapter-contract rule holds and the MCP surface can wrap the same route/service later.

### Reuse map

| Reused                                                        | From                                                  | For                                        |
| ------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------ |
| `content.plan_image_markers`                                  | `modules/content/atoms/content_plan_image_markers.py` | re-plan descriptions from text             |
| `content.generate_images`                                     | `modules/content/atoms/content_generate_images.py`    | image-gen-first generation + Pexels record |
| `content.inject_images`                                       | `modules/content/atoms/content_inject_images.py`      | `[IMAGE-N]` → `<img>`                      |
| `_try_image_gen` (uploads + returns final URL), `_try_pexels` | `modules/content/stages/replace_inline_images.py`     | two-strategy featured generation           |
| route → service construction from `app.state`                 | `routes/task_publishing_routes.py::regen_task_image`  | HTTP adapter pattern (sibling command)     |
| `_post_edit` CLI HTTP client + `--json` render                | `poindexter/cli/tasks.py::tasks_regen_image`          | thin CLI adapter                           |

## Data / state

- Writes: the latest `pipeline_versions` row's `content` + `featured_image_url` (in place), one `audit_log` row, `pipeline_tasks.regen_images_attempts += 1`, and `media_assets` rows for generated inline images (via `generate_images`).
- Does **not** touch `posts` (draft is pre-publish) or trigger a static rebuild.
- Re-planning may change _how many_ inline images exist and where — expected; it is a fresh plan, not a 1:1 swap.

## Error handling

- **Wrong status** (not `awaiting_approval`) → fail loud, no change.
- **Fail-loud gate** (a slot would be stock, no `--allow-stock`) → abort before persist; the draft is unchanged. Orphaned R2 objects / `media_assets` rows from the aborted generation are acceptable (R2 is cheap; `media_assets` is an append log) and are noted, not cleaned up in v1.
- **No image service / platform wired** → fail loud (matches `PostEditService.regen_image`).
- **Partial inline generation with `--allow-stock`** → proceed; the audit row records the mix.

## Scope

**In:** `awaiting_approval` drafts; featured + all inline; in-place update of the latest `pipeline_versions`; audit row; the `POST /api/tasks/{id}/rebuild-images` route + the `poindexter tasks rebuild-images` CLI client (`feedback_cli_first`).

**Out (separate work):**

- Published-post editing and live-site sync (poindexter#523).
- An **MCP tool** wrapping the route (the HTTP route itself ships in v1 as the CLI's backend; the MCP surface is a cheap follow-up over the same route/service).
- Enabling / fixing `preview_gate` and its `pipeline regen` path.
- A hard attempt-cap: `regen_images_attempts` is bumped for observability, but this operator-initiated command is **not** capped (the operator is the loop bound), unlike the automated gate.

## Testing (TDD)

Service-level, with fakes for the atoms/handles where the atoms' own tests already cover internals:

- **Guard:** non-`awaiting_approval` status → raises, no writes. _(regression guard)_
- **Strip:** existing `<img>` tags + Pexels `<figcaption>` removed before re-plan; a body with no images still works.
- **Re-plan:** `plan_image_markers` invoked with the stripped body; markers/plans surfaced.
- **Fail-loud gate:** when a fake `generate_images` returns a `pexels` source and `allow_stock=False` → aborts, `pipeline_versions` byte-unchanged, no audit row; with `allow_stock=True` → persists and the audit row records the mix. A `none` (nothing produced) slot aborts even with `allow_stock=True` (an empty slot cannot be persisted).
- **Uniform gate across slots:** a stock _featured_ with `allow_stock=False` aborts the same as a stock inline slot.
- **Happy path:** all-`image_gen` results → `inject_images` output written back, `featured_image_url` set, audit row shape (sources, counts, attempt), `regen_images_attempts` incremented.
- **Featured included:** featured regenerated from `featured_image_plan`, not left as the old URL.
- **Route:** `POST /api/tasks/{id}/rebuild-images` constructs the service from `app.state`, threads `allow_stock`, returns the `RebuildResult` (mirrors the `regen_task_image` route test's fake-service pattern).
- **CLI adapter:** thin HTTP client posts to the route, renders success + fail-loud messaging; `--json` shape; honors `post_edit_rebuild_images_timeout_s`.

## Open items confirmed as defaults (operator, 2026-07-09)

- Featured image **is** included in "all images". ✅
- Scope is `awaiting_approval`-only for v1. ✅
- **No** hard attempt-cap on the command. ✅
