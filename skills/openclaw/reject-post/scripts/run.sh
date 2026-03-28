#!/bin/bash
# scripts/run.sh — Reject a content task

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
GLADLABS_KEY="${GLADLABS_KEY}"

if [ -z "$GLADLABS_KEY" ]; then
  echo "Error: GLADLABS_KEY not configured"
  exit 1
fi

TASK_ID="$1"
REASON="${2:-}"

if [ -z "$TASK_ID" ]; then
  echo "Error: task_id is required"
  echo "Usage: run.sh \"task_id\" [\"reason\"]"
  exit 1
fi

echo "Rejecting task: $TASK_ID"

if [ -n "$REASON" ]; then
  PAYLOAD=$(jq -n --arg reason "$REASON" '{reason: $reason}')
else
  PAYLOAD='{}'
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/tasks/${TASK_ID}/reject" \
  -H "Authorization: Bearer ${GLADLABS_KEY}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "Task $TASK_ID rejected."
  echo "$BODY" | jq .
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
  exit 1
fi
