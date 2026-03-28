---
name: railway-status
description: Check Railway deployment status, recent logs, and backend health. Use when asked about backend status, deployment, or API health.
---

# Railway Status

Check the status of the FastAPI backend on Railway.

## Usage

```bash
scripts/run.sh [status|logs|health]
```

- `status` — Show deployment status and service info
- `logs` — Show last 20 log lines
- `health` — Ping the health endpoint
- No argument — show all three
