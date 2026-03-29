#!/bin/bash
# scripts/run.sh — View or update app_settings in the database

FASTAPI_URL="${FASTAPI_URL:-https://cofounder-production.up.railway.app}"
GLADLABS_KEY="${GLADLABS_KEY}"

ACTION="$1"
ARG1="$2"
ARG2="$3"

case "$ACTION" in
  list)
    CATEGORY="$ARG1"
    if [ -n "$CATEGORY" ]; then
      echo "Settings for category: $CATEGORY"
      RESPONSE=$(curl -s "${FASTAPI_URL}/api/settings?category=${CATEGORY}" \
        -H "Authorization: Bearer ${GLADLABS_KEY}")
    else
      echo "All settings:"
      RESPONSE=$(curl -s "${FASTAPI_URL}/api/settings" \
        -H "Authorization: Bearer ${GLADLABS_KEY}")
    fi
    echo "$RESPONSE" | jq -r '.settings[] | "\(.category)/\(.key) = \(.value)"' 2>/dev/null || echo "$RESPONSE"
    ;;

  get)
    KEY="$ARG1"
    if [ -z "$KEY" ]; then
      echo "Error: key is required"
      echo "Usage: run.sh get <key>"
      exit 1
    fi
    RESPONSE=$(curl -s "${FASTAPI_URL}/api/settings/${KEY}" \
      -H "Authorization: Bearer ${GLADLABS_KEY}")
    echo "$RESPONSE" | jq . 2>/dev/null || echo "$RESPONSE"
    ;;

  set)
    KEY="$ARG1"
    VALUE="$ARG2"
    if [ -z "$KEY" ] || [ -z "$VALUE" ]; then
      echo "Error: key and value are required"
      echo "Usage: run.sh set <key> <value>"
      exit 1
    fi
    PAYLOAD=$(jq -n --arg key "$KEY" --arg value "$VALUE" '{key: $key, value: $value}')
    RESPONSE=$(curl -s -X PUT "${FASTAPI_URL}/api/settings/${KEY}" \
      -H "Authorization: Bearer ${GLADLABS_KEY}" \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD")
    echo "$RESPONSE" | jq . 2>/dev/null || echo "$RESPONSE"
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
