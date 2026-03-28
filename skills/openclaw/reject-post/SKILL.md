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

- task_id (required): The ID of the task to reject
- reason (optional): Explanation for the rejection

## Output

Returns the updated task object confirming the rejection.
