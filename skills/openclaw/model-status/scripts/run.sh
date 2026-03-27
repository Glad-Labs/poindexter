#!/bin/bash
# scripts/run.sh — Check AI model health and availability

FASTAPI_URL="${FASTAPI_URL:-http://localhost:8000}"
API_TOKEN="${API_TOKEN}"

if [ -z "$API_TOKEN" ]; then
  echo "Error: API_TOKEN not configured"
  exit 1
fi

echo "Checking model health..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${FASTAPI_URL}/api/models/health" \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
  echo "=== Model Status ==="
  echo ""
  echo "$BODY" | jq .
else
  echo "Error: API returned HTTP $HTTP_CODE"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
  exit 1
fi
