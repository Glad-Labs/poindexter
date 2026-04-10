#!/bin/bash
# scripts/run.sh — Create a new blog post task

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY in your env)"
  exit 1
fi

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
  -H "Authorization: Bearer ${POINDEXTER_KEY}" \
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
