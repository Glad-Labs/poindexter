#!/bin/bash
# scripts/run.sh — View or update site settings

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
GLADLABS_KEY="${GLADLABS_KEY}"

if [ -z "$GLADLABS_KEY" ]; then
  echo "Error: GLADLABS_KEY not configured"
  exit 1
fi

SETTING_KEY="$1"
SETTING_VALUE="$2"

if [ -z "$SETTING_KEY" ]; then
  # GET all settings
  echo "Fetching current settings..."

  RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/settings" \
    -H "Authorization: Bearer ${GLADLABS_KEY}" \
    -H "Content-Type: application/json")

  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')

  if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    echo "=== Current Settings ==="
    echo "$BODY" | jq .
  else
    echo "Error: API returned HTTP $HTTP_CODE"
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
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

  PAYLOAD=$(jq -n --arg key "$SETTING_KEY" --arg value "$SETTING_VALUE" '{($key): $value}')

  RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${FASTAPI_URL}/api/settings" \
    -H "Authorization: Bearer ${GLADLABS_KEY}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')

  if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    echo "Setting updated successfully."
    echo "$BODY" | jq .
  else
    echo "Error: API returned HTTP $HTTP_CODE"
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    exit 1
  fi
fi
