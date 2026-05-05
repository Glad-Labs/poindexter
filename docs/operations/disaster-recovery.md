# Disaster Recovery Runbook

**Last reviewed:** 2026-04-30
**Audience:** solo operator (Matt) at 2am during an incident
**Prereqs:** Local PC online, Docker running, gh CLI authed, poindexter CLI installed, `~/.poindexter/bootstrap.toml` accessible (or you have the `database_url` + `POINDEXTER_SECRET_KEY` somewhere safe)

This runbook covers catastrophic-loss scenarios — the kind where "just restart the container" is not enough. For per-alert triage and routing, see [`incident-response.md`](./incident-response). For known-pattern symptom debugging, see [`troubleshooting.md`](./troubleshooting).

---

## Scenarios this covers

- **DB-1.** PostgreSQL container is healthy but data is corrupted / wrong / missing rows
- **DB-2.** `poindexter-postgres-local` Docker volume was wiped or destroyed (full data loss)
- **DB-3.** A migration ran half-way, left the schema in an inconsistent state
- **HOST-1.** New machine — rebuild the entire local stack from scratch (laptop died, fresh OS install)
- **HOST-2.** Docker Desktop / WSL2 corrupted — need to reset Docker but keep the data
- **CONFIG-1.** `bootstrap.toml` lost — need to reconstruct it
- **CONFIG-2.** `POINDEXTER_SECRET_KEY` lost — every encrypted secret in `app_settings` is unreadable

For per-service recovery (worker crashed, SDXL degraded, Vercel down), see the **Per-service recovery** section at the bottom.

---

## Quick triage flowchart

```
Is the postgres container running?
  NO  -> Try DB-1 (restart)
  YES -> Can you connect with psql?
            NO  -> Container is broken: DB-1 / DB-2 (volume gone)
            YES -> Are the tables there? (check `app_settings`, `posts`, `pipeline_tasks`)
                     NO  -> DB-2 (volume wiped, fresh DB)
                     YES -> Does data look right? (post counts, recent rows)
                              NO  -> DB-1 (corruption, restore from backup)
                              YES -> Issue is downstream — check incident-response.md

Is `~/.poindexter/bootstrap.toml` present and readable?
  NO  -> CONFIG-1 (reconstruct bootstrap.toml)

Is `POINDEXTER_SECRET_KEY` set in env / bootstrap.toml?
  NO  -> CONFIG-2 (re-generate key + re-seed every secret)

Can you run `docker ps`?
  NO  -> HOST-2 (Docker broken)

Is this a brand new machine / fresh OS?
  YES -> HOST-1 (full rebuild)
```

---

## Procedure: DB-1 — Restore PostgreSQL from backup

**Symptoms.** Container is running, you can connect, but data is wrong (missing rows, bad migration, accidental DELETE, etc.).

### Step 1 — Stop the worker so it doesn't fight you

```bash
docker stop poindexter-worker poindexter-brain-daemon
```

### Step 2 — List available backups

Backups land in `~/.poindexter/backups/` via `scripts/db-backup-local.sh` (run by cron / scheduled task). 14-day retention by default.

```bash
ls -lht ~/.poindexter/backups/ | head
# Expected: poindexter-db-2026-04-30T0312Z.dump  ...
```

If the directory is empty or hasn't been updated recently, your backup automation is broken — go to **DB-2** and treat this as a volume-loss event (your last good state is whatever's in git/Vercel/R2).

### Step 3 — Verify the backup is restorable BEFORE you drop the live DB

```bash
pg_restore --list ~/.poindexter/backups/poindexter-db-<TIMESTAMP>.dump | head -20
# Expected: a list of TABLE / INDEX / SEQUENCE entries.
# If pg_restore errors, the backup is corrupt — try the next-oldest one.
```

### Step 4 — Drop and recreate the DB

```bash
# Connect as superuser
docker exec -it poindexter-postgres-local psql -U poindexter -d postgres

# Inside psql:
DROP DATABASE poindexter_brain;
CREATE DATABASE poindexter_brain OWNER poindexter;
\q
```

### Step 5 — Restore from the backup

```bash
docker exec -i poindexter-postgres-local pg_restore \
    -U poindexter \
    -d poindexter_brain \
    --no-owner --no-privileges \
    < ~/.poindexter/backups/poindexter-db-<TIMESTAMP>.dump
```

