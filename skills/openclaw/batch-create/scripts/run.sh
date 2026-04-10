#!/bin/bash
# scripts/run.sh — Bulk create content tasks

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY in your env)"
  exit 1
fi

if [ $# -eq 0 ]; then
  echo "Error: at least one topic is required"
  echo "Usage: run.sh \"topic1\" \"topic2\" \"topic3\" ..."
  exit 1
fi

# Build JSON array of tasks from arguments using Python
TASKS=$(python -c "
import json, sys
tasks = []
for topic in sys.argv[1:]:
    tasks.append({
        'task_name': f'Blog post: {topic}',
        'topic': topic,
        'category': 'general',
        'target_audience': 'general'
    })
print(json.dumps(tasks))
" "$@")

PAYLOAD=$(python -c "
import json, sys
tasks = json.loads(sys.argv[1])
print(json.dumps({'tasks': tasks}))
" "$TASKS")

TASK_COUNT=$(python -c "import json,sys; print(len(json.loads(sys.argv[1])))" "$TASKS")
echo "Creating $TASK_COUNT tasks..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/tasks/bulk/create" \
  -H "Authorization: Bearer ${POINDEXTER_KEY}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
