# OpenClaw Integration

OpenClaw is a conversational gateway that connects Glad Labs' FastAPI content pipeline to messaging platforms (Discord, Telegram). It lets you trigger pipeline operations, check status, and receive notifications through natural language commands.

## How It Works

```
You (Discord/Telegram)
  |
  v
OpenClaw Gateway (port 3400)
  |
  v  HTTP + Bearer token
FastAPI Backend (port 8000)
  |
  v  webhook_events table
Webhook Delivery Service
  |
  v  POST /hooks/agent
OpenClaw Gateway --> Discord/Telegram notification
```

1. **Inbound:** You send a command in Discord/Telegram. OpenClaw matches it to a skill, which calls the FastAPI API with the configured `API_TOKEN`.
2. **Outbound:** The pipeline inserts events into `webhook_events`. The `WebhookDeliveryService` polls this table and POSTs formatted messages to OpenClaw's `/hooks/agent` endpoint, which relays them to your channel.

## Prerequisites

- OpenClaw installed and running (see [openclaw.dev](https://openclaw.dev))
- Discord bot or Telegram bot connected to OpenClaw
- FastAPI backend running on port 8000
- `API_TOKEN` env var set in both OpenClaw and FastAPI

## Configuration

### 1. Copy Skills

Copy the skill files to your OpenClaw skills directory:

```bash
cp -r skills/openclaw/* ~/.openclaw/skills/
```

Each skill is a directory with a `SKILL.md` (trigger phrases and description) and `scripts/run.sh` (HTTP calls to the FastAPI API).

### 2. Configure OpenClaw Environment

Add the FastAPI connection details to `~/.openclaw/openclaw.json`:

```json
{
  "skills_dir": "~/.openclaw/skills",
  "env": {
    "GLADLABS_API_URL": "http://localhost:8000",
    "GLADLABS_API_TOKEN": "your-api-token-here"
  }
}
```

### 3. Configure FastAPI Webhook

In your FastAPI `.env.local` (project root), set:

```env
# OpenClaw gateway URL (where webhook events are sent)
OPENCLAW_WEBHOOK_URL=http://localhost:3400

# Shared secret for webhook auth (must match OpenClaw config)
OPENCLAW_WEBHOOK_TOKEN=your-shared-secret-here
```

The `WebhookDeliveryService` starts automatically on FastAPI boot when `OPENCLAW_WEBHOOK_URL` is set. It polls the `webhook_events` table every 5 seconds and delivers events with up to 3 retries.

### 4. Set API Token

Both sides need the same bearer token. In `.env.local`:

```env
API_TOKEN=your-api-token-here
```

Generate a secure token: `openssl rand -base64 32`

## Available Skills (10)

| Skill            | Trigger Phrase             | Description                                                |
| ---------------- | -------------------------- | ---------------------------------------------------------- |
| `create-post`    | "write a post about..."    | Creates a new content task and starts the 6-stage pipeline |
| `batch-create`   | "create 10 posts about..." | Creates multiple content tasks in batch                    |
| `list-tasks`     | "show my tasks"            | Lists recent tasks with status and quality scores          |
| `approve-post`   | "approve task {id}"        | Approves a completed task for publishing                   |
| `reject-post`    | "reject task {id}"         | Rejects a task with feedback for revision                  |
| `publish-post`   | "publish task {id}"        | Publishes an approved task to the public site              |
| `cost-report`    | "what's my spend?"         | Shows daily/weekly cost breakdown by model and provider    |
| `quality-report` | "quality scores today"     | Shows recent quality scores and pass rates                 |
| `site-manage`    | "show settings"            | View or update pipeline settings (auto-publish, budget)    |
| `model-status`   | "which models are up?"     | Checks available AI models and provider health             |

See [skills-reference.md](skills-reference.md) for detailed skill documentation.

## Webhook Events

The pipeline emits these events to OpenClaw (formatted as human-readable messages):

| Event Type            | When                                            | Example Message                                                |
| --------------------- | ----------------------------------------------- | -------------------------------------------------------------- |
| `task.completed`      | Pipeline finishes all 6 stages                  | "Task abc12345 completed -- 'AI in Healthcare' (score: 87)"    |
| `task.auto_published` | Score exceeds `AUTO_PUBLISH_THRESHOLD`          | "Auto-published 'AI in Healthcare' (score: 92)"                |
| `task.failed`         | Pipeline encounters unrecoverable error         | "Task abc12345 failed -- 'AI in Healthcare': Model timeout"    |
| `task.needs_review`   | Task completes but below auto-publish threshold | "Task abc12345 needs review -- 'AI in Healthcare' (score: 71)" |
| `post.published`      | Post goes live on public site                   | "Published 'AI in Healthcare' to default"                      |
| `cost.budget_warning` | Daily spend exceeds 80% of `DAILY_BUDGET_USD`   | "Budget alert: spent $4.12 of $5.00 daily budget (82%)"        |

## Troubleshooting

### Skills not responding

1. Verify OpenClaw is running: `curl http://localhost:3400/health`
2. Check that skills are loaded: `ls ~/.openclaw/skills/` should show 10 directories
3. Verify API token matches: the token in `openclaw.json` must match `API_TOKEN` in `.env.local`
4. Check FastAPI is reachable: `curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/health`

### Webhook events not arriving in Discord/Telegram

1. Confirm `OPENCLAW_WEBHOOK_URL` is set in `.env.local` and FastAPI was restarted
2. Check the `webhook_events` table for pending events:
   ```sql
   SELECT id, event_type, delivered, delivery_attempts, created_at
   FROM webhook_events
   ORDER BY created_at DESC
   LIMIT 10;
   ```
3. Look for delivery errors in FastAPI logs: `grep "WEBHOOK" logs/`
4. Verify OpenClaw's `/hooks/agent` endpoint is reachable from the FastAPI host
5. Events retry up to 3 times. If `delivery_attempts >= 3`, the event is abandoned. Fix the issue, then reset:
   ```sql
   UPDATE webhook_events SET delivery_attempts = 0 WHERE delivered = FALSE;
   ```

### "API_TOKEN not configured" error

The FastAPI backend requires `API_TOKEN` to be set in `.env.local`. Without it, all authenticated endpoints return 500.

### Tasks created but no webhook events

Webhook events are emitted by the publishing routes (`task_publishing_routes.py`). If you create a task via the API but it never reaches the publishing stage (e.g., pipeline fails at research), no webhook event is emitted for completion. Check task status via the `list-tasks` skill or:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/tasks/TASK_ID
```
