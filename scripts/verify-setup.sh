#!/usr/bin/env bash
#
# Verify Setup — validates that Poindexter is properly configured.
#
# Usage:
#   bash scripts/verify-setup.sh
#
# Checks:
#   1. Docker containers running and healthy
#   2. PostgreSQL accessible with correct schema
#   3. Required app_settings populated
#   4. Ollama reachable with at least one model
#   5. SDXL server responding (if GPU available)
#   6. Worker connected and processing
#   7. API endpoints responding
#
# Exit code: 0 = all pass, 1 = failures found

set -uo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

pass() { echo -e "  ${GREEN}[PASS]${NC} $1"; ((PASS++)); }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; ((FAIL++)); }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; ((WARN++)); }
info() { echo -e "${BLUE}$1${NC}"; }

# Source env if available
if [ -f .env.local ]; then
    set -a; source .env.local 2>/dev/null || true; set +a
fi

# ============================================================
echo ""
info "=== Poindexter — Setup Verification ==="
echo ""

# ============================================================
info "1. Docker Containers"
# ============================================================

REQUIRED_CONTAINERS="poindexter-postgres-local poindexter-worker poindexter-brain-daemon poindexter-grafana poindexter-prometheus"
# Optional containers — pgadmin renames to poindexter-*; gitea/woodpecker/headscale
# stay with the legacy gladlabs-* prefix because they're internal-only.
OPTIONAL_CONTAINERS="gladlabs-woodpecker gladlabs-woodpecker-agent gladlabs-gitea gladlabs-headscale poindexter-pgadmin"

for c in $REQUIRED_CONTAINERS; do
    running=$(docker inspect --format='{{.State.Status}}' "$c" 2>/dev/null || echo "not found")
    if [ "$running" != "running" ]; then
        fail "$c: $running — run: docker compose -f docker-compose.local.yml up -d"
        continue
    fi
    health=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$c" 2>/dev/null || echo "unknown")
    if [ "$health" = "healthy" ]; then
        pass "$c: healthy"
    elif [ "$health" = "no-healthcheck" ]; then
        pass "$c: running"
    else
        warn "$c: running but health=$health — check: docker logs $c"
    fi
done

for c in $OPTIONAL_CONTAINERS; do
    status=$(docker inspect --format='{{.State.Status}}' "$c" 2>/dev/null || echo "not running")
    if [ "$status" = "running" ]; then
        pass "$c: running (optional)"
    else
        warn "$c: not running (optional — system works without it)"
    fi
done

# ============================================================
echo ""
info "2. PostgreSQL"
# ============================================================

PG_USER="${LOCAL_POSTGRES_USER:-poindexter}"
PG_PASS="${LOCAL_POSTGRES_PASSWORD:-poindexter-brain-local}"
PG_DB="${LOCAL_POSTGRES_DB:-poindexter_brain}"
PG_HOST="localhost"
PG_PORT="5433"

if PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
    pass "PostgreSQL connection: OK"
else
    fail "PostgreSQL connection failed — check LOCAL_POSTGRES_PASSWORD in .env.local"
fi

# Check app_settings table
SETTINGS_COUNT=$(PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -t -c "SELECT COUNT(*) FROM app_settings;" 2>/dev/null | tr -d ' ')
if [ -n "$SETTINGS_COUNT" ] && [ "$SETTINGS_COUNT" -gt 100 ]; then
    pass "app_settings: $SETTINGS_COUNT keys configured"
elif [ -n "$SETTINGS_COUNT" ] && [ "$SETTINGS_COUNT" -gt 0 ]; then
    warn "app_settings: only $SETTINGS_COUNT keys — run: psql -f scripts/seed-database.sql"
else
    fail "app_settings: table missing or empty — run: psql -f scripts/seed-database.sql"
fi

# Check critical settings
CRITICAL_KEYS="site_name site_url ollama_base_url"
for key in $CRITICAL_KEYS; do
    val=$(PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -t -c "SELECT value FROM app_settings WHERE key = '$key';" 2>/dev/null | tr -d ' ')
    if [ -n "$val" ] && [ "$val" != "" ]; then
        pass "app_settings.$key: configured"
    else
        fail "app_settings.$key: not set — update via: UPDATE app_settings SET value='...' WHERE key='$key';"
    fi
