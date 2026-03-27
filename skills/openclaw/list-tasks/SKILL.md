---
name: list_tasks
description: List content tasks, show the pipeline queue, or check task status. Use when the user says "show tasks", "what's in the queue", "pipeline status", "list posts", or "what's pending".
---

# List Tasks

Retrieves tasks from the content pipeline with optional status filtering.

## Usage

```bash
scripts/run.sh [status] [limit]
```

## Parameters

- status (optional): Filter by status — "pending", "in_progress", "completed", "failed", "approved", "rejected". Leave empty for all.
- limit (optional): Number of tasks to return. Defaults to 20.

## Output

Returns a list of tasks with their IDs, topics, statuses, and timestamps.
