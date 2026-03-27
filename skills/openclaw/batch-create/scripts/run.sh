#!/bin/bash
# scripts/run.sh — Bulk create content tasks

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN}"

if [ -z "$API_TOKEN" ]; then
  echo "Error: API_TOKEN not configured"
  exit 1
fi

if [ $# -eq 0 ]; then
  echo "Error: at least one topic is required"
  echo "Usage: run.sh \"topic1\" \"topic2\" \"topic3\" ..."
  exit 1
fi

# Build JSON array of tasks from arguments
TASKS="[]"
for TOPIC in "$@"; do
  TASKS=$(echo "$TASKS" | jq \
    --arg task_name "Blog post: $TOPIC" \
    --arg topic "$TOPIC" \
    '. + [{task_name: $task_name, topic: $topic, category: "general", target_audience: "general"}]')
done

PAYLOAD=$(echo "$TASKS" | jq '{tasks: .}')

echo "Creating $(echo "$TASKS" | jq length) tasks..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/tasks/bulk/create" \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "$BODY" | jq .
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
  exit 1
fi
