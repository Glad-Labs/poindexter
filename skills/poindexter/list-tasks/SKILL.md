---
name: list-tasks
description: List content tasks, show the pipeline queue, or check task status. Use when the user says "show tasks", "what's in the queue", "pipeline status", "list posts", or "what's pending".
---

# List Tasks

Retrieves tasks from the Glad Labs content pipeline via `GET /api/tasks` with optional
status filtering.

## Usage

```bash
scripts/run.sh [status] [limit]
```

## Parameters

- **status** (optional): filter by task status. Valid values:
  - `pending` — queued, waiting for the worker
  - `in_progress` — actively moving through the pipeline
  - `awaiting_approval` — passed QA, in the human review queue at `/pipeline`
  - `published` — approved and live on R2 static export
  - `rejected` — pipeline rejected (off-brand, failed QA after max rewrites, etc.)
  - `failed` — pipeline errored (timeout, worker crash, etc.)
  - `cancelled` — manually cancelled

  Legacy values `completed` and `approved` were removed in the 2026-04 refactor and will return empty results.

- **limit** (optional): max tasks to return. Defaults to 20.

## Output

Each task shows `id`, `task_name`, `topic`, `status`, and `created_at`. The worker
processes tasks serially — a single in_progress task blocks the rest of the queue
until it terminates.
