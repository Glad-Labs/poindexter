# Task unapprove path — design

## Problem

An operator who clicks **approve** on a `pipeline_tasks` row can't undo it
through any supported path. `approve` only _stages_ a task (per
`feedback_approve_does_not_mean_publish`) — it hasn't published — but both
existing reject code paths refuse to touch a task once it's `approved`:

- `routes/approval_routes.py::reject_task` (`POST /{task_id}/reject`) —
  hard-requires `status == "awaiting_approval"`, 409s otherwise.
- `routes/task_publishing_routes.py::approve_task` (`POST /{task_id}/approve`
  with `approved=false` — the "combined approve/reject handler") — allows
  `awaiting_approval` or `completed`, still 409s on `approved`.

There is no CLI command or MCP tool that reverts `approved` back to
`awaiting_approval` or to `rejected`. Today the only fix is a manual DB
`UPDATE`.

## Why this is more than a status flip

`approve_task` (task_publishing_routes.py, lines ~494-528) does more than set
`pipeline_tasks.status='approved'`. When approved without `auto_publish` and
without a `publish_at` slot, it calls
`publish_service.publish_post_from_task(..., stage_only=True)`, which inserts
a real row into `posts` at `status='approved'` (linked via
`posts.metadata->>'pipeline_task_id'`) so `poindexter schedule batch` can pick
it up later — `schedule batch` reads `posts` directly, not `pipeline_tasks`.

If "unapprove" only reverted `pipeline_tasks.status`, that staged `posts` row
would be orphaned — still `status='approved'`, still eligible for
`schedule batch` — so a task the operator believes they've walked back could
still get published. Unapprove must also delete that staged row and clear any
`scheduled_at` slot set via the `publish_at` param. (Confirmed with the user —
this cleanup is in scope, not deferred.)

## Approach

Two options existed:

- **(a)** Relax the existing reject endpoints to also accept `approved` as a
  starting status.
- **(b)** Add a dedicated `poindexter tasks unapprove <task_id>` CLI command +
  MCP tool + REST endpoint.

**Going with (b).** There are two different, differently-shaped reject code
paths already (see above); relaxing both to handle a starting status they
were never designed around — plus threading the new posts/scheduled_at
cleanup into each — is riskier and messier than one small, self-contained
addition. (b) also matches `feedback_cli_first` (CLI primary, MCP/REST
secondary) directly, and the existing `reject_task`/`approve_task` endpoints
stay untouched — zero regression risk to their existing test coverage.

## Design

### Service function — `services/publish_service.py::unapprove_task`

Placed next to the existing `unpublish_post` (the closest sibling: an
existing "reverse a publish-adjacent transition, keep `pipeline_tasks` +
`posts` in lockstep, one transaction" function). Signature — returns a plain
`dict[str, Any]`, matching `unpublish_post`'s own return convention (no new
result type introduced):

```python
async def unapprove_task(
    pool: Any,
    task_id: str,
    *,
    target_status: str = "awaiting_approval",  # or rejected_retry | rejected_final
    feedback: str | None = None,
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    ...
    # returns e.g. {"ok": True, "new_status": "awaiting_approval",
    #               "posts_row_removed": True, "reason": None}
```

Behavior, in order:

