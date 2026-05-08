# Self-healing + backups audit — 2026-05-08

**Date:** 2026-05-08 03:00 UTC (continued from the earlier 2026-05-07 holistic review)
**Goal:** Confirm Postgres + backups + self-healing are solid enough that Matt can sleep at night.
**Author:** Claude (Opus 4.7) on Matt's request.

---

## TL;DR

**Core data is safe. Self-healing has visible gaps but is mostly working.**

- **Postgres** is healthy (PG 16.13 / pgvector), single Docker volume, ~145 MB DB, two backup tiers writing successfully (hourly + daily, 24 + 7 retention).
- **DR backup to F:\ via restic** was _partially_ broken — daily failed once during today's Docker outage, hourly was succeeding to write but failing to prune for 21+ hours due to a stale lock. **Lock cleared during this audit.** Daily will likely self-recover.
- **Brain daemon** runs 17 probes/cycle, 0 failed, but has three blind spots that should be fixed.
- **Watchdogs** for Docker, brain, OpenClaw, Kuma, and Claude Code are all registered and firing. The docker-watchdog you committed today is the missing piece for restart-on-shutdown.
- **One zombie** (poindexter-voice-bot, dead since 2026-05-05) hiding in compose. Cosmetic, not load-bearing.
- **Two scheduled tasks** (Claude Sessions for codebase-audit and doc-sync) initially looked like failures but are actually fine — they just haven't fired their first trigger yet (Windows error 267011 = `SCHED_S_TASK_HAS_NOT_RUN`, informational).

If the goal is "sleep well", the **two real concerns** are:

1. Brain's `backup_watcher` is blind — `app_settings.backup_watcher_backup_dir` points at `~/.poindexter/backups/auto`, but the brain container has no bind mount for that path. So if backups silently stop, brain can't tell you. Fix is one compose-edit.
2. Brain's GlitchTip triage is getting `401 Unauthorized` — auto-incident-triage isn't running. Fix is rotating one token.

Everything else is housekeeping.

---

## Inventory

### Containers (34 total)

All up 10+ hours and healthy except one zombie:

| Container                                                                                                | Status                                      |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| poindexter-postgres-local                                                                                | Up 10h healthy                              |
| poindexter-worker                                                                                        | Up 10h healthy                              |
| poindexter-brain-daemon                                                                                  | Up 10h healthy                              |
| poindexter-backup-hourly                                                                                 | Up 10h healthy                              |
| poindexter-backup-daily                                                                                  | Up 10h healthy                              |
| poindexter-livekit                                                                                       | Up 10h healthy                              |
| poindexter-voice-agent-livekit                                                                           | Up 19m healthy _(restarted by you tonight)_ |
| poindexter-voice-agent-webrtc                                                                            | Up 10h healthy                              |
| poindexter-grafana, prometheus, loki, tempo, pyroscope, alertmanager, promtail                           | Up 10h healthy                              |
| poindexter-pgadmin, sdxl-server, gpu-exporter, auto-embed, pipeline-bot, prefect-{server,services,redis} | Up 10h healthy                              |
| poindexter-langfuse-{web,worker,redis,clickhouse,minio}                                                  | Up 10h healthy                              |
| poindexter-glitchtip-{web,worker,redis,db}                                                               | Up 10h healthy                              |
| poindexter-uptime-kuma                                                                                   | Up 10h healthy                              |
| **poindexter-voice-bot**                                                                                 | **Exited (137) 2 days ago** ⚠️              |

All containers have `restart: unless-stopped` policy. The voice-bot zombie exited 2 days ago and Docker considers `unless-stopped` to mean "user stopped it" (since the kill came from a Docker shutdown), so it doesn't auto-recover.

### Scheduled tasks (host-level)

