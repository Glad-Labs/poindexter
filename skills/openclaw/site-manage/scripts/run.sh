#!/bin/bash
# scripts/run.sh — View or update site settings

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8002}"
POINDEXTER_KEY="${POINDEXTER_KEY:-${GLADLABS_KEY}}"

if [ -z "$POINDEXTER_KEY" ]; then
  echo "Error: POINDEXTER_KEY not configured (set POINDEXTER_KEY in your env)"
  exit 1
fi

SETTING_KEY="$1"
SETTING_VALUE="$2"

if [ -z "$SETTING_KEY" ]; then
  # GET all settings
  echo "Fetching current settings..."

  RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/settings" \
    -H "Authorization: Bearer ${POINDEXTER_KEY}" \
    -H "Content-Type: application/json")

  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')

  if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    echo "=== Current Settings ==="
    echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  else
    echo "Error: API returned HTTP $HTTP_CODE"
    echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
    exit 1
  fi
else
  # POST to update a setting
  if [ -z "$SETTING_VALUE" ]; then
    echo "Error: setting_value is required when setting_key is provided"
    echo "Usage: run.sh [setting_key] [setting_value]"
    exit 1
  fi

  echo "Updating setting: $SETTING_KEY = $SETTING_VALUE"

  PAYLOAD=$(python -c "import json,sys; print(json.dumps({sys.argv[1]: sys.argv[2]}))" "$SETTING_KEY" "$SETTING_VALUE")

  RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/settings" \
    -H "Authorization: Bearer ${POINDEXTER_KEY}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')

  if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    echo "Setting updated successfully."
    echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  else
    echo "Error: API returned HTTP $HTTP_CODE"
    echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
    exit 1
  fi
fi
