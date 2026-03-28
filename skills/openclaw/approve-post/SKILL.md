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

## Parameters

- task_id (required): The ID of the task to approve

## Output

Returns the updated task object confirming the approval.
