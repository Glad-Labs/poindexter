# OpenClaw Skills Reference

This document describes all 10 OpenClaw skills available for the Glad Labs content pipeline. Each skill maps natural language commands to FastAPI API calls.

All skills authenticate with `Authorization: Bearer $GLADLABS_API_TOKEN` and target `$GLADLABS_API_URL` (configured in `~/.openclaw/openclaw.json`).

---

## 1. create-post

**Trigger phrases:** "write a post about...", "create post about...", "new article on..."

**What it does:** Creates a new content task and kicks off the 6-stage pipeline (Research, Creative Draft, QA Critique, Creative Refinement, Image Selection, Publishing Prep).

**API call:** `POST /api/tasks`

**Parameters:**

- `topic` (required) -- extracted from the natural language command
- `task_name` -- auto-generated from topic
- `category` -- defaults to "blog" unless specified
- `target_audience` -- defaults to "general" unless specified

**Example:**

```
You: write a post about quantum computing breakthroughs in 2026
Bot: Created task abc12345 -- 'Quantum Computing Breakthroughs in 2026'. Pipeline started.
```

---

## 2. list-tasks

**Trigger phrases:** "show my tasks", "list tasks", "what's in the pipeline"

**What it does:** Lists the most recent tasks with their status, quality scores, and creation dates.

**API call:** `GET /api/tasks?limit=10&offset=0`

**Example:**

```
You: show my tasks
Bot: Recent tasks:
  1. abc12345 -- 'AI in Healthcare' -- completed (score: 87) -- 2h ago
  2. def67890 -- 'Rust vs Go' -- in_progress (stage: qa_critique) -- 45m ago
  3. ghi11111 -- 'Web3 Trends' -- failed -- 1d ago
```

---

## 3. task-status

**Trigger phrases:** "check task {id}", "status of {id}", "how's task {id}"

**What it does:** Gets detailed status for a specific task including current stage, quality score, word count, and timestamps.

**API call:** `GET /api/tasks/{task_id}`

**Parameters:**

- `task_id` (required) -- the task ID (full UUID or short prefix)

**Example:**

```
You: check task abc12345
Bot: Task abc12345 -- 'AI in Healthcare'
  Status: completed
  Stage: publishing_prep
  Quality: 87/100
  Words: 1,842
  Created: 2026-03-25 14:30 UTC
  Completed: 2026-03-25 14:47 UTC
```

---

## 4. approve-task

**Trigger phrases:** "approve task {id}", "approve {id}"

**What it does:** Approves a completed task, marking it ready for publishing. Only works on tasks in `completed` status.

**API call:** `POST /api/tasks/{task_id}/approve`

**Example:**

```
You: approve task abc12345
Bot: Task abc12345 approved -- ready for publishing.
```

---

## 5. publish-task

**Trigger phrases:** "publish task {id}", "publish {id}", "push {id} live"

**What it does:** Publishes an approved task to the public Next.js site. Triggers ISR revalidation so the post appears immediately.

**API call:** `POST /api/tasks/{task_id}/publish`

**Example:**

```
You: publish task abc12345
Bot: Published 'AI in Healthcare' -- live at https://gladlabs.com/posts/ai-in-healthcare
```

---

## 6. reject-task

**Trigger phrases:** "reject task {id}", "reject {id} because..."

**What it does:** Rejects a task with optional feedback. The task can be re-run through the pipeline with adjustments.

**API call:** `POST /api/tasks/{task_id}/reject`

**Parameters:**

- `task_id` (required)
- `reason` (optional) -- extracted from the message after "because"

**Example:**

```
You: reject task abc12345 because the intro is too generic
Bot: Task abc12345 rejected -- feedback: 'the intro is too generic'
```

---

## 7. daily-summary

**Trigger phrases:** "daily summary", "today's summary", "what happened today"

**What it does:** Shows a summary of today's pipeline activity: tasks created, completed, failed, published, and total LLM cost.

**API call:** `GET /api/analytics/kpis?period=today`

**Example:**

```
You: daily summary
Bot: Today's Pipeline Summary:
  Tasks created: 5
  Completed: 3 (avg score: 84)
  Published: 2
  Failed: 1
  LLM cost: $2.47 of $5.00 budget
```

---

## 8. budget-status

**Trigger phrases:** "budget status", "how much have I spent", "cost check"

**What it does:** Shows current daily LLM spend against the configured `DAILY_BUDGET_USD`.

**API call:** `GET /api/costs/summary?period=today`

**Example:**

```
You: budget status
Bot: Daily Budget: $2.47 / $5.00 (49%)
  Anthropic: $1.82
  OpenAI: $0.65
  Tasks remaining before cap: ~5
```

---

## 9. queue-stats

**Trigger phrases:** "queue stats", "queue status", "what's queued"

**What it does:** Shows command queue depth and processing statistics.

**API call:** `GET /api/commands/stats/queue-stats`

**Example:**

```
You: queue stats
Bot: Command Queue:
  Pending: 2
  Processing: 1
  Completed (24h): 47
  Failed (24h): 3
```

---

## 10. site-health

**Trigger phrases:** "site health", "is the site up", "health check"

**What it does:** Checks FastAPI backend health, database connectivity, and model router status.

**API call:** `GET /api/health`

**Example:**

```
You: site health
Bot: System Health: OK
  API: healthy (uptime: 4d 12h)
  Database: connected (pool: 8/20)
  Model router: anthropic (fallback: openai)
  Public site: reachable
```
