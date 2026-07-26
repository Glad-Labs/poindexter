#!/bin/bash
# scripts/run.sh — Publish a content task

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"

# OAuth helper (Glad-Labs/poindexter#246).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "${SCRIPT_DIR}/../../_lib/get_token.sh"
POINDEXTER_TOKEN="$(get_poindexter_token)" || exit 1

TASK_ID="$1"

if [ -z "$TASK_ID" ]; then
  echo "Error: task_id is required"
  echo "Usage: run.sh \"task_id\""
  exit 1
fi

echo "Publishing task: $TASK_ID"

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/tasks/${TASK_ID}/publish" \
  -H "Authorization: Bearer ${POINDEXTER_TOKEN}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "Task $TASK_ID published successfully."
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
