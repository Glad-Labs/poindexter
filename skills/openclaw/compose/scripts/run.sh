#!/bin/bash
# Process Composer skill — plan, approve, execute business processes

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"
ACTION="$1"
INTENT="$2"

case "$ACTION" in
  plan)
    if [ -z "$INTENT" ]; then
      echo "Usage: run.sh plan \"your intent here\""
      exit 1
    fi
    PAYLOAD=$(python -c "import json,sys; print(json.dumps({'intent': sys.argv[1]}))" "$INTENT")
    RESPONSE=$(curl -s -X POST "${FASTAPI_URL}/api/compose/plan" \
      -H "Authorization: Bearer ${POINDEXTER_KEY}" \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD")
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
    ;;

  execute)
    if [ -z "$INTENT" ]; then
      echo "Usage: run.sh execute \"your intent here\""
      exit 1
    fi
    PAYLOAD=$(python -c "import json,sys; print(json.dumps({'intent': sys.argv[1]}))" "$INTENT")
    RESPONSE=$(curl -s -X POST "${FASTAPI_URL}/api/compose/execute" \
      -H "Authorization: Bearer ${POINDEXTER_KEY}" \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD")
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
    ;;

  approve)
    PLAN_ID="$INTENT"
    if [ -z "$PLAN_ID" ]; then
      echo "Usage: run.sh approve <plan_id>"
      exit 1
    fi
    RESPONSE=$(curl -s -X POST "${FASTAPI_URL}/api/compose/approve/${PLAN_ID}" \
      -H "Authorization: Bearer ${POINDEXTER_KEY}" \
      -H "Content-Type: application/json" \
      -d '{"approve": true}')
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
    ;;

  reject)
    PLAN_ID="$INTENT"
    REASON="${3:-}"
    if [ -z "$PLAN_ID" ]; then
      echo "Usage: run.sh reject <plan_id> [reason]"
      exit 1
    fi
    PAYLOAD=$(python -c "import json,sys; print(json.dumps({'approve': False, 'reason': sys.argv[1] if len(sys.argv) > 1 else ''}))" "$REASON")
    RESPONSE=$(curl -s -X POST "${FASTAPI_URL}/api/compose/approve/${PLAN_ID}" \
      -H "Authorization: Bearer ${POINDEXTER_KEY}" \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD")
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
    ;;

  steps)
    RESPONSE=$(curl -s "${FASTAPI_URL}/api/compose/steps" \
      -H "Authorization: Bearer ${POINDEXTER_KEY}")
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
    ;;

  *)
    echo "Process Composer"
    echo ""
    echo "Usage: run.sh <plan|execute|approve|reject|steps> [args]"
    echo ""
    echo "  plan \"intent\"        — Propose a plan for review"
    echo "  execute \"intent\"     — Execute immediately"
    echo "  approve <plan_id>    — Approve a pending plan"
    echo "  reject <plan_id>     — Reject a pending plan"
    echo "  steps                — List available building blocks"
    exit 1
    ;;
esac