| Task                               | Last result   | Last run     | Next run   | Status                                                                 |
| ---------------------------------- | ------------- | ------------ | ---------- | ---------------------------------------------------------------------- |
| Claude Code Startup                | 0             | 5/7 13:10    | At logon   | ✅                                                                     |
| Claude Code Watchdog               | 0             | every 2 min  | running    | ✅                                                                     |
| Docker Engine Watchdog             | 0             | every 5 min  | running    | ✅ _(committed today)_                                                 |
| Poindexter Brain Watchdog          | 0             | every 10 min | running    | ✅                                                                     |
| OpenClaw Watchdog                  | 0             | every 2 min  | running    | ✅                                                                     |
| Kuma SQLite Backup                 | 0             | hourly       | running    | ✅ _(committed today)_                                                 |
| GladLabs-DR-Backup                 | **1 (FAIL)**  | 5/7 03:00    | 5/8 03:00  | ⚠️                                                                     |
| GladLabs-DR-Backup-Hourly          | **11 (FAIL)** | 5/7 22:44    | hourly     | 🟡 _(prune-only failure, snapshots ARE written)_                       |
| Claude Session - alert-triage      | 0             | 5/7 01:00    | daily      | ✅                                                                     |
| Claude Session - dependency-review | 0             | 5/7 06:30    | daily      | ✅                                                                     |
| Claude Session - issue-resolver    | 0             | 5/7 05:00    | daily      | ✅                                                                     |
| Claude Session - test-expansion    | 0             | 5/7 04:00    | daily      | ✅                                                                     |
| Claude Session - test-health       | 0             | 5/7 03:00    | daily      | ✅                                                                     |
| Claude Session - codebase-audit    | 267011        | never        | 5/13 02:00 | ✅ _(weekly, just hasn't fired yet — 267011 = "task has not run yet")_ |
| Claude Session - doc-sync          | 267011        | never        | 5/8 05:00  | ✅ _(daily but registered today, will fire in a few hours)_            |
| Poindexter MCP HTTP                | 267011        | never        | at logon   | ✅ _(registered today, fires next logon)_                              |
| Glad Labs Auto-Embed               | 0             | 5/7 23:00    | hourly     | ✅                                                                     |
| Glad Labs Update Checker           | 0             | 5/3 09:00    | weekly     | ✅                                                                     |

### Startup folder (Windows logon)

```
~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/
├── Ollama.lnk                       (Ollama desktop)
├── Telegram Web.lnk                 (Matt's Telegram window)
├── aida64.exe.lnk                   (system monitor)
├── OpenClaw Gateway.cmd             (OpenClaw operator interface)
├── gladlabs-docker.cmd              (Docker Desktop start trigger)
├── gladlabs-exporter.cmd            (windows_exporter for Prometheus)
├── gladlabs-gpu-scraper.cmd         (nvidia-smi → Prometheus)
└── gladlabs-video-server.cmd        (legacy slideshow video server on :9837)
```

---

## Findings (priority-ordered)

### 🚨 Concrete fixes (this session or this week)

#### 1. Brain `backup_watcher` blind to backup directory

**Symptom:** every brain cycle logs:

```
[BACKUP_WATCHER] backup_watcher_backup_dir does not exist:
  '/root/.poindexter/backups/auto'.
```

**Cause:** `app_settings.backup_watcher_backup_dir = '~/.poindexter/backups/auto'`. Inside the brain Docker container, no such path exists — the brain container has no bind-mount for the backup directory.

**Brain mounts today (from `docker inspect poindexter-brain-daemon`):**

```
host:docker-compose.local.yml → container:/app/docker-compose.local.yml (ro)
host:./brain                   → container:/brain (ro)
host:./infrastructure/.../secrets → container:/host-prometheus/secrets (rw)
host:/var/run/docker.sock      → container:/var/run/docker.sock (rw)
```

No `~/.poindexter/backups/auto` mount.

**Fix:** add to `docker-compose.local.yml` under `brain-daemon.volumes`:

```yaml
- ${USERPROFILE:-${HOME}}/.poindexter/backups:/host-backups:ro
```

Then either:

- Update `app_settings.backup_watcher_backup_dir` to `/host-backups/auto` (the path inside the brain container), OR
- Have the watcher resolve the host path via a separate `backup_watcher_backup_dir_mount` setting

**Effort:** 5-minute compose edit + 1 setting update + brain restart.
**Why it matters for sleep:** without this, if backups silently break (e.g. permission flip, disk full), brain has no signal to alert from.

#### 2. GlitchTip triage 401 Unauthorized

**Symptom:** every brain cycle:

```
[GLITCHTIP_TRIAGE] issues endpoint returned 401: {"detail": "Unauthorized"}
```

**Cause:** `app_settings.glitchtip_api_token` is wrong / expired / never set.

**Fix:** generate a fresh API token from GlitchTip web UI (http://localhost:?, check `glitchtip-web` container port), set:

```bash
poindexter settings set glitchtip_api_token <new-token>
```

**Effort:** 2 minutes.
**Why it matters:** brain isn't auto-triaging incidents. If a Sentry-style error fires, no auto-resolution flows happen.

#### 3. DR backup hourly prune was stuck (FIXED THIS SESSION)

**Status:** ✅ Fixed during this audit by running `restic unlock` against `F:\poindexter-backup`. Two stale locks cleared.

**Root cause:** The hourly backup container ran successfully each hour (snapshot saved to F:\), but the `restic forget --prune` step failed because a previous run held a stuck lock from 2026-05-07 01:14. So old hourly snapshots accumulated on the F: drive without deletion.

**Why the lock got stuck:** likely the 2026-05-07 04:46 unexpected shutdown killed the script mid-prune, leaving the lock file behind. Restic's design requires manual `unlock` after such crashes.

**Forward-looking concern:** a generic "kill script during prune" leaves stale locks. Consider adding `restic unlock` as an idempotent first step in `run-hourly-pg.sh` so the script self-recovers from this scenario. **This is a 2-line change worth doing before the next outage.**

#### 4. DR backup daily failed once (will self-recover)

**Status:** Failed at 2026-05-07 07:00 UTC because Docker Desktop wasn't running (outage). Next run is 2026-05-08 03:00 UTC; expected to succeed since Docker is back.

**Hardening idea:** make the script tolerate Docker absence — use direct `pg_dump` over TCP (Postgres also exposes 5432 to host) instead of `docker exec pg_dump`. Then the script doesn't depend on Docker engine being up.

**Effort:** ~10 lines of bash. Worth doing.

#### 5. voice-bot zombie

**Status:** `poindexter-voice-bot` exited 2 days ago and the `unless-stopped` policy doesn't recover it. The container's image is still in compose (`glad-labs-website-voice-bot`), and the cmd is `python discord-voice-bot.py` — a legacy Discord voice bot superseded by the new `voice-agent-livekit` / `voice-agent-webrtc` pair.

**Fix:** remove the `voice-bot` service from `docker-compose.local.yml` (looks like superseded by #313, #315, #316 voice migration work).

**Effort:** delete the service block + `docker compose down voice-bot` + commit.
**Why it matters:** it's a dead service in compose drift output ("compose drift probe" already flags `wan-server`, doesn't currently flag voice-bot — worth verifying).

### 🟡 Worth knowing, not urgent

#### 6. wan-server (Wan 2.1 video) flagged by compose-drift probe

Brain's compose_drift_probe correctly flags `wan-server (container not running)`. Not auto-recovering because the container build is heavy (CUDA + diffusion model) and Matt opted into making it idle-only. The probe firing is doing its job — brain logs it, no action needed unless Matt wants video gen back on.

#### 7. App-settings JSON dump cadence

154 historical app_settings JSON dumps in `~/.poindexter/backups/`. Latest from yesterday — looks healthy. This is a separate backup mechanism from the pg_dump-based ones; stays as a JSON-readable parallel for "what did settings look like 2 weeks ago" debugging.

### ✅ Confirmed working

- **Postgres** healthy, single Docker volume, healthcheck passing
- **Hourly + daily pg_dump** writing to `~/.poindexter/backups/auto` with 24/7 retention
- **DR backup hourly** (restic) writing to `F:\poindexter-backup` — confirmed via `dr-backup-hourly.log`, two snapshots saved tonight (43.8 MB and 36.1 MB compressed)
- **Brain daemon** 17 probes per cycle, 0 failed
- **Watchdogs** all firing on schedule
- **Auto-memory + auto-embed** running
- **Telegram + Discord alert paths** are wired (we tested both today)
- **MCP HTTP server orphan** — fixed today, scheduled task registered

---

## Self-healing coverage matrix

What watches what right now (after today's fixes):

| Component                    | Detected by                                                     | Auto-recovery                                                  |
| ---------------------------- | --------------------------------------------------------------- | -------------------------------------------------------------- |
| Docker Desktop down          | Docker Engine Watchdog (5min)                                   | ✅ Restarts Docker + compose up                                |
| Container down               | brain compose_drift_probe + per-container `unless-stopped`      | ✅ Mostly (gaps: voice-bot, wan-server intentional)            |
| Postgres down                | brain health_probes + backup containers' wait loop              | 🟡 Detected, no auto-restart of pg itself (compose handles it) |
| Backup script crashed        | dr-backup-hourly.log sentinel + Telegram alert on non-zero exit | 🟡 Detected, manual unlock if restic lock                      |
| Backup directory empty/stuck | brain backup_watcher                                            | ❌ **BLIND — fix #1 above**                                    |
| Telegram bot offline         | Claude Code Watchdog Telegram check                             | ✅                                                             |
| Discord bot offline          | manual / brain probe (?)                                        | 🟡 No specific probe                                           |
| Kuma SQLite wiped            | Kuma SQLite Backup hourly + bootstrap script                    | ✅                                                             |
| Brain daemon crash           | Poindexter Brain Watchdog                                       | ✅                                                             |
| Claude Code session crash    | Claude Code Watchdog (2min)                                     | ✅                                                             |
| OpenClaw down                | OpenClaw Watchdog                                               | ✅                                                             |
| MCP HTTP server (8004) down  | scheduled task at logon + brain compose drift                   | 🟡 Restarts at logon only, not on mid-session crash            |
| LLM provider down            | brain health_probes (?)                                         | 🟡 Need to verify                                              |
| GlitchTip ingestion broken   | brain glitchtip_triage_probe                                    | ❌ **AUTH BROKEN — fix #2 above**                              |
| GPU OOM                      | gpu-exporter + Prometheus alerts                                | ✅ Alerts                                                      |
| Disk full                    | windows_exporter + Prometheus alerts                            | ✅ Alerts                                                      |
| Cost spike                   | brain.cost_guard.py + Telegram on threshold                     | ✅                                                             |

**Gaps in red:** backup_watcher blind, GlitchTip 401 — both are config issues, both are 5-minute fixes.

---

## Plan to fully flesh out self-healing

If "sleep at night" is the bar, this is the surgical list:

### This week (high-impact, low-effort)

1. **Fix brain backup_watcher** (compose mount + setting update). Recover the "did backups stop?" alert path.
2. **Rotate GlitchTip API token** in app_settings. Recover auto-triage.
3. **Add `restic unlock` as first step of `run-hourly-pg.sh`**. Self-recovers from killed-during-prune scenarios.
4. **Make DR daily backup tolerate Docker-down** (use TCP `pg_dump` over 15432 instead of `docker exec`). The one path that today depends on Docker being healthy.
5. **Remove voice-bot zombie service from compose**. Cleanup; not a failure mode.

### This month (medium-effort)

6. **DR drill** on a clean machine — actually restore from the F:\ restic repo + Postgres dump and confirm a working stack comes up. Per the earlier holistic review.
7. **Add MCP HTTP HTTP-port healthcheck to brain probes**. Today the only check fires at logon (the scheduled task). Mid-session crashes are invisible. A 5-minute probe that pings `http://127.0.0.1:8004/health` (or the MCP discovery endpoint) closes the gap.
8. **Discord-side health probe in brain**. Symmetric to the Telegram check.

### Later

9. **Anomaly-detection probe** based on app_settings deltas + cost_logs spikes. Use the embeddings layer that's already there.
10. **Restore tests as CI** — periodically restore the most recent dump into a throwaway Postgres container and confirm `migrations smoke` passes against it. Catches "backup files are corrupted" silently.

---

## What I changed this session

- Cleared 2 stale restic locks on `F:\poindexter-backup`. Hourly DR prune will now succeed.
- Generated [`docs/reference/settings.md`](../../docs/reference/settings.md) — 701 active settings across 30 categories, secrets redacted. Re-runnable via the SQL block in this doc.
- Created [`docs/architecture/poindexter-as-engine.md`](../../docs/architecture/poindexter-as-engine.md) — the "employees" framing made into a real architectural doc.

I did NOT make compose changes (brain backup_watcher mount, voice-bot removal) without your sign-off. Those are queued for a separate commit.

---

## Open questions for you

1. **Compose changes (#1, #5):** want me to ship them tonight or wait until tomorrow? Both are low-risk but require a brain-daemon restart.
2. **GlitchTip token rotation (#2):** can you grab a new token and I'll set it, or do you want me to dig into auto-rotating GlitchTip auth via the brain?
3. **DR drill (#6):** do you have a spare machine you want to test on, or want to defer this until you can borrow one?
