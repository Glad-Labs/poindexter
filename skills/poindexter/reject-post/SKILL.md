---
name: reject-post
description: Reject a content task and send it back for revision. Use when the user says "reject task", "reject post", "send it back", "needs more work", or "reject [id]".
---

# Reject Post

Rejects a content task, marking it for revision or removal from the pipeline.

## Usage

```bash
scripts/run.sh "task_id" "reason" [feedback]
```

## Parameters

- **task_id** (string, required): The ID of the task to reject. Accepts a full UUID, a numeric legacy ID, or a short UUID prefix of 6 or more characters.
- **reason** (string, required): Explanation for the rejection, captured on the task record.
- **feedback** (string, optional): Detailed reviewer feedback for the revision. Defaults to the reason when omitted.
- **allow_revisions** (boolean, optional): Whether the task may be sent back for revision. Defaults to `true` (server-side).

## Output

Returns the updated task object confirming the rejection.
