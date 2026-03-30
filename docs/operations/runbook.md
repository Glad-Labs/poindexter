# Glad Labs Operations Runbook

**Last Updated:** March 30, 2026
**Author:** Claude Code (autonomous operator)

---

## System Architecture

```
Matt (Phone/Telegram)
  ↓
Claude Code (PC) ←→ OpenClaw (PC) → Discord/Telegram
  ↓                    ↓
Worker (PC/Ollama)   Poindexter Bot
  ↓
Railway (Coordinator) ←→ PostgreSQL (Railway)
  ↓
Vercel (gladlabs.io) → Google (SEO/Search Console)
  ↓
Grafana Cloud ← Alloy ← windows_exporter + nvidia-smi
```

## Production URLs

| Service       | URL                                             | Purpose               |
| ------------- | ----------------------------------------------- | --------------------- |
| Public site   | https://gladlabs.io (→ www.gladlabs.io)         | Blog/content          |
| Backend API   | https://cofounder-production.up.railway.app     | FastAPI coordinator   |
| Health check  | /api/health                                     | System status         |
| Grafana       | https://gladlabs.grafana.net                    | Monitoring dashboards |
| GitHub        | https://github.com/Glad-Labs/glad-labs-codebase | Source code           |
| Project board | https://github.com/orgs/Glad-Labs/projects/2    | Task tracking         |

## Environment Variables (Production)

Only 3 env vars on Railway (everything else in DB):

- `DATABASE_URL` — auto-provided by Railway Postgres plugin
- `PORT` — auto-provided by Railway
- `ENVIRONMENT=production`
- `API_TOKEN` — for admin API auth

All other config is in the `app_settings` table (33 keys across 7 categories).

## Windows Scheduled Tasks

| Task                        | Schedule    | Purpose                                |
| --------------------------- | ----------- | -------------------------------------- |
| Glad Labs Worker            | On login    | Content generation via Ollama          |
| Glad Labs Daemon            | On login    | Auto-publish (5min) + content gen (8h) |
| OpenClaw Gateway            | On boot     | Telegram/Discord/WhatsApp              |
| OpenClaw Watchdog           | Every 2 min | Restart gateway if down                |
| Claude Code Watchdog        | Every 2 min | Restart Claude if down                 |
| NVIDIA SMI Exporter         | On boot     | GPU metrics on port 9835               |
| Glad Labs Content Generator | Every 8h    | PAUSED — quality improvements needed   |

## Claude Code Cron Jobs (session-based)

| Job                | Schedule        | Purpose                                 |
| ------------------ | --------------- | --------------------------------------- |
| Self-healing agent | Hourly at :13   | Health check + auto-fix                 |
| Code quality agent | Every 4h at :37 | Security/dead code/error handling scans |

These die when the Claude session ends. Re-create on new sessions.

## Content Pipeline Flow

```
1. Task created (API or content generator)
2. Worker picks up (polls every 5s)
3. Research stage (Ollama)
4. Draft generation (Ollama)
5. QA scoring (Ollama)
6. Programmatic validation (content_validator.py)
7. Cross-model QA review (Claude Haiku, if API key set)
8. SEO metadata generation
9. Training data capture
10. Task finalized → awaiting_approval
11. Auto-publisher approves + publishes (if score >= 80)
12. Post appears on gladlabs.io (ISR revalidates in 5 min)
13. Telegram + Discord notification sent
```

## Monitoring

### Grafana Dashboards

- **Ops (home):** post count, queue depth, quality scores, worker status, GPU, electricity cost
- **Hardware:** GPU util/temp/power, CPU, RAM, VRAM over time
- **Pipeline:** tasks by status, daily throughput
- **Cost Control:** daily spend, budget gauge, cost by provider
- **Quality:** score trends, pass rate

### Alerts (→ Telegram + Discord)

- Tasks stuck in_progress > 30 min
- Pipeline failure rate > 20%
- No tasks processed in 15 min
- GPU temperature > 80C
- GPU VRAM usage > 90%

## Common Operations

### Create a content task

```bash
curl -X POST https://cofounder-production.up.railway.app/api/tasks \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"task_name":"Blog post: Topic","topic":"Topic","category":"technology"}'
```

### Approve and publish a task

```bash
curl -X POST https://cofounder-production.up.railway.app/api/tasks/{id}/approve \
  -H "Authorization: Bearer $API_TOKEN"
curl -X POST https://cofounder-production.up.railway.app/api/tasks/{id}/publish \
  -H "Authorization: Bearer $API_TOKEN"
```

### Run auto-publisher manually

```bash
python scripts/auto-publisher.py
```

### Start worker manually

```powershell
.\scripts\start-worker.ps1
```

### Generate content batch

```bash
python scripts/scheduled-content.py --count 5
```

### Check system health

```bash
curl https://cofounder-production.up.railway.app/api/health
curl https://gladlabs.io/sitemap.xml
```

### Update a setting

```bash
curl -X PUT https://cofounder-production.up.railway.app/api/settings/{key} \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value":"new_value"}'
```

### Restart OpenClaw

```powershell
openclaw gateway restart
```

### View worker logs

```bash
tail -f /tmp/worker-*.log
```

### Sync config from git to OpenClaw

```powershell
.\infrastructure\openclaw\sync-config.ps1
```

## Disaster Recovery

### Site down (Vercel)

- Check: https://vercel.com/dashboard
- Fix: Push any commit to main → auto-deploys
- Fallback: `npx vercel --prod` from web/public-site/

### Backend down (Railway)

- Check: `railway logs -n 20 -s cofounder`
- Fix: `railway redeploy` or push to main
- Root dir: src/cofounder_agent, Builder: Dockerfile

### Database issues

- Connection: Railway auto-manages PostgreSQL
- Migrations: Run on startup via services/migrations/
- Backup: `scripts/backup-production-db.sh`

### Worker not processing

1. Check: `curl http://localhost:8002/api/health`
2. Kill: `powershell "Get-NetTCPConnection -LocalPort 8002 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"`
3. Restart: `.\scripts\start-worker.ps1`

### OpenClaw not responding

1. Check: `curl http://localhost:18789/status`
2. Fix: `openclaw gateway restart`
3. Verify: `openclaw channels status`

## Key Files

| File                                                     | Purpose                            |
| -------------------------------------------------------- | ---------------------------------- |
| `src/cofounder_agent/main.py`                            | FastAPI app entry point            |
| `src/cofounder_agent/services/task_executor.py`          | Pipeline execution + notifications |
| `src/cofounder_agent/services/content_router_service.py` | 6-stage content pipeline           |
| `src/cofounder_agent/services/content_validator.py`      | Anti-hallucination rules           |
| `src/cofounder_agent/services/model_router.py`           | LLM provider selection             |
| `src/cofounder_agent/services/settings_service.py`       | DB-backed config (app_settings)    |
| `src/cofounder_agent/services/cost_guard.py`             | Spend limits                       |
| `src/cofounder_agent/services/process_composer.py`       | Intent → workflow orchestration    |
| `scripts/daemon.py`                                      | Auto-publisher + content generator |
| `scripts/start-worker.ps1`                               | Worker startup                     |
| `scripts/auto-publisher.py`                              | Approve + validate + publish       |
| `scripts/scheduled-content.py`                           | Topic generation                   |
| `mcp-server/server.py`                                   | Claude desktop MCP integration     |
| `infrastructure/openclaw/`                               | OpenClaw config template           |
| `infrastructure/grafana/`                                | Dashboard JSON definitions         |
