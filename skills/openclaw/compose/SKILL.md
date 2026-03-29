---
name: compose
description: Send an intent to the Process Composer. The system proposes a plan, you approve, it executes. Use when the user says "compose", "plan", "do this", or describes a business process to execute.
---

# Process Composer

Send natural language intent to the system. It proposes a plan of steps, you review and approve, then it executes.

## Usage

```bash
scripts/run.sh plan "Write a blog post about AI orchestration"
scripts/run.sh execute "Check if the site is healthy"
scripts/run.sh steps
```

## Commands

- `plan "intent"` — Propose a plan for review (shows steps before executing)
- `execute "intent"` — Execute immediately (skip approval)
- `approve <plan_id>` — Approve a pending plan
- `reject <plan_id> [reason]` — Reject a pending plan
- `steps` — List available building blocks
