# Disaster Recovery Runbook

Recovery procedures for each critical Poindexter service. Ordered by severity.

## 1. PostgreSQL (Spine — highest priority)

**Symptoms:** API returns 500, brain daemon logs connection errors, Grafana dashboards empty.

**Recovery:**

```bash
# Check container status
docker ps | grep postgres

# Restart container
docker compose -f docker-compose.local.yml up -d postgres-local

# Verify connection
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c "SELECT 1"

# If data is corrupted, restore from backup
# Backups are at ~/.poindexter/backups/
ls ~/.poindexter/backups/
powershell -File scripts/db-restore.ps1 -BackupFile <latest_backup.sql.gz>
```

**Prevention:** Daily automated backups via db-backup.ps1. Brain daemon monitors DB health every 5 minutes.

---

## 2. Brain Daemon (Layer 0 watchdog)

**Symptoms:** No Telegram alerts, no brain_decisions logged, heartbeat file stale.

**Recovery:**

```bash
# Check heartbeat freshness
cat ~/.poindexter/heartbeat

# Check if process is running
tasklist | findstr brain_daemon  # Windows
ps aux | grep brain_daemon      # Linux

# Restart manually
pythonw brain/brain_daemon.py   # Windows (background)
nohup python3 brain/brain_daemon.py &  # Linux

# The OS-level watchdog (Task Scheduler/cron) should auto-restart
# within 10 minutes. Check:
# Windows: Task Scheduler > "Poindexter Brain Watchdog"
# Linux: crontab -l | grep brain-watchdog
```

**Prevention:** Layer 1 watchdog checks heartbeat every 10 minutes and auto-restarts.

---

## 3. Content Worker (FastAPI)

**Symptoms:** Pipeline stalled alert, no tasks processing, API health returns unhealthy worker.

**Recovery:**

```bash
# Check worker status
curl http://localhost:8002/api/health | python -m json.tool

# Restart worker
powershell -File scripts/start-worker.ps1  # Windows

# Verify tasks resume
curl http://localhost:8002/api/health
```

**Prevention:** Brain daemon monitors worker health. Grafana "Worker Offline" alert fires if no activity for 30 min (with idle-awareness).

---

## 4. Ollama (LLM Backend)

**Symptoms:** Content generation fails, tasks stuck in_progress, "Ollama Unresponsive" alert.

**Recovery:**

```bash
# Check if Ollama is responding
curl http://localhost:11434/api/tags

# Restart Ollama
ollama serve  # or restart the service

# Verify models are available
ollama list

# Required models: nomic-embed-text, glm-4.7-5090 (or configured writer model)
ollama pull nomic-embed-text
```

**Prevention:** Grafana "Ollama Unresponsive" alert (only fires when tasks are pending). Brain health probes test Ollama every cycle.

---

## 5. Vercel (Frontend)

**Symptoms:** Site returns 500 or blank page, "Site DOWN" brain alert.

**Recovery:**

```bash
# Check if site is up
curl -sf https://www.gladlabs.io

# Check Vercel deployment status
gh run list --repo Glad-Labs/glad-labs-stack --limit 3

# Redeploy latest
gh run rerun <run_id> --repo Glad-Labs/glad-labs-stack

# If Vercel token expired, generate new one at vercel.com/account/tokens
# Update GitHub secret:
gh secret set VERCEL_TOKEN --repo Glad-Labs/glad-labs-stack --body "<new_token>"
```

**Prevention:** GitHub Actions deploys on every push to main. Smoke test after each deploy.

---

## 6. Gitea (Source of Truth)

**Symptoms:** Can't push code, mirror stops syncing, Woodpecker/Actions fail.

**Recovery:**

```bash
# Check container
docker ps | grep gitea

# Restart
docker compose -f docker-compose.local.yml up -d gitea

# Verify
curl http://localhost:3001/api/v1/version

# If data lost, Gitea stores everything in the gitea-data Docker volume
# The GitHub mirror (glad-labs-stack) is a full backup of all code
```

**Prevention:** Push mirror to GitHub syncs every commit + hourly.

---

## 7. Grafana (Monitoring)

**Symptoms:** Can't access dashboards, alerts stop evaluating.

**Recovery:**

```bash
# Restart
docker compose -f docker-compose.local.yml up -d grafana

# Verify
curl http://localhost:3000/api/health

# Dashboards are provisioned from files — they auto-reload on restart
# Alert rules are in infrastructure/grafana/provisioning/alerting/
```

**Prevention:** Brain daemon is independent of Grafana — alerts continue via direct Telegram API even if Grafana is down.

---

## 8. OpenClaw (Messaging Gateway)

**Symptoms:** Can't reach bot via Discord/Telegram/WhatsApp, gateway health check fails.

**Recovery:**

```bash
# Check if running
curl http://127.0.0.1:18789/

# Restart
# Windows: run the startup shortcut or:
start "OpenClaw Gateway" cmd /d /c C:\Users\mattm\.openclaw\gateway.cmd

# Verify MCP servers loaded
# Check OpenClaw logs for "gladlabs" and "poindexter" server entries
```

**Prevention:** openclaw-watchdog.ps1 monitors gateway health and auto-restarts.

---

## 9. GPU Metrics Scraper

**Symptoms:** GPU panels in Grafana show stale data, "GPU Metrics Stale" alert fires.

**Recovery:**

```bash
# Check if scraper is running
tasklist | findstr gpu-scraper  # Windows

# Restart
pythonw scripts/gpu-scraper.py  # Windows background

# Verify data flowing
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain \
  -c "SELECT max(timestamp) FROM gpu_metrics"
```

**Prevention:** Grafana "GPU Metrics Stale" alert fires if no data for 30 minutes.

---

## Full System Recovery (Nuclear Option)

If everything is down and you need to rebuild from scratch:

```bash
# 1. Start core infrastructure
docker compose -f docker-compose.local.yml up -d postgres-local grafana

# 2. Wait for Postgres to be healthy
docker compose -f docker-compose.local.yml up -d --wait postgres-local

# 3. Start Gitea + CI runner
docker compose -f docker-compose.local.yml up -d gitea gitea-runner

# 4. Start brain daemon
pythonw brain/brain_daemon.py

# 5. Start worker
powershell -File scripts/start-worker.ps1

# 6. Start GPU scraper
pythonw scripts/gpu-scraper.py

# 7. Start OpenClaw
start "" cmd /d /c C:\Users\mattm\.openclaw\gateway.cmd

# 8. Verify everything
curl http://localhost:8002/api/health
curl http://localhost:3001/api/v1/version
curl http://localhost:3000/api/health
curl http://127.0.0.1:18789/
```

## Contact

- Matt Gladding: mattg@gladlabs.io
- Telegram: Brain daemon sends alerts to configured chat_id
- Discord: Ops channel receives Grafana alerts
