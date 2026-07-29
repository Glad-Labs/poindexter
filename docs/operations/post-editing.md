# Editing drafts — body & images

Operators can edit an `awaiting_approval` draft's body text and images directly,
with no hand-written SQL. Three surfaces share one service
(`modules/content/post_edit_service.py`): the CLI is primary; the MCP tools and
the REST API are parity wrappers over the same routes.

> **Drafts only.** These commands operate on the latest `pipeline_versions.content`
> row for a task in `awaiting_approval` (or earlier), keyed by `task_id`. Editing
> an already-**published** post is out of scope — that needs an H1-title
> re-derivation, a static-export rebuild, and tag revalidation (tracked as the
> deferred follow-up on poindexter#523).

## CLI (`poindexter tasks …`)

The edit commands sit alongside the approval workflow (`tasks approve` / `reject`),
so the operator flow is one coherent noun:

```bash
poindexter tasks list                         # find the draft
poindexter tasks get <task_id>                # review it
poindexter tasks edit-body <task_id> ...      # fix the text
poindexter tasks replace-image <task_id> ...  # swap an image
poindexter tasks approve <task_id>            # ship it
```

`<task_id>` accepts the full UUID or the 8-char prefix shown by `tasks list`.

### `tasks edit-body`

```bash
# Surgical find/replace (no editor):
poindexter tasks edit-body <task_id> --find "[memory/notes.md] " --replace ""

# Open the current body in $EDITOR; saving writes back what you edited:
poindexter tasks edit-body <task_id>
```

- `--find` (with optional `--replace`, default = delete the match) does a
  server-side substring replace on the draft body.
- With no `--find`, the current body opens in `$EDITOR`; saving submits the whole
  edited body. Saving with no changes writes nothing.
- After every body edit the anti-hallucination **validator re-runs, warn-only** —
  warnings print (`⚠ …`) but the edit still applies. You are the human approval
  gate; the validator advises, it does not block.

### `tasks replace-image`

```bash
poindexter tasks replace-image <task_id> --which featured  --url https://…/cover.webp
poindexter tasks replace-image <task_id> --which inline:2  --url https://…/fig2.webp
```

- `--which featured` swaps the draft's featured image.
- `--which inline:N` rewrites the `src` of the N-th inline `<img>` in the body
  (1-based).

### `tasks regen-image`

```bash
poindexter tasks regen-image <task_id> --which featured --prompt "a teal server rack, isometric, no people"
poindexter tasks regen-image <task_id> --which inline:1 --prompt "…"
```

Generates a fresh image via the image capability (honoring the configured
no-humans / on-topic negative prompt), uploads it to object storage (R2), and
swaps it in. Requires the image service to be reachable; otherwise the command
reports a 503.

### `tasks remove-image`

```bash
poindexter tasks remove-image <task_id> --which featured
poindexter tasks remove-image <task_id> --which inline:2
```

- `--which featured` clears the draft's featured image to none — no
  auto-promote of an inline image into its place.
- `--which inline:N` strips that `<img>` tag from the body entirely.
  Indices aren't stored — `inline:N` is always counted live off the current
  body, so a later image renumbers down automatically after a removal.

### `tasks add-image`

```bash
poindexter tasks add-image <task_id> --section "Benchmarks"
poindexter tasks add-image <task_id> --after inline:2 --prompt "a teal server rack, isometric, no people"
```

Generates a new image via the image capability and inserts it into the
draft — the counterpart to `replace-image`/`regen-image`, which can only
operate on a slot that already exists.

- Exactly one of `--after inline:N` (insert right after that existing
  image) or `--section <heading>` (insert after that H2-H4 heading's
  paragraph, fuzzy-matched) positions the new image.
- `--prompt` is optional. Without it, the prompt is derived from the target
  section's own heading text — for `--after`, the nearest heading before
  the anchor image; for `--section`, the heading you named (operator
  preference: image prompts come from headings, not typed by hand). If
  neither a prompt nor a derivable heading exists, the command asks for
  `--prompt` explicitly rather than generating off a blank prompt.
- Requires the image service to be reachable; otherwise the command
  reports a 503.

### `tasks rebuild-images`

```bash
poindexter tasks rebuild-images <task_id>                # fail loud if any slot would be stock
poindexter tasks rebuild-images <task_id> --allow-stock  # accept Pexels fallback
```

Rebuilds **every** image on the draft — the featured image and all inline images
— in one shot. It re-plans each image's prompt from the article text (the same
Image Decision Agent the pipeline uses), so **no prompts are typed**, and
regenerates. Prefers generated (SDXL/Z-Image) images.

- **Fail-loud by default.** If image-gen can't produce a slot and it would fall
  back to a Pexels stock photo, the command **aborts and changes nothing**,
  naming the affected slots. Pass `--allow-stock` to accept the fallback. (A slot
  that produces _nothing at all_ always aborts, even with `--allow-stock`.)
- `--allow-stock` is a **per-run override of the global
  `image_stock_fallback_enabled`** (default `false`, 2026-07-28). With the
  global off, the image atoms won't produce a stock substitute at all — so the
  flag has to reach the generation sites, not just relax the gate, or it would
  quietly stop meaning what it says. Either way a stock slot emits a
  warn-severity `image_gen_downgrade` finding: an opted-in downgrade is still a
  downgrade, and this whole class of defect stayed invisible for weeks because
  the fallback reported success.
- Re-planning may change **how many** inline images the post has — it is a fresh
  plan, not a 1-for-1 swap.
- The multi-image run is longer than a single `regen-image`; the CLI wait is
  tunable via `post_edit_rebuild_images_timeout_s` (default 600s).

## MCP parity

The same edits from the bot / Claude surface:

- `edit_post_body(task_id, find?, replace?, new_content?)`
- `replace_post_image(task_id, which, url)`
- `regen_post_image(task_id, which, prompt)`
- `remove_post_image(task_id, which)`
- `add_post_image(task_id, after?, section?, prompt?)` — exactly one of
  `after`/`section` required

(`which` = `featured` or `inline:N`.)

## API parity

`task_id`-keyed REST routes (OAuth2 client-creds, like the rest of `/api/tasks`):

- `POST /api/tasks/{task_id}/edit-body` — body `{new_content}` **or** `{find, replace}`
- `POST /api/tasks/{task_id}/replace-image` — body `{which, url}`
- `POST /api/tasks/{task_id}/regen-image` — body `{which, prompt}`
- `POST /api/tasks/{task_id}/remove-image` — body `{which}`
- `POST /api/tasks/{task_id}/add-image` — body `{after?, section?, prompt?}`

These five each return `{ok, field, detail, warnings, new_url}`.

- `POST /api/tasks/{task_id}/rebuild-images` — body `{allow_stock}` — returns
  `{ok, task_id, detail, inline_total, inline_generated, featured_source, stock_slots}`
  (no MCP wrapper yet; CLI + API only).

## Auditability

Every edit writes an `audit_log` row so the change is traceable:

| Action             | `event_type`          |
| ------------------ | --------------------- |
| body edit          | `post_edit_body`      |
| image URL swap     | `post_image_replace`  |
| image regenerate   | `post_image_regen`    |
| image remove       | `post_image_remove`   |
| image add          | `post_image_add`      |
| bulk image rebuild | `post_images_rebuild` |

Each row records the `task_id`, the field touched, and (for body edits) the
before/after length plus any validator warnings.