1. **Transaction** (mirrors `unpublish_post`'s idiom):
   - `UPDATE pipeline_tasks SET status = $2, scheduled_at = NULL, updated_at = NOW() WHERE task_id = $1 AND status = 'approved'` — the `WHERE status='approved'` guard makes this idempotent (checked via `UPDATE 1` vs `UPDATE 0`, same pattern as `unpublish_post`).
   - If that didn't match a row, return early (`ok=False, reason="not_approved"`) — no posts touched.
   - `DELETE FROM posts WHERE metadata ->> 'pipeline_task_id' = $1 AND status = 'approved'` — only ever removes a staged-but-never-published row (a published row has `status='published'`, never matched).
2. **Best-effort audit trail** (outside the hard transaction, individually
   try/excepted — mirrors `reject_task`'s existing style exactly, so a
   logging/audit hiccup never masks the core status flip that already
   succeeded):
   - `PipelineDB(pool).clear_qa_approved_snapshot(task_id)`
   - `pipeline_gate_history` insert — `gate_name="final_approval"`,
     `event_kind` = `target_status` when it's a `rejected_*` value, else the
     bare `"rejected"` (the `content_tasks` view's scalar subqueries filter
     `event_kind = ANY('approved','rejected','rejected_retry','rejected_final')`
     — a novel value like `"unapproved"` would be silently invisible to that
     view, so the plain-revert case reuses the existing `"rejected"` value:
     the approval **was** reversed, which the audit trail should reflect even
     though the task itself lives on for a fresh review pass).
   - `router_outcome_feedback.record_task_outcome(pool, task_id, decision="rejected")`
   - `db_service.mark_model_performance_outcome(task_id, human_approved=False)`
3. Return the result dict (see signature above).

### Route — `POST /api/tasks/{task_id}/unapprove` (`routes/approval_routes.py`)

Co-located with `reject_task` (the module already "owns rejection"). Thin —
no inline SQL (the CI adapter-purity ratchet fails on _new_ inline SQL in
`routes/`; that's the reason the SQL lives in the service function, not
copy-pasted into the route the way `reject_task`'s pre-existing insert does):

1. Resolve/canonicalize `task_id` (identical prefix-resolution dance as
   `reject_task`).
2. `current_status != "approved"` → 409 (`"Cannot unapprove task with status
'{current_status}' — expected 'approved'"`).
3. Parse body:
   ```python
   class UnapproveRequest(BaseModel):
       to: Literal["awaiting_approval", "rejected_retry", "rejected_final"] = "awaiting_approval"
       feedback: str | None = None
   ```
4. Call `unapprove_task(...)`, return:
   ```json
   {
     "task_id": "...",
     "status": "awaiting_approval",
     "previous_status": "approved",
     "posts_row_removed": true,
     "message": "Task reverted to awaiting_approval."
   }
   ```

### CLI (primary) — `poindexter/cli/tasks.py`

```
poindexter tasks unapprove <task_id> [--to awaiting_approval|rejected_retry|rejected_final] [--feedback TEXT]
```

- Default `--to awaiting_approval`.
- `--feedback` required only when `--to` targets a `rejected_*` value
  (matches `tasks reject`'s existing requirement); optional for a plain
  revert (falls back to a generic note, e.g. `"Unapproved by operator"` — no
  fabricated reason, per `feedback_no_hardcoded_lengths_in_prompts` /
  `feedback_no_dummy_data`, just an honest description of what happened).
- Posts to the new `/unapprove` route via the existing `WorkerClient` /
  `_post_action`-style helper.

### MCP tool (secondary) — `mcp-server/server.py`

```python
@mcp.tool()
async def unapprove_post(task_id: str, to: str = "awaiting_approval", feedback: str = "") -> str:
    """Revert an approved-but-not-yet-published task (undo an accidental approve)."""
```

Thin wrapper over the same route, mirrors `reject_post`'s shape.

## Out of scope

- Touching `reject_task` / `approve_task` at all — no relax, no shared
  helper extraction from them. New code only.
- The operator console UI (no button added) — CLI/MCP/REST only, per the
  issue's ask.
- Any change to `schedule batch` itself.

## Testing

- **Service-level** (new file `tests/unit/services/test_publish_service_unapprove.py`,
  mirroring `test_publish_service_unpublish.py`'s `_make_pool`
  transaction-mock pattern and naming convention):
  - Reverts `approved` → `awaiting_approval` (default), clears
    `scheduled_at`, deletes a matching staged `posts` row.
  - Reverts `approved` → `rejected_final` / `rejected_retry` (the
    "approved → reject" transition explicitly asked for in the issue).
  - Idempotent no-op when task isn't currently `approved`.
  - No-op posts delete when no staged row exists (auto_publish / publish_at
    path never created one) — not an error.
- **Route-level** (`tests/unit/routes/test_approval_routes.py`, using the
  `APPROVED_TASK` fixture already defined there and currently unused):
  - `POST /unapprove` on an `approved` task → 200, default target.
  - `POST /unapprove` with `to=rejected_final` → 200, status reflects it.
  - `POST /unapprove` on a non-`approved` task → 409.
  - 404 / ambiguous-prefix handling mirrors the existing `reject_task` tests.