Expected output: a stream of `pg_restore: processing data for table "..."` lines, then exit 0. Some warnings about extensions (`pgcrypto`, `vector`) are normal — they'll be re-created.

### Step 6 — Re-apply any migrations newer than the backup

```bash
poindexter migrate status
# Compare the "applied" list against what's on disk.
poindexter migrate up
```

### Step 7 — Bring the worker back

```bash
docker start poindexter-brain-daemon poindexter-worker
sleep 10
curl -s http://localhost:8002/api/health | python -m json.tool
```

### Verification

```bash
# Row counts should be in the right ballpark for the backup vintage
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT 'posts' AS tbl, COUNT(*) FROM posts UNION ALL
   SELECT 'app_settings', COUNT(*) FROM app_settings UNION ALL
   SELECT 'pipeline_tasks', COUNT(*) FROM pipeline_tasks;"
```

---

## Procedure: DB-2 — `poindexter-postgres-local` data volume was wiped

**Symptoms.** `docker ps` shows postgres is up, but `psql -c "\dt"` returns "Did not find any relations" — the database exists but is empty. Or the container won't even start because the volume mount is missing.

**Worst case** — you lost the volume AND have no backup. The static export on R2 / public-site frontend still has the published posts. The git history has the schema migrations. Everything else is gone.

### Step 1 — Confirm the volume is actually gone

```bash
docker volume ls | grep postgres
# Expected names: poindexter-postgres-data, postgres-local-data, etc.

docker volume inspect <volume_name> 2>&1 | head -10
# If "No such volume", it's gone.
```

### Step 2 — Stop everything that talks to the DB

```bash
docker stop poindexter-worker poindexter-brain-daemon poindexter-grafana \
  poindexter-langfuse-web poindexter-langfuse-worker 2>/dev/null
```

### Step 3 — IF you have a backup, restore it

Same as **DB-1** Steps 4-7. If you got here because backups were missing too, continue.

### Step 4 — IF no backup exists, rebuild the schema

```bash
# Ensure the container has a fresh empty volume
docker compose -f docker-compose.local.yml up -d postgres-local
sleep 5

# Run all migrations from scratch
poindexter migrate up
# Expected: "Applied N migration(s)"
```

### Step 5 — Recreate the secrets that lived in `app_settings`

This is the painful part. Encrypted secrets are gone forever. You need to:

1. Go to [`secret-rotation.md`](./secret-rotation) and rotate **every** API key listed in the inventory (Telegram, Discord, OpenAI, Anthropic, Lemon Squeezy, Resend, Cloudinary, Pexels, etc.) — treat all of them as compromised because the encrypted blobs may have leaked with the volume backup.
2. Re-seed each one with `poindexter set` (see secret-rotation.md "Re-seeding from scratch").

### Step 6 — Re-import published posts from R2

The frontend reads `static/posts/index.json` from R2. That's your source of truth for what was live.

**TBD — needs operator to confirm the procedure once they've done it once.** There's no off-the-shelf re-import script. Sketch:

```bash
# Pull the live index from R2
curl -sf https://<r2-public-url>/static/posts/index.json -o /tmp/posts-index.json
# For each slug, pull static/posts/<slug>.json and INSERT into the posts table.
# Manual SQL or a one-off script — not currently in the repo.
```

