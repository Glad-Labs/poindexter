---
name: Infrastructure Status
last_updated: 2026-04-01T21:30:00Z
updated_by: claude-code
category: state
---

# Infrastructure Status — April 1, 2026

## Docker Containers (docker-compose.local.yml)

| Service           | Port | Status      | Purpose                                            |
| ----------------- | ---- | ----------- | -------------------------------------------------- |
| pgvector          | 5433 | Running     | Brain DB + embeddings (140 vectors, 2274 ops rows) |
| Grafana           | 3000 | Running     | 5 dashboards (brain, health, GPU, audit, LLM)      |
| Headscale         | 8080 | Running     | Self-hosted VPN (replaces Tailscale cloud)         |
| Gitea             | 3001 | Needs setup | Self-hosted git (replaces GitHub)                  |
| Woodpecker Server | 8280 | Running     | CI/CD (replaces GitHub Actions)                    |
| Woodpecker Agent  | -    | Waiting     | Needs Gitea OAuth to authenticate                  |

## Windows Scheduled Tasks (11 active)

| Task                 | Interval   | Purpose                           |
| -------------------- | ---------- | --------------------------------- |
| OpenClaw Watchdog    | 2 min      | Auto-restart gateway              |
| Claude Code Watchdog | 2 min      | Auto-restart sessions             |
| Auto-Publisher       | 5 min      | Publish approved content          |
| Auto-Embed           | 1 hour     | Sync memories + posts to pgvector |
| DB Backup            | Daily 2 AM | pg_dump to ~/.gladlabs/backups/   |
| NVIDIA Exporter      | Boot       | GPU metrics on port 9835          |
| Worker               | Login      | Task execution with Ollama        |
| Brain Daemon         | Login      | Health probes, trend analysis     |
| OpenClaw Gateway     | Login      | Telegram/Discord/WhatsApp         |
| Update Checker       | Weekly     | winget upgrades + health          |

## Cloud Services (revenue infrastructure only)

| Service | Cost   | Purpose              |
| ------- | ------ | -------------------- |
| Vercel  | Free   | gladlabs.io frontend |
| Railway | ~$5/mo | Public site DB + API |

## Tailscale/Headscale Network

| Device           | IP                                         | Status                    |
| ---------------- | ------------------------------------------ | ------------------------- |
| Workstation      | 100.81.93.12 (Tailscale) / TBD (Headscale) | Connected                 |
| Pixel 9          | 100.66.1.122 (Tailscale) / TBD (Headscale) | Connected                 |
| Headscale server | 192.168.1.176:8080                         | Running, auth key created |

## Grafana Dashboards

1. Brain Operations — /d/brain-operations
2. System Health — /d/system-health
3. GPU Metrics — /d/gpu-metrics
4. Pipeline Audit Log — /d/audit-log
5. LLM Analytics — /d/llm-analytics

## Pending Manual Steps

1. Gitea initial setup (browser: http://localhost:3001)
2. Headscale client migration (tailscale up --login-server ...)
3. Phone Headscale config
4. Woodpecker OAuth setup (after Gitea)
