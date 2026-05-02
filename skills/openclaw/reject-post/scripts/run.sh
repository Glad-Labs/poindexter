#!/bin/bash
# scripts/run.sh — Reject a content task

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"

# OAuth helper (Glad-Labs/poindexter#246).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "${SCRIPT_DIR}/../../_lib/get_token.sh"
POINDEXTER_TOKEN="$(get_poindexter_token)" || exit 1

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
  -H "Authorization: Bearer ${POINDEXTER_TOKEN}" \
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
