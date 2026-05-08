# Incident Response Runbook

**Last reviewed:** 2026-04-30
**Audience:** solo operator (Matt) at 2am during an incident
**Prereqs:** Local PC online, Docker running, gh CLI authed, poindexter CLI installed, Grafana access

When an alert fires (Telegram or Discord), this is the playbook. Find the alert by name in the index, jump to its section, follow the steps. If something doesn't match a known alert, fall through to **Unknown alert — generic triage**.

---

## Scenarios this covers

- A Telegram or Discord notification just fired and you need to know what to do
- You woke up to multiple alerts and need to triage by severity
- A symptom is happening but no alert fired (Grafana / brain daemon may be down)
- You need to escalate / mute an alert during planned maintenance

For catastrophic-loss scenarios (lost DB volume, lost secret key), see [`disaster-recovery.md`](./disaster-recovery). For known-pattern symptoms, see [`troubleshooting.md`](./troubleshooting). For routine secret rotation, see [`secret-rotation.md`](./secret-rotation).

---

## Quick triage flowchart

```
Alert fired in the last 5 minutes?
  YES -> Check the alert NAME first. Jump to that section below.
  NO  -> Is the issue still happening?
           YES -> Treat as a fresh symptom; check troubleshooting.md by symptom.
           NO  -> Add a postmortem entry to troubleshooting.md and move on.

Multiple alerts at once?
  -> Triage by severity:
     1. critical   -> Worker offline / brain daemon stale / GPU temp high
     2. data loss  -> DB size growing weird / disk space low
     3. warning    -> Stuck tasks, embedding lag, cost spike, traffic anomaly
     4. cosmetic   -> Pipeline stalled (no new tasks) / quality drop / approval rate

Telegram silent but you SEE a problem?
  -> Brain daemon may be dead.
     Check: cat ~/.poindexter/heartbeat
     Then:  docker ps | grep brain-daemon
     Jump to "Brain Daemon Stale" alert section.
```

---

## Alert index — alert name to runbook section

These are the active Grafana alert rules in `infrastructure/grafana/provisioning/alerting/alert-rules.yml` (15 rules as of 2026-04-30). Brain daemon also fires a few synthetic alerts directly to Telegram.

