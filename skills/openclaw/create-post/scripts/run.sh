#!/bin/bash
# scripts/run.sh — Create a new blog post task

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN}"

if [ -z "$API_TOKEN" ]; then
  echo "Error: API_TOKEN not configured"
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

PAYLOAD=$(jq -n \
  --arg task_name "Blog post: $TOPIC" \
  --arg topic "$TOPIC" \
  --arg category "$CATEGORY" \
  --arg target_audience "$TARGET_AUDIENCE" \
  --arg primary_keyword "$PRIMARY_KEYWORD" \
  '{
    task_name: $task_name,
    topic: $topic,
    category: $category,
    target_audience: $target_audience,
    primary_keyword: $primary_keyword
  }')

echo "Creating task for topic: $TOPIC"

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/tasks" \
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
