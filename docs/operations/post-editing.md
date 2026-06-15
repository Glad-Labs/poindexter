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

## MCP parity

The same edits from the bot / Claude surface:

- `edit_post_body(task_id, find?, replace?, new_content?)`
- `replace_post_image(task_id, which, url)`
- `regen_post_image(task_id, which, prompt)`

(`which` = `featured` or `inline:N`.)

## API parity

`task_id`-keyed REST routes (OAuth2 client-creds, like the rest of `/api/tasks`):

- `POST /api/tasks/{task_id}/edit-body` — body `{new_content}` **or** `{find, replace}`
- `POST /api/tasks/{task_id}/replace-image` — body `{which, url}`
- `POST /api/tasks/{task_id}/regen-image` — body `{which, prompt}`

Each returns `{ok, field, detail, warnings, new_url}`.

## Auditability

Every edit writes an `audit_log` row so the change is traceable:

| Action           | `event_type`         |
| ---------------- | -------------------- |
| body edit        | `post_edit_body`     |
| image URL swap   | `post_image_replace` |
| image regenerate | `post_image_regen`   |

Each row records the `task_id`, the field touched, and (for body edits) the
before/after length plus any validator warnings.
