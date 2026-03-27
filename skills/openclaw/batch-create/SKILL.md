---
name: batch_create
description: Create multiple blog posts or content tasks at once. Use when the user says "create several posts", "batch create", "make 5 articles about", or wants multiple topics queued simultaneously.
---

# Batch Create

Creates multiple content tasks in a single API call. Each task enters the pipeline independently.

## Usage

```bash
scripts/run.sh "topic1" "topic2" "topic3" ...
```

## Parameters

- topics (required): One or more topic strings, each as a separate argument. All tasks use "general" category by default.

## Output

Returns the bulk creation response with IDs and statuses for all created tasks.
