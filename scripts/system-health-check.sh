#!/usr/bin/env bash
#
# Poindexter system-health checklist.
#
# Walks every major subsystem and reports PASS / WARN / FAIL with a
# next-step hint when something isn't green. Designed so any operator
# can run it fresh and know within ~60 seconds whether the stack is
# healthy.
#
# Modes:
#   bash scripts/system-health-check.sh            # interactive (default)
#   bash scripts/system-health-check.sh --all      # run every check, no prompts
#   bash scripts/system-health-check.sh --only=<section>  # one section
#
# Sections (case-insensitive, space/comma-separated):
#   infra, db, worker, models, media, alerts, pipeline, publish, secrets, scheduled
#
# Exit codes: 0 = all PASS, 1 = any WARN, 2 = any FAIL, 3 = check error.
#
# Written 2026-04-24 in response to operator request for "a way to
# verify everything is working without reading through the logs".

set -u

# Colors
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
  C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""
fi

MODE_INTERACTIVE=1
ONLY_SECTION=""

for arg in "$@"; do
  case "$arg" in
    --all) MODE_INTERACTIVE=0 ;;
    --only=*) ONLY_SECTION="${arg#--only=}" ;;
    -h|--help)
      sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
done

# Tally
PASS=0
WARN=0
FAIL=0
TOTAL=0

section_active() {
  [ -z "$ONLY_SECTION" ] && return 0
  [[ ",$ONLY_SECTION," == *",$1,"* ]]
}

check() {
  local section="$1"; shift
  local label="$1"; shift
  # Remaining args run as the check; exit 0 = PASS, 1 = WARN, 2 = FAIL.
  # stdout/stderr captured for hint lines.

  section_active "$section" || return 0

  TOTAL=$((TOTAL + 1))
  printf "%s[%02d]%s %-58s" "$C_DIM" "$TOTAL" "$C_RESET" "$label"
  local output
  output=$("$@" 2>&1)
  local rc=$?
  case "$rc" in
    0) printf "%s PASS%s" "$C_GREEN" "$C_RESET"; PASS=$((PASS + 1)) ;;
    1) printf "%s WARN%s" "$C_YELLOW" "$C_RESET"; WARN=$((WARN + 1)) ;;
    2) printf "%s FAIL%s" "$C_RED" "$C_RESET"; FAIL=$((FAIL + 1)) ;;
    *) printf "%s ERR%s ", "$C_RED" "$C_RESET"; FAIL=$((FAIL + 1)) ;;
  esac
  if [ -n "$output" ]; then
    # Take first two lines of output as the hint, indented.
    printf "\n%s    %s%s\n" "$C_DIM" "$(echo "$output" | head -2 | tr '\n' ' ')" "$C_RESET"
  else
    printf "\n"
  fi

  # Interactive prompt after each FAIL/WARN
  if [ "$MODE_INTERACTIVE" = "1" ] && [ "$rc" != "0" ]; then
    printf "%s    Continue? [Y/n/q]: %s" "$C_DIM" "$C_RESET"
    read -r reply
    case "$reply" in
      [nN]|[qQ]) echo "Aborted by operator."; exit 3 ;;
    esac
  fi
}

