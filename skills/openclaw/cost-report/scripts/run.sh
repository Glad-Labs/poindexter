#!/bin/bash
# scripts/run.sh — Cost + budget + operational metrics for the Glad Labs pipeline.
#
# Hits two API endpoints:
#   GET /api/metrics/costs/budget    — money spent vs limits
#   GET /api/metrics/operational     — task counts and worker state
#
# The old /api/metrics root path was removed in the 2026-04 pipeline refactor.
# If you see a 404 on either of the new paths, confirm the worker container is
# running and that the routes are registered (routes/metrics_routes.py).

set -euo pipefail

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY:-}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY or GLADLABS_KEY in your env)" >&2
  exit 1
fi

MODE="${1:-all}"

fetch() {
  local path="$1"
  curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}${path}" \
    -H "Authorization: Bearer ${POINDEXTER_KEY}" \
    -H "Content-Type: application/json"
}

print_section() {
  local title="$1"
  local body="$2"
  echo "=== $title ==="
  echo "$body" | python -m json.tool 2>/dev/null || echo "$body"
  echo ""
}

show_budget() {
  local resp http body
  resp=$(fetch "/api/metrics/costs/budget")
  http=$(echo "$resp" | tail -1)
  body=$(echo "$resp" | sed '$d')
  if [ "$http" -ge 200 ] && [ "$http" -lt 300 ]; then
    print_section "Budget" "$body"
  else
    echo "Error fetching /api/metrics/costs/budget: HTTP $http" >&2
    echo "$body" >&2
    return 1
  fi
}

show_operational() {
  local resp http body
  resp=$(fetch "/api/metrics/operational")
  http=$(echo "$resp" | tail -1)
  body=$(echo "$resp" | sed '$d')
  if [ "$http" -ge 200 ] && [ "$http" -lt 300 ]; then
    print_section "Operational Metrics" "$body"
  else
    echo "Error fetching /api/metrics/operational: HTTP $http" >&2
    echo "$body" >&2
    return 1
  fi
}

case "$MODE" in
  budget)
    show_budget
    ;;
  operational)
    show_operational
    ;;
  all|"")
    show_budget
    show_operational
    ;;
  *)
    echo "Usage: run.sh [budget|operational|all]" >&2
    exit 1
    ;;
esac
