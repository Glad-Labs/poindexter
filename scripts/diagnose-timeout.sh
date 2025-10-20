#!/bin/bash
# Timeout Diagnostics Script
# Usage: ./diagnose-timeout.sh

echo "🔍 Strapi API Diagnostics"
echo "=========================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

STRAPI_URL="${NEXT_PUBLIC_STRAPI_API_URL:-http://localhost:1337}"

echo "📡 Testing Strapi at: $STRAPI_URL"
echo ""

# Test 1: Check if server is reachable
echo "1️⃣  Connection Test..."
if timeout 5 curl -s -o /dev/null -w "%{http_code}" "$STRAPI_URL/api/health" > /tmp/status_code.txt 2>/dev/null; then
  STATUS=$(cat /tmp/status_code.txt)
  if [ "$STATUS" = "200" ]; then
    echo -e "${GREEN}✓ Strapi is reachable (HTTP $STATUS)${NC}"
  elif [ "$STATUS" = "404" ]; then
    echo -e "${YELLOW}⚠ Strapi reachable but endpoint not found (HTTP $STATUS)${NC}"
  else
    echo -e "${RED}✗ Strapi returned HTTP $STATUS${NC}"
  fi
else
  echo -e "${RED}✗ Connection timeout - Strapi not responding${NC}"
fi

echo ""

# Test 2: Check response time
echo "2️⃣  Response Time Test..."
START=$(date +%s%N)
RESPONSE=$(timeout 10 curl -s -o /dev/null -w "%{http_code}" "$STRAPI_URL/api/posts?pagination[limit]=1" 2>/dev/null)
END=$(date +%s%N)
ELAPSED=$(( (END - START) / 1000000 ))

if [ ! -z "$RESPONSE" ]; then
  if [ $ELAPSED -lt 1000 ]; then
    echo -e "${GREEN}✓ Response time: ${ELAPSED}ms (HTTP $RESPONSE)${NC}"
  elif [ $ELAPSED -lt 5000 ]; then
    echo -e "${YELLOW}⚠ Slow response: ${ELAPSED}ms (threshold: 5000ms)${NC}"
  else
    echo -e "${RED}✗ Very slow response: ${ELAPSED}ms${NC}"
  fi
else
  echo -e "${RED}✗ No response from API${NC}"
fi

echo ""

# Test 3: Check specific endpoints
echo "3️⃣  Endpoint Tests..."

ENDPOINTS=("/posts" "/categories" "/tags")

for ENDPOINT in "${ENDPOINTS[@]}"; do
  START=$(date +%s%N)
  STATUS=$(timeout 5 curl -s -o /dev/null -w "%{http_code}" "$STRAPI_URL/api${ENDPOINT}?pagination[limit]=1" 2>/dev/null)
  END=$(date +%s%N)
  ELAPSED=$(( (END - START) / 1000000 ))
  
  if [ "$STATUS" = "200" ]; then
    echo -e "${GREEN}✓ $ENDPOINT: ${ELAPSED}ms${NC}"
  else
    echo -e "${RED}✗ $ENDPOINT: HTTP $STATUS (${ELAPSED}ms)${NC}"
  fi
done

echo ""
echo "📊 Summary"
echo "=========="
echo "If you see ✓ for all tests: Your Strapi is healthy"
echo "If you see ⚠ or ✗: Investigate Strapi logs and networking"
echo ""
echo "🔗 Next steps:"
echo "1. Check Railway status: https://railway.app"
echo "2. Check Strapi logs: railway logs"
echo "3. Restart Strapi if needed: railway restart"
echo "4. Check environment variables in Vercel dashboard"