File a follow-up issue if this scenario actually happens (#TBD).

### Verification

```bash
poindexter migrate status     # Should show all migrations applied.
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT COUNT(*) FROM app_settings;"   # Should be > 0 after migration seeds run.
```

---

## Procedure: DB-3 — Migration ran half-way, schema is inconsistent

**Symptoms.** A `poindexter migrate up` errored mid-run. `poindexter migrate status` shows a migration as "pending" but you can see partial DDL was already applied (e.g., a column exists that shouldn't be there until that migration completes).

### Step 1 — Capture the failure mode

```bash
poindexter migrate status > /tmp/migrate-status-before.txt
# Note the LAST applied migration name. The pending one(s) after it are the suspects.
```

### Step 2 — Choose: roll forward or roll back?

**Roll forward** is preferred when:

- The failed migration is small and you can eyeball what didn't run
- Re-running it (after manual cleanup) is safe (the DDL is idempotent or you can drop the half-applied object)

**Roll back** is preferred when:

- The migration is complex
- You have a recent backup and can afford to lose a few minutes of activity

### Step 3a — Roll forward

```bash
# Inspect the migration file
cat src/cofounder_agent/services/migrations/<NNNN>_<name>.py

# Manually drop / undo whatever DDL the failed migration partially applied
docker exec -it poindexter-postgres-local psql -U poindexter -d poindexter_brain
# Inside psql, e.g.:
#   DROP TABLE IF EXISTS half_created_table;
#   ALTER TABLE existing_table DROP COLUMN IF EXISTS half_added_col;
# \q

# Re-run
poindexter migrate up
```

### Step 3b — Roll back

```bash
# Roll back to the migration immediately BEFORE the broken one
poindexter migrate down --to <NNNN_previous_known_good>
# Confirms with prompt; pass --yes to skip.

# Verify
poindexter migrate status
```

If the failing migration has no `down()`/`rollback_migration()`, the down command will skip it. In that case, restore from backup (**DB-1**) to the last good state.

### Step 4 — Bring services back up

```bash
docker start poindexter-worker poindexter-brain-daemon
```

### Verification

```bash
poindexter migrate status   # All applied or all pending — no half-states.
curl -s http://localhost:8002/api/health | python -m json.tool
```

---

## Procedure: HOST-1 — Rebuild the entire local stack on a new machine

**Scenario.** Laptop died, fresh OS install, or you're moving to new hardware.

### Step 1 — Install prerequisites

| Tool                                   | Why                          |
| -------------------------------------- | ---------------------------- |
| Docker Desktop 4.26+ with WSL2 backend | Runs all containers          |
| Git + Git Bash (Windows)               | start-stack.sh uses bash     |
| Python 3.12+                           | poindexter CLI               |
| Node.js 22+                            | Public site dev/build        |
| Ollama 0.1.40+                         | Local LLM inference          |
| NVIDIA driver supporting CUDA 12.8+    | SDXL + Wan video on RTX 5090 |

### Step 2 — Clone the repo

```bash
git clone git@github.com:Glad-Labs/glad-labs-stack.git ~/glad-labs-stack
cd ~/glad-labs-stack
```

### Step 3 — Restore secrets

You need the following from a safe place (1Password, encrypted USB, second machine):

- `~/.poindexter/bootstrap.toml` — contains `database_url` and `POINDEXTER_SECRET_KEY`
- A recent backup from `~/.poindexter/backups/` (or the cloud copy if you set one up)

```bash
mkdir -p ~/.poindexter/backups
cp /path/to/backup/bootstrap.toml ~/.poindexter/bootstrap.toml
chmod 600 ~/.poindexter/bootstrap.toml
cp /path/to/backup/poindexter-db-*.dump ~/.poindexter/backups/
```

If you don't have either, skip ahead — we'll generate a fresh bootstrap and rebuild from migrations + R2.

### Step 4 — Install poindexter

```bash
pip install -e src/cofounder_agent
poindexter --help    # confirm install
```

### Step 5 — Bring up the stack

```bash
bash scripts/start-stack.sh
# Expected: postgres-local, worker, brain-daemon, grafana, etc. all start.

docker ps --format "table {{.Names}}\t{{.Status}}"
```

### Step 6 — Restore data

If you have a backup → follow **DB-1** Steps 4-7 from "Drop and recreate the DB."

If no backup → run migrations to create empty schema, then go to **DB-2** Steps 5-6.

### Step 7 — Pull Ollama models

```bash
ollama pull nomic-embed-text
ollama pull gemma3:27b
ollama pull qwen3:8b
ollama pull glm-4.7-5090:latest    # writer model — large
# Confirm with:
ollama list
```

### Step 8 — Verify everything

```bash
curl -s http://localhost:8002/api/health | python -m json.tool
curl -s http://localhost:3000/api/health
curl -s http://localhost:9836/health     # SDXL
curl -s http://localhost:11434/api/tags  # Ollama
```

### Step 9 — Re-pair MCP / OpenClaw / Telegram / Discord

```bash
# Telegram pairing — use the /telegram:configure skill in Claude Code
# Discord pairing — use the /discord:configure skill in Claude Code
# OpenClaw — re-install via the existing setup script + paste tokens
```

---

## Procedure: HOST-2 — Docker / WSL2 broken, keep the data

**Symptoms.** `docker ps` errors, Docker Desktop refuses to start, WSL2 is unresponsive. The data is fine — only the runtime is busted.

### Step 1 — Try a soft reset first

```powershell
# PowerShell, as admin
wsl --shutdown
Restart-Service com.docker.service -Force
# Wait 30s, try `docker ps` again
```

### Step 2 — If that fails, reset Docker Desktop (preserves volumes by default)

In Docker Desktop GUI: Settings → Troubleshoot → "Reset to factory defaults" **WARNING — this nukes named volumes**. Use "Clean / Purge data" only if you have a current backup.

The safer path: **uninstall Docker Desktop, reinstall same version, restart**. Named volumes survive a reinstall as long as you don't delete the WSL2 distro `docker-desktop-data`.

### Step 3 — Restart the stack

```bash
bash scripts/start-stack.sh
docker volume ls    # Confirm postgres volume still exists
```

### Step 4 — Verify data integrity

```bash
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT COUNT(*) FROM posts; SELECT COUNT(*) FROM app_settings;"
# Compare against what you remember the counts being.
```

If the counts are way off, treat as **DB-2** (volume loss).

---

## Procedure: CONFIG-1 — Lost `bootstrap.toml`

**Symptoms.** Worker won't start. Logs say `notify_operator(): no DATABASE_URL resolved`, then `sys.exit(2)`.

### Step 1 — Reconstruct the file

```bash
mkdir -p ~/.poindexter
cat > ~/.poindexter/bootstrap.toml <<'EOF'
database_url = "postgresql://poindexter:<password>@localhost:15432/poindexter_brain"

# Operator notification fallbacks (optional — used when DB is unreachable)
telegram_bot_token = ""
telegram_chat_id = ""
discord_ops_webhook_url = ""

# Symmetric key for app_settings encrypted secrets — REQUIRED if you
# have any is_secret=true rows.
POINDEXTER_SECRET_KEY = "<existing key>"
EOF
chmod 600 ~/.poindexter/bootstrap.toml
```

You need the **postgres password** (find it: `docker exec poindexter-postgres-local printenv POSTGRES_PASSWORD`) and the **`POINDEXTER_SECRET_KEY`** (this is the killer — see CONFIG-2 if lost).

### Step 2 — Restart worker

```bash
docker restart poindexter-worker
sleep 5
curl -s http://localhost:8002/api/health
```

### Verification

```bash
# Worker should now start without sys.exit(2)
docker logs poindexter-worker 2>&1 | tail -20
```

---

## Procedure: CONFIG-2 — Lost `POINDEXTER_SECRET_KEY`

**Symptoms.** Worker boots, but every `get_secret(...)` call raises `SecretsError: Could not decrypt`. Telegram/Discord/Vercel revalidation/Lemon Squeezy webhook verification all 401. `app_settings` rows with `is_secret=true` show `enc:v1:...` blobs that can never be decoded again.

**There is no recovery for the encrypted blobs.** Encryption with AES-256 + a lost key is final. You must rotate every secret.

### Step 1 — Generate a new key

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Copy the output, save it RIGHT NOW (1Password / encrypted USB).
```

### Step 2 — Update `bootstrap.toml`

```bash
# Edit ~/.poindexter/bootstrap.toml — set POINDEXTER_SECRET_KEY = "<new key>"
chmod 600 ~/.poindexter/bootstrap.toml
```

### Step 3 — Wipe the dead encrypted rows

```bash
docker exec -it poindexter-postgres-local psql -U poindexter -d poindexter_brain
```

```sql
-- See what's gone
SELECT key, length(value) FROM app_settings
WHERE is_secret = TRUE AND value LIKE 'enc:v1:%';

-- Wipe them so they don't poison future reads
UPDATE app_settings SET value = '' WHERE is_secret = TRUE AND value LIKE 'enc:v1:%';
```

### Step 4 — Restart the worker so it picks up the new key

```bash
docker restart poindexter-worker
```

### Step 5 — Re-seed every secret

Follow [`secret-rotation.md`](./secret-rotation) — go through the inventory and rotate / re-add each one.

### Verification

```bash
# No more enc:v1: blobs with empty values
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT key FROM app_settings WHERE is_secret=TRUE AND value=''"
# Expected: empty after you've re-seeded all the secrets you actually use.

# Trigger a real downstream that uses a secret (e.g. Telegram alert).
# Mint a fresh JWT for the CLI client first — see `poindexter auth
# mint-token --client-id <pdx_xxx> --client-secret <secret>`.
curl -s -X POST http://localhost:8002/api/test/notify-operator \
  -H "Authorization: Bearer $JWT"
# Expected: Telegram message arrives.
```

---

## Per-service recovery (subordinate playbooks)

For when a single service is down but the rest of the stack is fine. These were the original disaster-recovery entries — kept here as a quick reference.

### Brain Daemon — no Telegram alerts, no `brain_decisions` rows

```bash
docker ps | grep brain-daemon
docker compose -f docker-compose.local.yml up -d brain-daemon
cat ~/.poindexter/heartbeat   # Should be < 5 min old
```

The OS-level watchdog (Task Scheduler / cron) auto-restarts within 10 minutes.

### Content Worker (FastAPI) — pipeline stalled, `/api/health` fails

```bash
curl -s http://localhost:8002/api/health
docker compose -f docker-compose.local.yml up -d worker
# Or full stack: bash scripts/start-stack.sh
```

### Ollama — content generation fails, "Ollama Unresponsive" alert

```bash
curl -s http://localhost:11434/api/tags
ollama serve   # If not running as service
ollama list    # Verify required models present
```

### SDXL Server — posts publishing with Pexels stock images

```bash
curl -s http://localhost:9836/health | python -m json.tool
docker compose -f docker-compose.local.yml up -d sdxl-server
# If torch/torchvision mismatch:
docker exec poindexter-sdxl-server pip install --upgrade torchvision peft
docker restart poindexter-sdxl-server
```

### Vercel — site returns 500 / blank page

```bash
curl -sf https://www.gladlabs.io
gh run list --repo Glad-Labs/glad-labs-stack --limit 3
gh run rerun <run_id> --repo Glad-Labs/glad-labs-stack
```

### Grafana — dashboards down

```bash
docker compose -f docker-compose.local.yml up -d grafana
curl -s http://localhost:3000/api/health
# Dashboards are file-provisioned and reload on restart.
```

The brain daemon alerts via Telegram **directly** — Grafana down does NOT mute alerts.

### OpenClaw Gateway — bot unreachable via Discord/Telegram

```bash
curl -s http://127.0.0.1:18789/
# Windows restart:
start "OpenClaw Gateway" cmd /d /c "%USERPROFILE%\.openclaw\gateway.cmd"
```

`openclaw-watchdog.ps1` auto-restarts on health failure.

### GPU metrics stale ("GPU Metrics Stale" alert)

```bash
curl -s http://localhost:9835/metrics | head -5
# Restart scraper (Windows):
taskkill /IM python.exe /FI "WINDOWTITLE eq nvidia-smi-exporter"
pythonw scripts/nvidia-smi-exporter.py
```

---

## Backup hygiene — preventing the next disaster

- `scripts/db-backup-local.sh` runs daily and prunes after 14 days. If you don't see a fresh dump in `~/.poindexter/backups/` every morning, your scheduled task is broken — fix it before the next incident, not during.
- **`bootstrap.toml` should be in your password manager** as a secure note. If it dies, you're in CONFIG-1 + CONFIG-2 territory — minimum 1 hour of pain.
- **Take periodic offsite backups** of `~/.poindexter/backups/`. Cloud sync (rclone → R2/S3) once a week is enough.
- **`POINDEXTER_SECRET_KEY` is the doomsday key.** Print it on paper, put it in a fireproof safe. If you lose it AND the live `app_settings` table, you cannot recover the encrypted secrets — only re-issue them.

---

## See also

- [`incident-response.md`](./incident-response) — alert routing and triage
- [`secret-rotation.md`](./secret-rotation) — rotating individual secrets
- [`troubleshooting.md`](./troubleshooting) — known-symptom debugging entries
- [`local-development-setup.md`](./local-development-setup) — fresh setup walkthrough
- [`ci-deploy-chain.md`](./ci-deploy-chain) — how Vercel deploys are wired
- `scripts/db-backup-local.sh` — backup script (cron / scheduled task)
- `src/cofounder_agent/plugins/secrets.py` — encryption module reference

## Contact

- Matt Gladding: hello@gladlabs.io
- Telegram: brain daemon sends alerts to configured `chat_id`
- Discord: ops channel receives Grafana alerts
