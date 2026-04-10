#!/bin/bash
# scripts/run.sh — List content tasks with optional status filter

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY in your env)"
  exit 1
fi

STATUS="$1"
LIMIT="${2:-20}"

QUERY="limit=${LIMIT}"
if [ -n "$STATUS" ]; then
  QUERY="${QUERY}&status=${STATUS}"
fi

echo "Fetching tasks (status=${STATUS:-all}, limit=${LIMIT})..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/tasks?${QUERY}" \
  -H "Authorization: Bearer ${POINDEXTER_KEY}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  TOTAL=$(echo "$BODY" | python -c "import sys,json; d=json.load(sys.stdin); v=d.get('total'); print(v if v is not None else '')" 2>/dev/null)

  if [ -n "$TOTAL" ]; then
    echo "Total tasks: $TOTAL"
    echo ""
  fi

  echo "$BODY" | python -c "
import sys,json
d=json.load(sys.stdin)
tasks=d.get('tasks',[])
for t in tasks:
    print(json.dumps({k: t.get(k) for k in ('id','task_name','status','topic','created_at')}, indent=2))
" 2>/dev/null || echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
