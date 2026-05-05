#!/bin/bash
# scripts/run.sh — Create a new blog post task

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"

# OAuth helper (Glad-Labs/poindexter#246).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "${SCRIPT_DIR}/../../_lib/get_token.sh"
POINDEXTER_TOKEN="$(get_poindexter_token)" || exit 1

TOPIC="$1"
CATEGORY="${2:-general}"
TARGET_AUDIENCE="${3:-general}"
PRIMARY_KEYWORD="${4:-}"

if [ -z "$TOPIC" ]; then
  echo "Error: topic is required"
  echo "Usage: run.sh \"topic\" [category] [target_audience] [primary_keyword]"
  exit 1
fi

echo "Creating task for topic: $TOPIC"

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/tasks" \
  -H "Authorization: Bearer ${POINDEXTER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"task_name\":\"Blog post: ${TOPIC}\",\"topic\":\"${TOPIC}\",\"category\":\"${CATEGORY}\",\"target_audience\":\"${TARGET_AUDIENCE}\",\"primary_keyword\":\"${PRIMARY_KEYWORD}\"}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "Task created successfully!"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
