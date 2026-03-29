#!/bin/bash
# scripts/run.sh — Check AI model health and availability

FASTAPI_URL="${FASTAPI_URL:-https://cofounder-production.up.railway.app}"
GLADLABS_KEY="${GLADLABS_KEY}"

if [ -z "$GLADLABS_KEY" ]; then
  echo "Error: GLADLABS_KEY not configured"
  exit 1
fi

echo "Checking model health..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/models/health" \
  -H "Authorization: Bearer ${GLADLABS_KEY}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "=== Model Status ==="
  echo ""
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | python -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
