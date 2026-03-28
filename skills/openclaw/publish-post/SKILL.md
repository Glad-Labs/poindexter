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

- task_id (required): The ID of the task to publish

## Output

Returns the updated task object confirming publication.
