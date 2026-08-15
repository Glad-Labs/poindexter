# Featured-image reliability

How a post ends up with a hero image, and the two ways that used to fail
silently. Companion to [`image-ocr-text-gate.md`](image-ocr-text-gate.md),
which covers the server-side text gate.

## The path

`canonical_blog` reaches the hero through `stage.source_featured_image`
(`modules/content/stages/source_featured_image.py`); `dev_diary` runs the same
node. In order:

1. **Subject** — `_resolve_featured_subject`: the writer's `[HERO-IMAGE:]`
   subject, else the Image Decision Agent's hero plan, else the topic.
2. **Style** — `_select_style_lru` rotates `app_settings.image_styles`
   least-recently-used, so style stays decoupled from content.
3. **Prompt** — `_build_image_gen_prompt` asks `inline_image_prompt_model` for
   one SDXL prompt using the `image.featured_image` skill wording, then
   sanitises the reply (below).
4. **Render** — `_render_image_gen` POSTs to `image_gen_server_url` under
   `gpu.lock("image_gen")`, retried `image_gen_render_attempts` times.
5. **Upload** — R2, then `posts.featured_image_data` carries the
   reproducibility blob (model, seed, prompt, negative prompt, dimensions).

If step 4 yields nothing, the stage returns `ok=True` with no image and emits an
`image_gen_downgrade` finding (routed to Discord). **The post still publishes.**
That is deliberate — a missing hero should not strand finished copy — but it
means the render path has to actually work, because nothing downstream blocks on
it.

## Failure 1 — the retry loop that could never retry

`_try_image_gen_featured` wraps the render in a retry loop whose comment names
the intent exactly: _"The dominant failure is a WINDOW, not a verdict: image-gen
exits and restarts to hand VRAM back for a video render, or the GPU lock times
out under contention. Both clear in seconds."_

Neither was retried. `_render_image_gen` had no exception handling, so a read
timeout or a wedged lock arrived as an **exception** and flew straight past the
`for attempt in ...` loop into the caller's outer `except Exception`. One
attempt, no backoff, hero abandoned. Production trace (task `d297faa0`,
2026-08-15):

```
02:17:55  [IMAGE] Style: risograph print style | image-gen prompt: ...
02:22:58  image-gen featured render failed () — falling back to a Pexels STOCK photo
```

303 seconds is one `image_render_timeout_seconds`, not the configured two
attempts. Seven published posts had shipped with no hero by the time this
surfaced.

**Fix.** `_render_image_gen` now _returns_ transient failures as a value —
`(None, {"transient": True, "failure": "..."})` — so the loop that exists to
survive them can see them:

| Failure                                         | Classification | Why                                          |
| ----------------------------------------------- | -------------- | -------------------------------------------- |
| `httpx.TimeoutException` / `TransportError`     | transient      | server restarting or slow; clears in seconds |
| `GpuLockTimeoutError`                           | transient      | lock contention; the holder drains           |
| `GpuBusyError` (`eta_exceeds_budget`, `no_fit`) | transient      | capacity, clears as the holder finishes      |
| `GpuBusyError` (`game_mode`)                    | **terminal**   | the operator claimed the GPU for hours       |
| HTTP 5xx                                        | transient      | server up but unhealthy                      |
| HTTP 4xx                                        | **terminal**   | verdict on the request                       |
| OCR gate rejection                              | **terminal**   | server already re-rolled its seed budget     |

### The empty error message that hid it

`str(httpx.ReadTimeout())` is the **empty string** — these exceptions carry
their meaning in the type. So `"image-gen featured render failed (%s)"`
rendered as `failed ()`, and the routed finding said the same: a warn-level
alert that fired for weeks and named no cause. `describe_exception()` now falls
back to the type name. **When logging an exception in this area, never
interpolate it bare.**

## Failure 2 — the prompt model planning out loud

Local instruct models frequently answer the `image.featured_image` brief by
_restating it_, and `image_prompt_max_tokens` (150) truncates them before they
reach the actual prompt. The reply went to the diffusion model verbatim:

```
*   Subject: A single polished chrome sphere on a dark mirrored surface...
    *   Style: Risograph print style (grainy texture, halftone dots).
    *   Constraints: No text, no generic tech backgrounds, no hands.
    *   Output format: Single prompt, 1-2 sentences, nothing else.
```

26 of the 39 posts published in the 60 days before the fix carry a scratchpad
like that in `posts.featured_image_data->>'image_gen_prompt'` — SDXL was being
asked to illustrate the word "Constraints".

**Fix.** `services/image_prompt_sanitizer.py::clean_image_prompt` reduces the
reply to the prompt. It lives in `services/` rather than beside the stage
because the inline-image path (`atoms/_image_helpers.py`) has the identical bug,
and **an atom may not import a stage**. Two salvage shapes:

- _prose then scaffolding_ — keep the head, drop from the first scaffolding line;
- _scaffolding only_ — lift the `Subject:` value, which does describe a scene.

Bullet-ness is the primary signal, not the labels: the brief asks for "1-2
sentences, nothing else", so a reply in a markdown **list** is enumerating
requirements, not writing a prompt. Returns `""` when nothing is renderable.

### The fallback must keep the subject

On `""`, and on any prompt-build failure, the caller uses
`subject_fallback_prompt(subject, style, style_tags)`. The previous fallback was
`f"{style}, {style_tags}, no text, no faces"` — **style only** — so every
degraded run produced a handsome image of nothing in particular. An off-topic
hero is its own defect independent of how it was produced, so a subject-less
fallback trades a visible failure for an invisible one.

## Backfilling a published post

`poindexter tasks regen-image <task_id> --which featured --prompt "..."` reaches
published posts: it writes `posts.featured_image_url` and rebuilds the static
export. Two caveats, both long-standing:

- it does **not** bust the Vercel ISR cache — call `trigger_isr_revalidate(<slug>)`
  separately or the page serves the old render indefinitely;
- generation plus rebuild routinely outruns the client timeout. A `ReadTimeout`
  means the rebuild is still running, not that the edit failed — the DB writes
  land first, so verify in the DB rather than retrying.

Find gaps with:

```sql
SELECT slug FROM posts
WHERE status = 'published'
  AND COALESCE(NULLIF(featured_image_url, ''), cover_image_url) IS NULL;
```

`cover_image_url` belongs in the check: `lib/posts.ts::getPostImage` falls back
to it, so a post with only a cover is not missing its hero.

## Settings

| Key                                     | Default | Role                                                    |
| --------------------------------------- | ------- | ------------------------------------------------------- |
| `image_gen_render_attempts`             | 2       | render attempts per hero                                |
| `image_gen_retry_backoff_seconds`       | 3.0     | pause between attempts                                  |
| `image_render_timeout_seconds`          | 300     | per-request render cap                                  |
| `image_featured_stage_overhead_seconds` | 120     | prompt build + lock wait + upload headroom              |
| `image_prompt_max_tokens`               | 150     | prompt-model output cap — too low to survive a preamble |
| `image_stock_fallback_enabled`          | false   | opt-in Pexels hero when image-gen yields nothing        |

`resolve_stage_timeout_seconds` derives the node-timeout floor from the first
four, so the wrapper can never kill a render it asked for.