done

# Check content_tasks table exists
if PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1 FROM content_tasks LIMIT 0;" >/dev/null 2>&1; then
    pass "content_tasks table: exists"
else
    fail "content_tasks table: missing — migrations may not have run"
fi

# ============================================================
echo ""
info "3. Ollama (Local LLM)"
# ============================================================

OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
if curl -s "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    pass "Ollama: reachable at $OLLAMA_URL"
    MODEL_COUNT=$(curl -s "$OLLAMA_URL/api/tags" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo "0")
    if [ "$MODEL_COUNT" -gt 0 ]; then
        pass "Ollama models: $MODEL_COUNT available"
    else
        fail "Ollama: no models pulled — run: ollama pull qwen3:8b gemma3:27b"
    fi

    # Check minimum required models
    MODELS=$(curl -s "$OLLAMA_URL/api/tags" | python3 -c "import json,sys; print(' '.join(m['name'] for m in json.load(sys.stdin).get('models',[])))" 2>/dev/null)
    for m in "qwen3:8b" "gemma3:27b" "nomic-embed-text"; do
        if echo "$MODELS" | grep -q "$m"; then
            pass "Model $m: available"
        else
            warn "Model $m: not found — run: ollama pull $m"
        fi
    done
else
    fail "Ollama: not reachable at $OLLAMA_URL — install from https://ollama.com and run: ollama serve"
fi

# ============================================================
echo ""
info "4. SDXL Server (Image Generation)"
# ============================================================

SDXL_URL="${SDXL_SERVER_URL:-http://localhost:9836}"
SDXL_HEALTH=$(curl -s "$SDXL_URL/health" 2>/dev/null)
if [ -n "$SDXL_HEALTH" ]; then
    GPU_NAME=$(echo "$SDXL_HEALTH" | python3 -c "import json,sys; print(json.load(sys.stdin).get('gpu','unknown'))" 2>/dev/null || echo "unknown")
    pass "SDXL server: running on $GPU_NAME"
else
    warn "SDXL server: not running at $SDXL_URL — AI image generation will be disabled (Pexels fallback used)"
fi

# ============================================================
echo ""
info "5. API / Worker"
# ============================================================

API_URL="${API_BASE_URL:-http://localhost:8002}"
API_HEALTH=$(curl -s "$API_URL/api/health" 2>/dev/null)
if [ -n "$API_HEALTH" ]; then
    WORKER_RUNNING=$(echo "$API_HEALTH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('worker',{}).get('running','unknown'))" 2>/dev/null || echo "unknown")
    if [ "$WORKER_RUNNING" = "True" ]; then
        pass "Worker API: healthy, worker running"
    else
        pass "Worker API: responding (worker status: $WORKER_RUNNING)"
    fi
else
    fail "Worker API: not responding at $API_URL/api/health"
fi

# ============================================================
echo ""
info "6. Grafana (Monitoring)"
# ============================================================

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
if curl -s "$GRAFANA_URL/api/health" >/dev/null 2>&1; then
    pass "Grafana: reachable at $GRAFANA_URL"
else
    warn "Grafana: not reachable — monitoring dashboards unavailable"
fi

# ============================================================
echo ""
info "=== RESULTS ==="
echo ""
echo -e "  ${GREEN}PASS: $PASS${NC}  ${RED}FAIL: $FAIL${NC}  ${YELLOW}WARN: $WARN${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}Setup has failures. Fix the FAIL items above before running the pipeline.${NC}"
    exit 1
else
    if [ "$WARN" -gt 0 ]; then
        echo -e "${YELLOW}Setup OK with warnings. The pipeline will work but some features may be limited.${NC}"
    else
        echo -e "${GREEN}All checks passed! Your system is ready. Create your first post:${NC}"
        echo ""
        echo "  curl -X POST $API_URL/api/tasks \\"
        echo "    -H \"Authorization: Bearer \$API_TOKEN\" \\"
        echo "    -H \"Content-Type: application/json\" \\"
        echo "    -d '{\"topic\": \"Your first AI-generated post\", \"task_type\": \"blog_post\"}'"
    fi
    exit 0
fi
