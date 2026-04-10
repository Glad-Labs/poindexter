#!/bin/bash
# scripts/run.sh — Fetch completed tasks and display quality scores

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY in your env)"
  exit 1
fi

LIMIT="${1:-10}"

echo "Fetching quality report (last $LIMIT completed tasks)..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/tasks?status=completed&limit=${LIMIT}" \
  -H "Authorization: Bearer ${POINDEXTER_KEY}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "=== Quality Report ==="
  echo ""

  TOTAL=$(echo "$BODY" | python -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo "0")
  echo "Completed tasks: $TOTAL"
  echo ""

  echo "$BODY" | python -c "
import sys,json
d=json.load(sys.stdin)
for t in d.get('tasks',[]):
    meta = t.get('metadata') or {}
    print(json.dumps({
        'id': t.get('id'),
        'task_name': t.get('task_name'),
        'topic': t.get('topic'),
        'quality_score': t.get('quality_score') or meta.get('quality_score','N/A'),
        'completed_at': t.get('completed_at') or t.get('updated_at','N/A')
    }, indent=2))
" 2>/dev/null || echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
