# Disaster Recovery Runbook

**Last Updated:** 2026-04-17

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

# The brain daemon runs as a Docker container
docker ps | grep brain-daemon

# Restart
docker compose -f docker-compose.local.yml up -d brain-daemon

# If running standalone (non-Docker):
# Windows: pythonw brain/brain_daemon.py
# Linux:   nohup python3 brain/brain_daemon.py &

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
curl http://localhost:8002/api/health | python3 -m json.tool

# Restart worker container
docker compose -f docker-compose.local.yml up -d worker

# Or restart the entire stack from bootstrap.toml
bash scripts/start-stack.sh

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

# Required models (configurable via app_settings):
ollama pull nomic-embed-text   # embeddings
ollama pull gemma3:27b          # QA critic
ollama pull qwen3:8b            # fast tasks
```

**Prevention:** Grafana "Ollama Unresponsive" alert (only fires when tasks are pending). Brain health probes test Ollama every cycle.

---

## 5. SDXL Server (Image Generation)

**Symptoms:** Posts publish with Pexels stock photos instead of SDXL-generated images. Health endpoint returns `degraded`.

**Recovery:**

```bash
# Check health
curl http://localhost:9836/health | python3 -m json.tool

# If degraded, check error reason in the response
# Common: torch/torchvision mismatch, PEFT missing

# Restart container
docker compose -f docker-compose.local.yml up -d sdxl-server

# If dependency issue persists, fix inside container:
docker exec poindexter-sdxl-server pip install --upgrade torchvision peft
docker restart poindexter-sdxl-server
```

**Prevention:** Pipeline falls back to Pexels stock photos if SDXL is degraded — content still publishes, just with generic images.

---

## 6. Vercel (Frontend)

**Symptoms:** Site returns 500 or blank page, "Site DOWN" brain alert.

**Recovery:**

```bash
# Check if site is up
curl -sf https://www.gladlabs.io

# Check Vercel deployment status
gh run list --repo Glad-Labs/poindexter --limit 3

# Redeploy latest
gh run rerun <run_id> --repo Glad-Labs/poindexter

# If Vercel token expired, generate new one at vercel.com/account/tokens
# Update GitHub secret:
gh secret set VERCEL_TOKEN --repo Glad-Labs/poindexter --body "<new_token>"
```

**Prevention:** GitHub Actions deploys on every push to main. Smoke test after each deploy.

---

## 7. Gitea (Source of Truth)

**Symptoms:** Can't push code, mirror stops syncing, Gitea Actions fail.

**Recovery:**

```bash
# Check container
docker ps | grep gitea

# Restart
docker compose -f docker-compose.local.yml up -d gitea gitea-runner

# Verify
curl http://localhost:3001/api/v1/version

# If data lost, Gitea stores everything in the gitea-data Docker volume
# The GitHub mirror (Glad-Labs/poindexter) is a full backup of all code
```

**Prevention:** Sync script pushes sanitized copy to GitHub on every push.

---

## 8. Grafana (Monitoring)

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

## 9. OpenClaw (Messaging Gateway)

**Symptoms:** Can't reach bot via Discord/Telegram, gateway health check fails.

**Recovery:**

```bash
# Check if running
curl http://127.0.0.1:18789/

# Restart
# Windows: run the startup shortcut or:
start "OpenClaw Gateway" cmd /d /c %USERPROFILE%\.openclaw\gateway.cmd

# Verify MCP servers loaded
# Check OpenClaw logs for "poindexter" and "gladlabs" server entries
```

**Prevention:** openclaw-watchdog.ps1 monitors gateway health and auto-restarts.

---

## 10. GPU Metrics (nvidia-smi-exporter)

**Symptoms:** GPU panels in Grafana show stale data, "GPU Metrics Stale" alert fires.

**Recovery:**

```bash
# Check if exporter is running (serves on port 9835)
curl http://localhost:9835/metrics | head -5

# Check if HWiNFO shared memory is enabled (for PSU metrics)
curl http://localhost:9835/metrics | grep hwinfo

# If "shared memory not available", enable in HWiNFO64 Settings
# Verify SensorsSM=1 in C:\Program Files\HWiNFO64\HWiNFO64.INI

# Restart scraper
# Windows: taskkill /IM python.exe /FI "WINDOWTITLE eq nvidia-smi-exporter" && pythonw scripts/nvidia-smi-exporter.py
```

**Prevention:** Grafana "GPU Metrics Stale" alert fires if no data for 30 minutes.

---

## Full System Recovery (Nuclear Option)

If everything is down and you need to rebuild from scratch:

```bash
# 1. Ensure bootstrap.toml exists
cat ~/.poindexter/bootstrap.toml
# If missing: poindexter setup --auto

# 2. Start the entire stack from bootstrap.toml
bash scripts/start-stack.sh

# 3. Wait for all containers to be healthy
docker ps --format "table {{.Names}}\t{{.Status}}"

# 4. Start standalone services (not in Docker)
pythonw scripts/nvidia-smi-exporter.py   # GPU metrics

# 5. Start OpenClaw (if using)
start "" cmd /d /c %USERPROFILE%\.openclaw\gateway.cmd

# 6. Verify everything
curl http://localhost:8002/api/health      # Worker
curl http://localhost:3001/api/v1/version   # Gitea
curl http://localhost:3000/api/health       # Grafana
curl http://localhost:9836/health           # SDXL
curl http://localhost:9835/metrics | head -3 # GPU metrics
```

## Contact

- Matt Gladding: hello@gladlabs.io
- Telegram: Brain daemon sends alerts to configured chat_id
- Discord: Ops channel receives Grafana alerts
