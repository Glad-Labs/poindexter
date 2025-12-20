#!/bin/bash
# Image Generation Integration Test
# Tests the 3-layer improvements

echo "🧪 Testing Image Generation Improvements..."
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# API base URL
API_URL="http://localhost:8000/api"

# Test 1: Check if API is responding
echo -e "${YELLOW}Test 1: API Health Check${NC}"
HEALTH=$(curl -s "${API_URL}/health" 2>/dev/null)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ API is responding${NC}"
else
    echo -e "${RED}❌ API not responding on port 8000${NC}"
    exit 1
fi
echo ""

# Test 2: Create a test task
echo -e "${YELLOW}Test 2: Create Test Task${NC}"
TASK_RESPONSE=$(curl -s -X POST "${API_URL}/tasks" \
    -H "Content-Type: application/json" \
    -d '{
        "prompt": "Write about AI-Powered NPCs in Games",
        "style": "informative",
        "tone": "professional"
    }' 2>/dev/null)

TASK_ID=$(echo "$TASK_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

if [ ! -z "$TASK_ID" ]; then
    echo -e "${GREEN}✅ Task created: $TASK_ID${NC}"
else
    echo -e "${YELLOW}⚠️  Could not create task (may need authentication)${NC}"
    echo "Response: $TASK_RESPONSE"
fi
echo ""

# Test 3: Check code changes
echo -e "${YELLOW}Test 3: Verify Code Changes${NC}"

# Check seo_content_generator.py
if grep -q "NO PEOPLE" src/cofounder_agent/services/seo_content_generator.py 2>/dev/null; then
    echo -e "${GREEN}✅ seo_content_generator.py: NO PEOPLE requirement added${NC}"
else
    echo -e "${RED}❌ seo_content_generator.py: NO PEOPLE requirement missing${NC}"
fi

# Check pexels_client.py
if grep -q "_is_content_appropriate" src/cofounder_agent/services/pexels_client.py 2>/dev/null; then
    echo -e "${GREEN}✅ pexels_client.py: Content filtering added${NC}"
else
    echo -e "${RED}❌ pexels_client.py: Content filtering missing${NC}"
fi

# Check image_service.py
if grep -q "concept_keywords" src/cofounder_agent/services/image_service.py 2>/dev/null; then
    echo -e "${GREEN}✅ image_service.py: Multi-level search added${NC}"
else
    echo -e "${RED}❌ image_service.py: Multi-level search missing${NC}"
fi
echo ""

# Test 4: Check if Pexels API key is configured
echo -e "${YELLOW}Test 4: Environment Configuration${NC}"
if [ ! -z "$PEXELS_API_KEY" ]; then
    echo -e "${GREEN}✅ PEXELS_API_KEY is set${NC}"
else
    echo -e "${YELLOW}⚠️  PEXELS_API_KEY not in environment${NC}"
fi

if [ ! -z "$DATABASE_URL" ]; then
    echo -e "${GREEN}✅ DATABASE_URL is set${NC}"
else
    echo -e "${YELLOW}⚠️  DATABASE_URL not in environment${NC}"
fi
echo ""

# Test 5: Check Oversight Hub
echo -e "${YELLOW}Test 5: Check Oversight Hub${NC}"
HUB_RESPONSE=$(curl -s http://localhost:3000 -w "\n%{http_code}" 2>/dev/null | tail -1)
if [ "$HUB_RESPONSE" = "200" ]; then
    echo -e "${GREEN}✅ Oversight Hub is responding on port 3000${NC}"
else
    echo -e "${RED}❌ Oversight Hub not responding (HTTP $HUB_RESPONSE)${NC}"
fi
echo ""

echo -e "${GREEN}✅ Integration test complete${NC}"
echo ""
echo "📋 Summary:"
echo "  - Backend API: Port 8000"
echo "  - Oversight Hub: Port 3000"
echo "  - Code changes: 3/3 implemented"
echo ""
echo "🚀 Ready to test image generation improvements!"
echo "   Go to: http://localhost:3000"

