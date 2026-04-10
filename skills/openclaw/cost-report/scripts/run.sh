#!/bin/bash
# scripts/run.sh — Fetch and display cost/spending metrics

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY in your env)"
  exit 1
fi

echo "Fetching cost metrics..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/metrics" \
  -H "Authorization: Bearer ${POINDEXTER_KEY}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "=== Cost Report ==="
  echo ""

  # Extract cost-related fields if present
  TOTAL_COST=$(echo "$BODY" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_cost') or d.get('cost_summary',{}).get('total','N/A'))" 2>/dev/null || echo "N/A")
  echo "Total Cost: $TOTAL_COST"

  # Show per-model breakdown if available
  echo ""
  echo "--- Per-Model Breakdown ---"
  echo "$BODY" | python -c "
import sys,json
d=json.load(sys.stdin)
costs = d.get('cost_by_model') or d.get('model_costs')
if costs:
    print(json.dumps(costs, indent=2))
" 2>/dev/null

  # Show full response as fallback
  echo ""
  echo "--- Full Metrics ---"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
