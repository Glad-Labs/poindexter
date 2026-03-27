---
name: quality_report
description: Show content quality scores and QA results for completed tasks. Use when the user says "quality report", "show quality scores", "how's the content quality", or "QA results".
---

# Quality Report

Fetches recently completed tasks and displays their quality scores from the QA critique stage.

## Usage

```bash
scripts/run.sh [limit]
```

## Parameters

- limit (optional): Number of completed tasks to show. Defaults to 10.

## Output

Returns completed tasks with their quality scores, QA feedback, and overall content ratings.
