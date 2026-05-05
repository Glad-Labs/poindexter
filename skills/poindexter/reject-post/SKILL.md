---
name: reject-post
description: Reject a content task and send it back for revision. Use when the user says "reject task", "reject post", "send it back", "needs more work", or "reject [id]".
---

# Reject Post

Rejects a content task, marking it for revision or removal from the pipeline.

## Usage

```bash
scripts/run.sh "task_id" "reason"
```

## Parameters

- **task_id** (string, required): The ID of the task to reject. Accepts a full UUID, a numeric legacy ID, or a short UUID prefix of 6 or more characters.
- **reason** (string, optional): Explanation for the rejection, captured on the task record.

## Output

Returns the updated task object confirming the rejection.
