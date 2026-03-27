#!/bin/bash
# scripts/run.sh — Fetch and display cost/spending metrics

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN}"

if [ -z "$API_TOKEN" ]; then
  echo "Error: API_TOKEN not configured"
  exit 1
fi

echo "Fetching cost metrics..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/metrics" \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "=== Cost Report ==="
  echo ""

  # Extract cost-related fields if present
  TOTAL_COST=$(echo "$BODY" | jq -r '.total_cost // .cost_summary.total // "N/A"')
  echo "Total Cost: $TOTAL_COST"

  # Show per-model breakdown if available
  echo ""
  echo "--- Per-Model Breakdown ---"
  echo "$BODY" | jq '.cost_by_model // .model_costs // empty' 2>/dev/null

  # Show full response as fallback
  echo ""
  echo "--- Full Metrics ---"
  echo "$BODY" | jq .
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
  exit 1
fi
