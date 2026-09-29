---
name: approve-post
description: Approve a content task for publishing. Use when the user says "approve task", "approve post", "looks good, approve it", or "approve [id]".
---

# Approve Post

Approves a content task, moving it forward in the pipeline toward publishing.

## Usage

```bash
scripts/run.sh "task_id"
```

The script sends a plain approve: it stages the task and nothing else.

## Parameters

- **task_id** (string, required): The ID of the task to approve. Accepts a full UUID, a numeric legacy ID, or a short UUID prefix of 6 or more characters — the route resolves the prefix against `pipeline_tasks` for ergonomics.

The route behind it, `POST /api/tasks/{task_id}/approve`, also takes these JSON body fields when called directly:

- **approved** (boolean, optional): `true` to approve, `false` to reject. Defaults to `true`. Pass `false` to reject inline (equivalent to the `reject` endpoint).
- **human_feedback** (string, optional): Free-form reviewer notes captured on the task.
- **reviewer_id** (string, optional): Identifier of the reviewer recorded with the approval.
- **featured_image_url** (string, optional): Approve the post with this featured image instead of the pipeline's. It is applied before the approval commits, through the same writer as `POST /api/tasks/{task_id}/replace-image` (`which: featured`), so the post the approval creates carries it. A value the writer refuses returns 400 and leaves the task unapproved. Ignored when `approved` is `false`. To change the image after approving, use `replace-image`.
- **image_source** (string, optional): Where that image came from (e.g. `pexels`, `image_gen`). Recorded on the approval record.
- **auto_publish** (boolean, optional): Defaults to `false`. Approving a task **stages** it — it does NOT publish by default. To publish, either pass `auto_publish=true` or call the `publish-post` skill separately. (This matches the deliberate "approve != publish" behavior.)
- **publish_at** (string, optional): Approve and queue the post to publish at this time. Takes ISO 8601, `now`, `tomorrow 9am` or `next monday 14:00`; clock words are read in the operator's timezone. It cannot be combined with `auto_publish=true`, and an unparseable value returns 400 before anything changes.

## Output

Returns the updated task object confirming the approval. The status is `approved` (the post is staged), or `published` with `auto_publish=true`. With `publish_at`, `scheduled_for` is the slot the server committed; when it's null, `message` says why the post wasn't scheduled.
