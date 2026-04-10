#!/bin/bash
# scripts/run.sh — Reject a content task

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY in your env)"
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
  PAYLOAD=$(python -c "import json,sys; print(json.dumps({'reason': sys.argv[1]}))" "$REASON")
else
  PAYLOAD='{}'
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/tasks/${TASK_ID}/reject" \
  -H "Authorization: Bearer ${POINDEXTER_KEY}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "Task $TASK_ID rejected."
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
