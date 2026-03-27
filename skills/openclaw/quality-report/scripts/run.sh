#!/bin/bash
# scripts/run.sh — Fetch completed tasks and display quality scores

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN}"

if [ -z "$API_TOKEN" ]; then
  echo "Error: API_TOKEN not configured"
  exit 1
fi

LIMIT="${1:-10}"

echo "Fetching quality report (last $LIMIT completed tasks)..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/tasks?status=completed&limit=${LIMIT}" \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "=== Quality Report ==="
  echo ""

  TOTAL=$(echo "$BODY" | jq '.total // 0')
  echo "Completed tasks: $TOTAL"
  echo ""

  echo "$BODY" | jq '.tasks[]? | {
    id,
    task_name,
    topic,
    quality_score: (.quality_score // .metadata.quality_score // "N/A"),
    completed_at: (.completed_at // .updated_at // "N/A")
  }' 2>/dev/null || echo "$BODY" | jq .
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
  exit 1
fi
