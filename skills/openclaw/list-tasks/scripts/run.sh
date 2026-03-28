#!/bin/bash
# scripts/run.sh — List content tasks with optional status filter

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
GLADLABS_KEY="${GLADLABS_KEY}"

if [ -z "$GLADLABS_KEY" ]; then
  echo "Error: GLADLABS_KEY not configured"
  exit 1
fi

STATUS="$1"
LIMIT="${2:-20}"

QUERY="limit=${LIMIT}"
if [ -n "$STATUS" ]; then
  QUERY="${QUERY}&status=${STATUS}"
fi

echo "Fetching tasks (status=${STATUS:-all}, limit=${LIMIT})..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/tasks?${QUERY}" \
  -H "Authorization: Bearer ${GLADLABS_KEY}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  TOTAL=$(echo "$BODY" | jq '.total // empty')
  TASKS=$(echo "$BODY" | jq '.tasks // empty')

  if [ -n "$TOTAL" ]; then
    echo "Total tasks: $TOTAL"
    echo ""
  fi

  echo "$BODY" | jq '.tasks[]? | {id, task_name, status, topic, created_at}' 2>/dev/null || echo "$BODY" | jq .
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
  exit 1
fi
