#!/bin/bash
# scripts/run.sh — View or update app_settings in the database

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

ACTION="$1"
ARG1="$2"
ARG2="$3"

case "$ACTION" in
  list)
    CATEGORY="$ARG1"
    if [ -n "$CATEGORY" ]; then
      echo "Settings for category: $CATEGORY"
      RESPONSE=$(curl -s "${FASTAPI_URL}/api/settings?category=${CATEGORY}" \
        -H "Authorization: Bearer ${POINDEXTER_KEY}")
    else
      echo "All settings:"
      RESPONSE=$(curl -s "${FASTAPI_URL}/api/settings" \
        -H "Authorization: Bearer ${POINDEXTER_KEY}")
    fi
    echo "$RESPONSE" | python -c "
import sys,json
d=json.load(sys.stdin)
for s in d.get('settings',[]):
    print(f\"{s.get('category','')}/{s.get('key','')} = {s.get('value','')}\")
" 2>/dev/null || echo "$RESPONSE"
    ;;

  get)
    KEY="$ARG1"
    if [ -z "$KEY" ]; then
      echo "Error: key is required"
      echo "Usage: run.sh get <key>"
      exit 1
    fi
    RESPONSE=$(curl -s "${FASTAPI_URL}/api/settings/${KEY}" \
      -H "Authorization: Bearer ${POINDEXTER_KEY}")
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
    ;;

  set)
    KEY="$ARG1"
    VALUE="$ARG2"
    if [ -z "$KEY" ] || [ -z "$VALUE" ]; then
      echo "Error: key and value are required"
      echo "Usage: run.sh set <key> <value>"
      exit 1
    fi
    PAYLOAD=$(python -c "import json,sys; print(json.dumps({'key': sys.argv[1], 'value': sys.argv[2]}))" "$KEY" "$VALUE")
    RESPONSE=$(curl -s -X PUT "${FASTAPI_URL}/api/settings/${KEY}" \
      -H "Authorization: Bearer ${POINDEXTER_KEY}" \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD")
    echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
    ;;

  *)
    echo "Usage: run.sh <list|get|set> [args]"
    echo ""
    echo "  list [category]    List all settings or filter by category"
    echo "  get <key>          Get a specific setting"
    echo "  set <key> <value>  Update a setting"
    echo ""
    echo "Categories: api_keys, pipeline, auth, features, cors, webhooks"
    exit 1
    ;;
esac
