#!/bin/bash
ACTION="${1:-all}"
RAILWAY_URL="${RAILWAY_URL:-https://cofounder-staging.up.railway.app}"

case "$ACTION" in
  status)
    railway status 2>&1
    ;;
  logs)
    railway logs --tail 20 2>&1
    ;;
  health)
    curl -s "${RAILWAY_URL}/api/health" | jq .
    ;;
  all|*)
    echo "=== Deployment Status ==="
    railway status 2>&1
    echo ""
    echo "=== Health Check ==="
    curl -s "${RAILWAY_URL}/api/health" | jq . 2>/dev/null || echo "Health check failed"
    echo ""
    echo "=== Recent Logs (last 10) ==="
    railway logs --tail 10 2>&1
    ;;
esac