section() {
  section_active "$1" || return 0
  printf "\n%s== %s ==%s\n" "$C_BOLD$C_CYAN" "$2" "$C_RESET"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

psql_exec() {
  docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain \
    -t -A -c "$1" 2>&1
}

worker_api() {
  curl -s -m 5 -H "Authorization: Bearer dev-token" "$@"
}

# ---------------------------------------------------------------------------
# Infra: docker + postgres + worker reachable
# ---------------------------------------------------------------------------

section infra "Infrastructure"

check infra "Docker daemon responding" bash -c '
  docker version --format "{{.Server.Version}}" >/dev/null 2>&1 || { echo "docker not running — start Docker Desktop"; exit 2; }
'

check infra "Expected containers running" bash -c '
  expected="poindexter-postgres-local poindexter-worker poindexter-grafana poindexter-prometheus poindexter-alertmanager poindexter-sdxl-server poindexter-voice-bot poindexter-brain-daemon"
  missing=""
  for c in $expected; do
    state=$(docker inspect -f "{{.State.Status}}" "$c" 2>/dev/null)
    [ "$state" = "running" ] || missing="$missing $c"
  done
  if [ -n "$missing" ]; then
    echo "missing/not-running:$missing — run bash scripts/start-stack.sh"
    exit 2
  fi
'

check infra "Postgres accepting connections" bash -c '
  psql_output=$(docker exec poindexter-postgres-local pg_isready -U poindexter 2>&1)
  echo "$psql_output" | grep -q "accepting connections" || { echo "$psql_output"; exit 2; }
'

check infra "Worker /api/health healthy" bash -c '
  resp=$(curl -s -m 5 http://localhost:8002/api/health 2>&1)
  echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get(\"status\")==\"healthy\" else 2)" || { echo "$resp" | head -c 200; exit 2; }
'

check infra "Disk usage under 80%" bash -c '
  pct=$(df -h /c 2>/dev/null | awk "NR==2 {gsub(\"%\", \"\"); print \$5}" || df -h / | awk "NR==2 {gsub(\"%\", \"\"); print \$5}")
  pct=${pct:-0}
  if [ "$pct" -gt 90 ]; then echo "disk ${pct}% full — clean up embeddings/podcasts/videos"; exit 2; fi
  if [ "$pct" -gt 80 ]; then echo "disk ${pct}% full — monitor"; exit 1; fi
  echo "disk usage ${pct}%"
'

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

section db "Database"

check db "Required tables present" bash -c '
  required="content_tasks posts app_settings media_assets embeddings audit_log_events alert_events"
  missing=""
  for t in $required; do
    exists=$(psql_exec "SELECT 1 FROM information_schema.tables WHERE table_name=\"$t\";")
    [ "$exists" = "1" ] || missing="$missing $t"
  done
  [ -z "$missing" ] || { echo "missing tables:$missing — check migrations"; exit 2; }
'

check db "Recent task activity (last 24h)" bash -c '
  count=$(psql_exec "SELECT COUNT(*) FROM content_tasks WHERE created_at > NOW() - INTERVAL \"24 hours\";")
  count=${count:-0}
  if [ "$count" = "0" ]; then echo "zero tasks in 24h — autonomous generation may be stopped"; exit 1; fi
  echo "$count tasks created in last 24h"
'

check db "Approval queue within cap" bash -c '
  count=$(psql_exec "SELECT COUNT(*) FROM content_tasks WHERE status=\"awaiting_approval\";")
  cap=$(psql_exec "SELECT value FROM app_settings WHERE key=\"max_approval_queue\";")
  count=${count:-0}; cap=${cap:-20}
  pct=$((count * 100 / cap))
  if [ "$pct" -ge 100 ]; then echo "queue full ($count/$cap) — throttling generation"; exit 2; fi
  if [ "$pct" -ge 80 ]; then echo "queue at ${pct}% ($count/$cap) — review soon"; exit 1; fi
  echo "queue $count/$cap"
'

check db "Embeddings table size reasonable" bash -c '
  rows=$(psql_exec "SELECT COUNT(*) FROM embeddings;")
  size=$(psql_exec "SELECT pg_size_pretty(pg_total_relation_size(\"embeddings\"));")
  rows=${rows:-0}
  if [ "$rows" -gt 100000 ]; then echo "$rows rows ($size) — run collapse_old_embeddings job"; exit 1; fi
  echo "$rows rows, $size"
'

# ---------------------------------------------------------------------------
# LLM / GPU models
# ---------------------------------------------------------------------------

section models "LLM & GPU Models"

check models "Ollama responding" bash -c '
  tags=$(curl -s -m 5 http://localhost:11434/api/tags 2>&1)
  echo "$tags" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get(\"models\", [])), \"models loaded\"); sys.exit(0)" || { echo "ollama not responding"; exit 2; }
'

check models "Required Ollama models present" bash -c '
  required="gemma3:27b qwen3:8b nomic-embed-text:latest"
  tags=$(curl -s -m 5 http://localhost:11434/api/tags 2>/dev/null)
  missing=""
  for m in $required; do
    echo "$tags" | grep -q "\"name\":\"$m\"" || missing="$missing $m"
  done
  [ -z "$missing" ] || { echo "missing:$missing — run: ollama pull <model>"; exit 2; }
'

check models "Configured writer model exists locally" bash -c '
  writer=$(psql_exec "SELECT value FROM app_settings WHERE key=\"pipeline_writer_model\";" | sed "s|^ollama/||")
  tags=$(curl -s -m 5 http://localhost:11434/api/tags 2>/dev/null)
  echo "$tags" | grep -q "\"name\":\"$writer\"" || { echo "writer model $writer not in ollama — ollama pull $writer"; exit 2; }
  echo "writer=$writer"
'

check models "SDXL server idle/ready" bash -c '
  resp=$(curl -s -m 3 http://localhost:9836/health 2>&1)
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"status\", \"\"))" 2>/dev/null)
  case "$status" in
    idle|ok|ready) echo "sdxl status=$status" ;;
    busy) echo "sdxl busy — in use by pipeline"; exit 1 ;;
    degraded) echo "sdxl degraded — restart: docker restart poindexter-sdxl-server"; exit 2 ;;
    *) echo "sdxl status=$status"; exit 2 ;;
  esac
'

check models "Voice bot (video generator) healthy" bash -c '
  resp=$(curl -s -m 3 http://localhost:9837/ 2>&1)
  echo "$resp" | grep -qE "Video Generation|video" || { echo "voice-bot not responding"; exit 2; }
'

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

section observability "Observability (Grafana / Prometheus / Alertmanager)"

check observability "Grafana UI responding" bash -c '
  resp=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://localhost:3000/api/health 2>&1)
  [ "$resp" = "200" ] || { echo "grafana returned $resp"; exit 2; }
'

check observability "Prometheus scraping worker" bash -c '
  resp=$(curl -s -m 5 "http://localhost:9090/api/v1/query?query=up{job=\"poindexter-worker\"}" 2>&1)
  val=$(echo "$resp" | python3 -c "import sys,json; r=json.load(sys.stdin).get(\"data\",{}).get(\"result\",[]); print(r[0][\"value\"][1] if r else \"\")" 2>/dev/null)
  [ "$val" = "1" ] || { echo "prometheus up{worker}=$val — scrape broken"; exit 2; }
'

check observability "Alertmanager routing to worker webhook" bash -c '
  resp=$(curl -s -m 5 http://localhost:9093/api/v2/status 2>&1)
  echo "$resp" | grep -q "poindexter-webhook" || { echo "alertmanager receiver not configured"; exit 2; }
'

check observability "QA Observability dashboard provisioned" bash -c '
  # Reads file presence rather than hitting Grafana API (no admin password exposure)
  docker exec poindexter-grafana sh -c "test -f /etc/grafana/dashboards/qa-observability.json" || { echo "dashboard not mounted"; exit 2; }
'

# ---------------------------------------------------------------------------
# Alerts (direct-to-API fan-out)
# ---------------------------------------------------------------------------

section alerts "Alerts (Discord + Telegram)"

check alerts "Discord webhook configured" bash -c '
  url=$(psql_exec "SELECT value FROM app_settings WHERE key=\"discord_ops_webhook_url\";")
  [ -n "$url" ] && [ "$url" != "" ] || { echo "discord_ops_webhook_url not set"; exit 2; }
  echo "$url" | grep -q "^https" || { echo "discord webhook malformed"; exit 2; }
'

check alerts "Telegram bot token decrypts" bash -c '
  docker exec -i poindexter-worker python <<PYEOF 2>&1
import asyncio, asyncpg, os
async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    from services.site_config import SiteConfig
    sc = SiteConfig()
    await sc.load(pool)
    tok = await sc.get_secret("telegram_bot_token")
    chat = sc.get("telegram_chat_id", "")
    await pool.close()
    if tok and len(tok) > 30 and ":" in tok and chat:
        print(f"decrypted_len={len(tok)} chat_id={chat}")
        return 0
    print("telegram secret decrypt failed or values empty")
    import sys; sys.exit(2)
asyncio.run(main())
PYEOF
'

check alerts "Alert policy: Telegram critical-only" bash -c '
  enabled=$(psql_exec "SELECT value FROM app_settings WHERE key=\"telegram_alerts_enabled\";")
  if [ "$enabled" = "true" ]; then
    echo "telegram_alerts_enabled=true — all alerts go to Telegram (flood risk)"
    exit 1
  fi
  echo "telegram_alerts_enabled=false (critical only)"
'

# ---------------------------------------------------------------------------
# Pipeline (content generation)
# ---------------------------------------------------------------------------

section pipeline "Content pipeline"

check pipeline "25 scheduled jobs registered" bash -c '
  count=$(docker logs poindexter-worker --since 30m 2>&1 | grep -oE "PluginScheduler started with [0-9]+ jobs" | tail -1 | grep -oE "[0-9]+")
  count=${count:-0}
  if [ "$count" -lt 20 ]; then echo "only $count jobs registered — check plugins/registry.py"; exit 2; fi
  if [ "$count" -lt 25 ]; then echo "$count jobs (expected 25)"; exit 1; fi
  echo "$count jobs registered"
'

check pipeline "Recent approval rate (24h)" bash -c '
  approved=$(psql_exec "SELECT COUNT(*) FROM content_tasks WHERE status IN (\"awaiting_approval\",\"approved\",\"published\") AND created_at > NOW() - INTERVAL \"24 hours\";")
  rejected=$(psql_exec "SELECT COUNT(*) FROM content_tasks WHERE status=\"rejected\" AND created_at > NOW() - INTERVAL \"24 hours\";")
  approved=${approved:-0}; rejected=${rejected:-0}
  total=$((approved + rejected))
  if [ "$total" = "0" ]; then echo "no completed tasks in 24h"; exit 1; fi
  rate=$((approved * 100 / total))
  if [ "$rate" -lt 20 ]; then echo "approval rate ${rate}% ($approved/$total) — check docs/experiments/pipeline-tuning.md"; exit 1; fi
  echo "approval rate ${rate}% ($approved/$total)"
'

check pipeline "No stuck in_progress tasks" bash -c '
  count=$(psql_exec "SELECT COUNT(*) FROM content_tasks WHERE status=\"in_progress\" AND started_at < NOW() - INTERVAL \"30 minutes\";")
  count=${count:-0}
  if [ "$count" -gt 0 ]; then echo "$count tasks stuck in_progress >30min — see troubleshooting.md"; exit 1; fi
  echo "no stuck tasks"
'

# ---------------------------------------------------------------------------
# Publishing (site + media)
# ---------------------------------------------------------------------------

section publish "Publishing (site + media)"

check publish "Static export to R2 fresh" bash -c '
  status=$(curl -s -o /dev/null -w "%{http_code}" https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static/posts/index.json)
  [ "$status" = "200" ] || { echo "posts index HTTP $status — run poindexter rebuild-export"; exit 2; }
'

check publish "Public site returns 200" bash -c '
  status=$(curl -s -o /dev/null -w "%{http_code}" https://www.gladlabs.io/)
  [ "$status" = "200" ] || { echo "gladlabs.io HTTP $status"; exit 2; }
'

check publish "Recent published post exists" bash -c '
  last=$(psql_exec "SELECT slug FROM posts WHERE status=\"published\" ORDER BY published_at DESC LIMIT 1;")
  last_date=$(psql_exec "SELECT published_at::date FROM posts WHERE status=\"published\" ORDER BY published_at DESC LIMIT 1;")
  [ -n "$last" ] || { echo "no published posts"; exit 2; }
  age_days=$(python3 -c "from datetime import date; print((date.today() - date.fromisoformat(\"$last_date\")).days)")
  if [ "$age_days" -gt 7 ]; then echo "latest post $age_days days old: $last"; exit 1; fi
  echo "latest $last (${age_days}d old)"
'

check publish "Recent podcast mp3 exists" bash -c '
  count=$(docker exec poindexter-worker sh -c "ls -1 /root/.poindexter/podcast/*.mp3 2>/dev/null | wc -l")
  count=${count:-0}
  if [ "$count" = "0" ]; then echo "no podcast mp3s — backfill_podcasts may be broken"; exit 1; fi
  echo "$count podcast mp3s in /root/.poindexter/podcast/"
'

check publish "Recent video mp4 exists" bash -c '
  count=$(docker exec poindexter-worker sh -c "ls -1 /root/.poindexter/video/*.mp4 2>/dev/null | wc -l")
  count=${count:-0}
  if [ "$count" = "0" ]; then echo "no video mp4s — backfill_videos may be broken"; exit 1; fi
  echo "$count video mp4s"
'

# ---------------------------------------------------------------------------
# Secrets (encrypted app_settings)
# ---------------------------------------------------------------------------

section secrets "Secrets & encryption"

check secrets "Encrypted secrets decrypt correctly" bash -c '
  docker exec -i poindexter-worker python <<PYEOF 2>&1
import asyncio, asyncpg, os
async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    from services.site_config import SiteConfig
    sc = SiteConfig()
    await sc.load(pool)
    keys = ["revalidate_secret", "telegram_bot_token", "pexels_api_key", "resend_api_key"]
    broken = []
    for k in keys:
        val = await sc.get_secret(k)
        if val is None or val == "" or str(val).startswith("enc:v1:"):
            broken.append(k)
    await pool.close()
    if broken:
        print(f"failed to decrypt: {broken} — check plugins/secrets + pgcrypto key")
        import sys; sys.exit(2)
    print(f"decrypted {len(keys)}/{len(keys)} test keys")
asyncio.run(main())
PYEOF
'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
echo "${C_BOLD}=== Summary ===${C_RESET}"
printf "  %sPASS%s: %d\n" "$C_GREEN" "$C_RESET" "$PASS"
printf "  %sWARN%s: %d\n" "$C_YELLOW" "$C_RESET" "$WARN"
printf "  %sFAIL%s: %d\n" "$C_RED" "$C_RESET" "$FAIL"
echo "  Total: $TOTAL"

if [ "$FAIL" -gt 0 ]; then
  echo "${C_RED}System has failures. See checks marked FAIL above.${C_RESET}"
  exit 2
fi
if [ "$WARN" -gt 0 ]; then
  echo "${C_YELLOW}System is operational with warnings. See checks marked WARN above.${C_RESET}"
  exit 1
fi
echo "${C_GREEN}All systems operational.${C_RESET}"
exit 0