| Alert                          | Severity | Section                                                             |
| ------------------------------ | -------- | ------------------------------------------------------------------- |
| Worker Offline                 | critical | [§ Worker Offline](#worker-offline)                                 |
| Brain Daemon Stale             | critical | [§ Brain Daemon Stale](#brain-daemon-stale)                         |
| GPU Temperature High           | critical | [§ GPU Temperature High](#gpu-temperature-high)                     |
| High Error Rate                | critical | [§ High Error Rate](#high-error-rate)                               |
| Stale Tasks                    | warning  | [§ Stale Tasks](#stale-tasks)                                       |
| Embedding Sync Lag             | warning  | [§ Embedding Sync Lag](#embedding-sync-lag)                         |
| DB Size Warning                | warning  | [§ DB Size Warning](#db-size-warning)                               |
| Daily Cost Spike               | warning  | [§ Daily Cost Spike](#daily-cost-spike)                             |
| Content Quality Drop           | warning  | [§ Content Quality Drop](#content-quality-drop)                     |
| Traffic Anomaly                | warning  | [§ Traffic Anomaly](#traffic-anomaly)                               |
| Pipeline Stalled               | warning  | [§ Pipeline Stalled](#pipeline-stalled)                             |
| Ollama Unresponsive            | warning  | [§ Ollama Unresponsive](#ollama-unresponsive)                       |
| Zero Published Posts This Week | warning  | [§ Zero Published Posts This Week](#zero-published-posts-this-week) |
| GPU Metrics Stale              | warning  | [§ GPU Metrics Stale](#gpu-metrics-stale)                           |
| Disk Space Low                 | warning  | [§ Disk Space Low](#disk-space-low)                                 |
| Site DOWN (brain)              | critical | [§ Site DOWN](#site-down)                                           |
| Wan Server DEGRADED            | warning  | [§ Wan Server DEGRADED](#wan-server-degraded)                       |
| (Unknown alert)                | varies   | [§ Unknown alert — generic triage](#unknown-alert--generic-triage)  |

---

## Worker Offline

**Means.** No `brain_decisions` row in the last 10 minutes AND no `pipeline_tasks` activity in the last 30 minutes. The worker is probably crashed.

**Triage.**

```bash
docker ps --filter name=poindexter-worker --format "{{.Status}}"
# If "Restarting" or empty -> container is down
# If "Up X minutes" -> worker is alive but not processing
```

**Fix.**

```bash
# Restart
docker compose -f docker-compose.local.yml up -d worker

# Watch boot logs
docker logs -f poindexter-worker 2>&1 | head -40
# Look for: "Application startup complete." (FastAPI boot)
# Look for: "[task_executor] poll cycle starting" (worker actually working)

# Verify
curl -s http://localhost:8002/api/health | python -m json.tool
```

If the container won't start (crashloop), see [`troubleshooting.md`](./troubleshooting) "Test suite fails in the worker container" or check the logs for a Python traceback. Most common: a recent migration added a column that the deployed code doesn't expect — roll the migration back or pull/rebuild.

**Escalation.** If the container is healthy but tasks still aren't processing, jump to **Stale Tasks** below.

---

## Brain Daemon Stale

**Means.** No `brain_decisions` rows in the last 15 minutes. The 5-minute brain cycle has stopped.

**Triage.**

```bash
cat ~/.poindexter/heartbeat
# Should be a unix timestamp from < 5 minutes ago.

docker ps --filter name=poindexter-brain-daemon --format "{{.Status}}"
```

**Fix.**

```bash
docker compose -f docker-compose.local.yml up -d brain-daemon
docker logs -f poindexter-brain-daemon 2>&1 | head -20
```

The OS-level watchdog (Task Scheduler "Poindexter Brain Watchdog" / cron `brain-watchdog.sh`) should auto-restart within 10 minutes — if it didn't, the watchdog itself is broken. Check:

```bash
# Windows
schtasks /Query /TN "Poindexter Brain Watchdog" /V /FO LIST
# Linux
crontab -l | grep brain-watchdog
```

**Escalation.** If brain restarts but immediately exits, look at the logs for a DB connection error — likely a `DATABASE_URL` problem. See [`disaster-recovery.md`](./disaster-recovery) CONFIG-1.

---

## GPU Temperature High

**Means.** The latest `gpu_metrics` row has `temperature > 85°C`. Alert is stable for 5 min.

**Triage.**

```bash
nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used --format=csv

# What's hammering the GPU right now?
docker stats --no-stream poindexter-sdxl-server poindexter-wan-server poindexter-worker
```

**Fix.**

1. **If it's a runaway pipeline:** pause it.
   ```bash
   poindexter settings set pipeline_paused true
   ```
2. **If it's an SDXL or Wan render:** stop the in-flight job.
   ```bash
   docker restart poindexter-sdxl-server   # nuclear; cancels any in-flight render
   docker restart poindexter-wan-server
   ```
3. **If it's a third-party workload (gaming, a stray process):** kill that process or accept the temperature.
4. **If GPU temp stays high after killing all GPU loads:** physical cooling problem. Check fan curve, intake/exhaust airflow, ambient temp.

**Verification.**

```bash
# Wait 60s, re-check
nvidia-smi --query-gpu=temperature.gpu --format=csv
# Should drop below 80°C within 1 minute of removing the load.
```

**Escalation.** Sustained > 90°C is hardware damage territory. Power-off the workstation if it stays there.

---

## High Error Rate

**Means.** More than 5 `audit_log` rows with `severity='error'` in the last hour.

**Triage.**

```sql
-- What kinds of errors?
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT category, COUNT(*) AS n, MAX(timestamp) AS latest
   FROM audit_log
   WHERE severity = 'error' AND timestamp > NOW() - INTERVAL '1 hour'
   GROUP BY category ORDER BY n DESC LIMIT 10;"

-- Top 5 most recent error messages
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT timestamp, category, LEFT(message, 100) AS msg
   FROM audit_log
   WHERE severity = 'error' AND timestamp > NOW() - INTERVAL '1 hour'
   ORDER BY timestamp DESC LIMIT 5;"
```

**Fix.** Depends on the category. Common ones:

- `category='ollama'` → see [§ Ollama Unresponsive](#ollama-unresponsive)
- `category='pipeline'` → see Stale Tasks / `troubleshooting.md`
- `category='webhook'` → likely a 4xx from a third-party (Vercel, Telegram, Discord) — check secret rotation

---

## Stale Tasks

**Means.** One or more `pipeline_tasks` rows have `status='in_progress'` and `updated_at` older than 2 hours.

**Triage.**

```sql
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT task_id, LEFT(topic, 50) AS topic, updated_at, NOW() - updated_at AS stuck_for
   FROM pipeline_tasks
   WHERE status = 'in_progress' AND updated_at < NOW() - INTERVAL '2 hours'
   ORDER BY updated_at;"
```

**Fix.**

```sql
-- Clear the stuck rows (they'll fall out of the alert)
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "UPDATE pipeline_tasks
   SET status='failed', error_message='manually cleared — stuck in_progress'
   WHERE status='in_progress' AND updated_at < NOW() - INTERVAL '2 hours';"
```

Then check what hung — see [`troubleshooting.md`](./troubleshooting) "Pipeline task stuck in_progress for more than 10 minutes."

---

## Embedding Sync Lag

**Means.** Newest row in `embeddings` is more than 6 hours old. Auto-embed daemon is probably dead.

**Triage + fix.**

```bash
docker ps --filter name=poindexter-auto-embed --format "{{.Status}}"
docker compose -f docker-compose.local.yml up -d auto-embed
docker logs -f poindexter-auto-embed 2>&1 | head -20
```

If the daemon is alive but embeddings still aren't progressing, check Ollama (the embedding model is `nomic-embed-text`, served by Ollama). See [§ Ollama Unresponsive](#ollama-unresponsive).

---

## DB Size Warning

**Means.** `poindexter_brain` is now > 1 GB.

**Triage.**

```sql
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT relname AS table, pg_size_pretty(pg_total_relation_size(relid)) AS size
   FROM pg_stat_user_tables
   ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;"
```

**Fix.** Usual suspects:

- `audit_log` — runs forever by default. Add a retention policy:
  ```sql
  DELETE FROM audit_log WHERE timestamp < NOW() - INTERVAL '30 days';
  ```
- `embeddings` — high churn. Check whether old post embeddings are being re-generated unnecessarily.
- `pipeline_tasks` — old failed rows accumulate. See `poindexter retention --help` for archival commands.

**Escalation.** Sustained growth > 10 GB needs a long-term plan — partition the high-churn tables, archive cold rows to a separate DB.

---

## Daily Cost Spike

**Means.** `cost_logs` SUM in the last 24h exceeds $5.

**Triage.**

```sql
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT provider, model, SUM(cost_usd) AS spend, COUNT(*) AS calls
   FROM cost_logs WHERE created_at > NOW() - INTERVAL '24 hours'
   GROUP BY provider, model ORDER BY spend DESC;"
```

**Fix.** If a paid provider is spiking:

```bash
# Disable the paid model — fallback chain takes over
poindexter settings set <provider>_enabled false

# Or tighten the daily budget so cost_guard kills further calls
poindexter settings set cost_guard_daily_limit_usd 1.0
```

For the budget to actually enforce, the cost guard must be enabled — verify with `poindexter settings get cost_guard_enabled`.

**Escalation.** $5/day = $150/month. If this is sustained without commensurate revenue, kill the paid provider entirely (`poindexter settings set <provider>_enabled false`) and run on local Ollama only.

---

## Content Quality Drop

**Means.** 7-day average `quality_score` is below 70. Pipeline is producing weaker content.

**Triage.**

```sql
-- What's the current writer model?
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT key, value, updated_at FROM app_settings WHERE key='pipeline_writer_model';"

-- Score by day
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT DATE(updated_at) AS d, ROUND(AVG(quality_score)::numeric, 1) AS avg_q, COUNT(*) AS n
   FROM pipeline_tasks WHERE quality_score IS NOT NULL AND updated_at > NOW() - INTERVAL '14 days'
   GROUP BY d ORDER BY d DESC;"
```

**Fix.** Most common cause — `pipeline_writer_model` was flipped off the intended model. See [`troubleshooting.md`](./troubleshooting) "Approval rate drops to ~0%" entry.

```sql
-- Restore intended writer
UPDATE app_settings SET value = 'ollama/glm-4.7-5090:latest', updated_at = NOW()
WHERE key = 'pipeline_writer_model';
```

---

## Traffic Anomaly

**Means.** Today's `page_views` count is less than 50% of yesterday's. Possible site issue.

**Triage.**

```bash
# Is the site even up?
curl -sf https://www.gladlabs.io >/dev/null && echo "site OK" || echo "SITE DOWN"

# Did Vercel deploy something broken in the last 24h?
gh run list --repo Glad-Labs/glad-labs-stack --limit 5
```

**Fix.** If site is down → see [§ Site DOWN](#site-down). If site is up but traffic still dropped:

- Check Search Console for new manual penalties or indexing drops
- Check Google Analytics for referrer changes
- Check whether `ViewTracker` beacon is firing — open the site in a browser, watch Network tab, look for `/api/views/track` POST. If missing, the analytics beacon is broken (frontend issue, not a real traffic drop).

---

## Pipeline Stalled

**Means.** Zero new `pipeline_tasks` rows in the last 48 hours.

**Triage.**

```bash
# Is the worker alive?
curl -s http://localhost:8002/api/health | python -m json.tool

# Is anything blocking new tasks?
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT key, value FROM app_settings
   WHERE key IN ('pipeline_paused', 'max_approval_queue', 'pipeline_paused_for_gaming');"

# Approval queue full?
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT COUNT(*) FROM content_tasks WHERE status='awaiting_approval';"
```

**Fix.**

- If `pipeline_paused=true` → `poindexter settings set pipeline_paused false`
- If approval queue is at the cap → drain it (approve / reject pending posts)
- If worker is healthy and queue is empty but no new tasks fire → topic discovery may be stuck. See `troubleshooting.md` "Topic discovery keeps generating the same rejected topic genre."

---

## Ollama Unresponsive

**Means.** No local-model `cost_logs` rows in the last 6 hours WHILE tasks are pending. Ollama is probably down.

**Triage.**

```bash
curl -s http://localhost:11434/api/tags
# Expected: a JSON list of models. If hung / refused, Ollama is dead.

ollama ps   # Currently loaded models
nvidia-smi  # Is Ollama using GPU?
```

**Fix.**

```bash
# Restart Ollama
# Windows: net stop ollama; net start ollama
# Or just: ollama serve   (foreground)

# Verify required models present
ollama list
# If missing:
ollama pull nomic-embed-text
ollama pull gemma3:27b
ollama pull glm-4.7-5090:latest
```

---

## Zero Published Posts This Week

**Means.** No `posts` rows with `status='published'` and `published_at > NOW() - 7d`. Content publishing has stopped.

**Triage.** Three possible causes:

1. Pipeline isn't generating → see [§ Pipeline Stalled](#pipeline-stalled)
2. Pipeline is generating but everything's getting rejected → see "Approval rate drops to ~0%" in `troubleshooting.md`
3. Posts are reaching `awaiting_approval` but Matt hasn't approved any → operator action needed

```sql
-- Distinguish the cases
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT status, COUNT(*) FROM content_tasks
   WHERE created_at > NOW() - INTERVAL '7 days'
   GROUP BY status ORDER BY status;"
```

**Fix.** Whichever case applies — drain the queue, fix the writer model, or unpause the pipeline.

---

## GPU Metrics Stale

**Means.** No `gpu_metrics` rows in the last 30 minutes. The scraper crashed.

**Triage + fix.**

```bash
# Is the exporter running?
curl -s http://localhost:9835/metrics | head -3
# If hung / refused, restart it.

# Windows
taskkill /IM python.exe /FI "WINDOWTITLE eq nvidia-smi-exporter" 2>NUL
pythonw scripts/nvidia-smi-exporter.py
```

If running via the GPU scraper service:

```bash
docker ps | grep gpu-scraper   # if containerized
# Or check Task Scheduler for the host script
```

---

## Disk Space Low

**Means.** Free disk space is below 50 GB. (The current alert SQL actually checks `pg_database_size` — this rule is mislabeled and should be fixed; treat as DB size.)

**Triage + fix.**

```powershell
# Disk usage (Windows)
Get-PSDrive C | Select-Object Used,Free
docker system df
```

```bash
# Free up Docker space
docker system prune -af --volumes  # WARNING: drops unused volumes
# Or the gentler:
powershell -File scripts/docker-prune.ps1
```

For DB size growth, see [§ DB Size Warning](#db-size-warning).

---

## Site DOWN

**Means.** Brain daemon's site probe failed for > 5 minutes. `https://www.gladlabs.io` is returning non-2xx.

**Triage.**

```bash
curl -I https://www.gladlabs.io
# Note status code.

# Vercel deploy status
gh run list --repo Glad-Labs/glad-labs-stack --limit 5
```

**Fix.**

- 404 on routes that should exist → ISR cache issue, see `troubleshooting.md` "Post is Not Found" + "Static export writes succeed but the frontend still shows stale data"
- 500 → Vercel build broken; check the deploy logs in Vercel dashboard
- Connection refused → DNS / Vercel domain issue; check Vercel project settings

```bash
# Force a redeploy of the latest build
gh run rerun <latest_run_id> --repo Glad-Labs/glad-labs-stack
```

---

## Wan Server DEGRADED

**Means.** `curl http://localhost:9840/health` returned `degraded:true`. Video generation will silently fall back to SDXL/Pexels stills.

**Fix.** See [`troubleshooting.md`](./troubleshooting) entries:

- "Wan-server enters DEGRADED state — `/generate` returns 503 forever"
- "`poindexter-wan-server` container restart-loops every ~30 seconds"

Quick recovery:

```bash
docker restart poindexter-wan-server
sleep 30
curl -s http://localhost:9840/health | python -m json.tool
```

**TBD — needs operator confirmation:** No Grafana alert for this exists yet (per `troubleshooting.md`). The brain daemon should probe `wan-server /health` and alert; for now, this section assumes you noticed manually.

---

## Unknown alert — generic triage

If an alert fired with a name that's not in the index above:

### Step 1 — Find it in Grafana

```bash
# Open the alert rules dashboard (self-hosted Grafana)
xdg-open http://localhost:3000/alerting/list
# Or on Windows
start http://localhost:3000/alerting/list
# From phone via tailnet: http://100.81.93.12:3000/alerting/list
```

Find the firing rule. Read its `summary` and `description` annotations.

### Step 2 — Open the alert's source query

In Grafana → click the alert → "View rule" → copy the SQL. Run it directly:

```bash
docker exec -it poindexter-postgres-local psql -U poindexter -d poindexter_brain
# Paste the SQL, see the current value.
```

### Step 3 — Decide if it's real

- Value just barely over threshold → likely flapping; consider raising the threshold (it's in `infrastructure/grafana/provisioning/alerting/alert-rules.yml`)
- Value way over threshold → real issue; investigate the table the SQL queries

### Step 4 — Document it

Add a section above with the alert name, the meaning, and the fix you applied.

---

## Planned maintenance — muting alerts

If you're doing intentional work that will fire alerts (e.g., bringing the worker down for an upgrade):

```bash
# Pause the pipeline so it doesn't try to claim tasks during the downtime
poindexter settings set pipeline_paused true

# Mute Grafana alerts via the contact-point silence
# Open http://localhost:3000/alerting/silences/new (or via tailnet)
# Select all matchers (label=team / value=glad-labs), set duration, save.
```

After maintenance:

```bash
poindexter settings set pipeline_paused false
# Remove the silence in Grafana UI
```

---

## Escalation

This is a single-operator system. There is no on-call rotation.

- **Critical bugs in product code (Poindexter):** file a GitHub issue at https://github.com/Glad-Labs/poindexter/issues
- **Glad Labs business / operator issues:** file in the private tracker at https://github.com/Glad-Labs/glad-labs-stack/issues
- **Hardware issues:** Matt's PC build details are in `~/.claude/projects/C--Users-mattm/memory/user_hardware.md` for reference

---

## See also

- [`troubleshooting.md`](./troubleshooting) — symptom-driven debugging
- [`disaster-recovery.md`](./disaster-recovery) — catastrophic-loss playbooks
- [`secret-rotation.md`](./secret-rotation) — secret rotation procedures
- `infrastructure/grafana/provisioning/alerting/alert-rules.yml` — the alert rule source of truth
- `brain/` — brain daemon source (synthetic alerts fired directly from here)
