---
name: publish-post
description: Manually publish a completed content task. Use when the user says "publish post", "push it live", "publish task", or "publish [id]".
---

# Publish Post

Manually triggers publishing for a content task that has completed the pipeline.

## Usage

```bash
scripts/run.sh "task_id"
```

## Parameters

- **task_id** (string, required): The ID of the task to publish. Accepts a full UUID, a numeric legacy ID, or a short UUID prefix of 6 or more characters.

## Output

Returns the updated task object confirming publication.
