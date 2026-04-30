---
name: approve-post
description: Approve a content task for publishing. Use when the user says "approve task", "approve post", "looks good, approve it", or "approve [id]".
---

# Approve Post

Approves a content task, moving it forward in the pipeline toward publishing.

## Usage

```bash
scripts/run.sh "task_id" [publish_at]
```

## Parameters

- **task_id** (string, required): The ID of the task to approve. Accepts a full UUID, a numeric legacy ID, or a short UUID prefix of 6 or more characters — the route resolves the prefix against `pipeline_tasks` for ergonomics.
- **approved** (boolean, optional): `true` to approve, `false` to reject. Defaults to `true`. Pass `false` to reject inline (equivalent to the `reject` endpoint).
- **human_feedback** (string, optional): Free-form reviewer notes captured on the task.
- **reviewer_id** (string, optional): Identifier of the reviewer recorded with the approval.
- **featured_image_url** (string, optional): Override the featured image URL before publish.
- **image_source** (string, optional): Source of the featured image — `pexels` or `sdxl`.
- **auto_publish** (boolean, optional): When `true` (default), approval also publishes immediately. Set `false` to approve without publishing — call the `publish-post` skill separately.
- **publish_at** (ISO-8601 string, optional): When set together with `auto_publish=true`, the task is scheduled rather than published immediately. The value is parsed as ISO-8601 (`Z` suffix accepted) and stored as `scheduled_at`; the scheduled publisher job picks it up at that time. If parsing fails the task is published immediately and a warning is logged.

## Output

Returns the updated task object confirming the approval. Status will be `approved`, `published`, or `scheduled` depending on `auto_publish` and `publish_at`.
