#!/bin/bash
# Automated End-to-End UI Testing Script
# Guides you through complete E2E workflow testing

set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

API_URL="${API_URL:-http://localhost:8000}"
UI_URL="${UI_URL:-http://localhost:3001}"
TEST_RESULTS_FILE="E2E_TEST_RESULTS_$(date +%Y%m%d_%H%M%S).txt"

# Initialize results file
{
    echo "=========================================="
    echo "E2E UI TESTING RESULTS"
    echo "=========================================="
    echo "Date: $(date)"
    echo "API URL: $API_URL"
    echo "UI URL: $UI_URL"
    echo ""
} > "$TEST_RESULTS_FILE"

echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   E2E UI TESTING - Oversight Hub ↔ Poindexter        ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# Phase 1: Health Checks
echo -e "${YELLOW}[PHASE 1/6] Service Health Verification${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Backend Health
echo -n "Checking backend health... "
if HEALTH=$(curl -s "$API_URL/health" 2>/dev/null); then
    if echo "$HEALTH" | grep -q "ok"; then
        echo -e "${GREEN}✅ PASS${NC}"
        echo "  ✓ Backend responding on port 8000" | tee -a "$TEST_RESULTS_FILE"
    else
        echo -e "${RED}❌ FAIL${NC}"
        echo "  ✗ Backend returned invalid response" | tee -a "$TEST_RESULTS_FILE"
        exit 1
    fi
else
    echo -e "${RED}❌ FAIL${NC}"
    echo "  ✗ Backend not responding (is 'npm run dev:cofounder' running?)" | tee -a "$TEST_RESULTS_FILE"
    exit 1
fi

# Frontend Health
echo -n "Checking frontend availability... "
if curl -s "$UI_URL" | grep -q "root"; then
    echo -e "${GREEN}✅ PASS${NC}"
    echo "  ✓ Oversight Hub responding on port 3001" | tee -a "$TEST_RESULTS_FILE"
else
    echo -e "${RED}❌ FAIL${NC}"
    echo "  ✗ Frontend not responding (is 'npm run dev:oversight' running?)" | tee -a "$TEST_RESULTS_FILE"
    exit 1
fi

# API Components
echo -n "Checking API components... "
if COMPONENTS=$(curl -s "$API_URL/api/health" 2>/dev/null); then
    if echo "$COMPONENTS" | jq . > /dev/null 2>&1; then
        echo -e "${GREEN}✅ PASS${NC}"
        echo "  ✓ API components responding" | tee -a "$TEST_RESULTS_FILE"
        echo "$COMPONENTS" | jq '.components' 2>/dev/null | tee -a "$TEST_RESULTS_FILE" || true
    else
        echo -e "${YELLOW}⚠️  PARTIAL${NC}"
        echo "  ⚠ API responding but components check failed" | tee -a "$TEST_RESULTS_FILE"
    fi
else
    echo -e "${YELLOW}⚠️  WARNING${NC}"
    echo "  ⚠ API health check skipped" | tee -a "$TEST_RESULTS_FILE"
fi

echo ""

# Phase 2: Chat API Tests
echo -e "${YELLOW}[PHASE 2/6] Chat Endpoint Testing${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

CONV_ID="e2e-$(date +%s%N)"

echo -n "Test 1/3: Simple chat (Ollama)... "
START_TIME=$(date +%s%N)
if CHAT_RESPONSE=$(curl -s -X POST "$API_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"What is the capital of France?\",
    \"model\": \"ollama-llama2\",
    \"conversationId\": \"$CONV_ID\",
    \"temperature\": 0.7,
    \"max_tokens\": 100
  }" 2>/dev/null); then
    END_TIME=$(date +%s%N)
    ELAPSED=$((($END_TIME - $START_TIME) / 1000000))
    
    if echo "$CHAT_RESPONSE" | grep -q "response"; then
        echo -e "${GREEN}✅ PASS${NC} (${ELAPSED}ms)"
        RESPONSE_PREVIEW=$(echo "$CHAT_RESPONSE" | jq -r '.response // "N/A"' 2>/dev/null | head -c 60)
        echo "  ✓ Response: ${RESPONSE_PREVIEW}..." | tee -a "$TEST_RESULTS_FILE"
    else
        echo -e "${RED}❌ FAIL${NC}"
        echo "  ✗ No response field in result" | tee -a "$TEST_RESULTS_FILE"
        echo "  Raw: $(echo "$CHAT_RESPONSE" | head -c 100)" | tee -a "$TEST_RESULTS_FILE"
    fi
else
    echo -e "${RED}❌ FAIL${NC}"
    echo "  ✗ Request failed" | tee -a "$TEST_RESULTS_FILE"
fi

echo -n "Test 2/3: Multi-turn conversation... "
if CHAT2=$(curl -s -X POST "$API_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Tell me about machine learning\",
    \"model\": \"ollama-llama2\",
    \"conversationId\": \"$CONV_ID\"
  }" 2>/dev/null); then
    if echo "$CHAT2" | grep -q "response"; then
        echo -e "${GREEN}✅ PASS${NC}"
        echo "  ✓ Multi-turn conversation working" | tee -a "$TEST_RESULTS_FILE"
    else
        echo -e "${RED}❌ FAIL${NC}"
        echo "  ✗ Second turn failed" | tee -a "$TEST_RESULTS_FILE"
    fi
else
    echo -e "${RED}❌ FAIL${NC}"
    echo "  ✗ Request failed" | tee -a "$TEST_RESULTS_FILE"
fi

echo -n "Test 3/3: Chat with different model... "
if CHAT3=$(curl -s -X POST "$API_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Hello\",
    \"model\": \"gpt-3.5\",
    \"conversationId\": \"$CONV_ID-alt\"
  }" 2>/dev/null); then
    if echo "$CHAT3" | grep -q "response"; then
        echo -e "${GREEN}✅ PASS${NC}"
        MODEL_USED=$(echo "$CHAT3" | jq -r '.provider // "unknown"' 2>/dev/null)
        echo "  ✓ Provider: $MODEL_USED" | tee -a "$TEST_RESULTS_FILE"
    else
        echo -e "${YELLOW}⚠️  PARTIAL${NC}"
        echo "  ⚠ Model tested but no response (API key issue?)" | tee -a "$TEST_RESULTS_FILE"
    fi
else
    echo -e "${RED}❌ FAIL${NC}"
    echo "  ✗ Request failed" | tee -a "$TEST_RESULTS_FILE"
fi

echo ""

# Phase 3: Task/Metrics API
echo -e "${YELLOW}[PHASE 3/6] Task & Metrics Endpoints${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo -n "Checking task metrics... "
if METRICS=$(curl -s "$API_URL/api/metrics" 2>/dev/null); then
    if echo "$METRICS" | grep -q "success_rate"; then
        echo -e "${GREEN}✅ PASS${NC}"
        TOTAL=$(echo "$METRICS" | jq -r '.total_tasks // 0' 2>/dev/null)
        SUCCESS=$(echo "$METRICS" | jq -r '.success_rate // 0' 2>/dev/null)
        echo "  ✓ Total tasks: $TOTAL | Success rate: ${SUCCESS}%" | tee -a "$TEST_RESULTS_FILE"
    else
        echo -e "${YELLOW}⚠️  PARTIAL${NC}"
        echo "  ⚠ Metrics endpoint responding but no success_rate field" | tee -a "$TEST_RESULTS_FILE"
    fi
else
    echo -e "${YELLOW}⚠️  WARNING${NC}"
    echo "  ⚠ Metrics endpoint not responding" | tee -a "$TEST_RESULTS_FILE"
fi

echo ""

# Phase 4: Frontend Integration
echo -e "${YELLOW}[PHASE 4/6] Frontend Integration Testing${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo -e "${BLUE}ℹ️  Open browser to: ${YELLOW}http://localhost:3001${NC}"
echo "${BLUE}ℹ️  Complete these tests manually:${NC}"
echo "    1. Page loads without errors"
echo "    2. Can navigate to Orchestrator/Composer"
echo "    3. Can enter natural language request"
echo "    4. Can submit request"
echo "    5. Response appears within 120 seconds"
echo "    6. Check browser console for errors (F12)"
echo ""
echo -e "${YELLOW}Press ENTER when you've completed Phase 4 manual testing...${NC}"
read -r

echo -n "Did all UI tests pass? (y/n) "
read -r UI_PASS
if [ "$UI_PASS" = "y" ]; then
    echo -e "${GREEN}✅ Frontend Integration PASS${NC}" | tee -a "$TEST_RESULTS_FILE"
else
    echo -e "${RED}❌ Frontend Integration FAIL${NC}" | tee -a "$TEST_RESULTS_FILE"
fi

echo ""

# Phase 5: Error Handling
echo -e "${YELLOW}[PHASE 5/6] Error Handling Tests${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo -n "Test 1/2: Invalid input handling... "
if INVALID=$(curl -s -X POST "$API_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"\",
    \"model\": \"ollama-llama2\",
    \"conversationId\": \"test\"
  }" 2>/dev/null); then
    # Should handle gracefully (either error message or validation)
    if echo "$INVALID" | grep -q "response\|error"; then
        echo -e "${GREEN}✅ PASS${NC}"
        echo "  ✓ Handles empty input gracefully" | tee -a "$TEST_RESULTS_FILE"
    else
        echo -e "${YELLOW}⚠️  PARTIAL${NC}"
        echo "  ⚠ Response unclear" | tee -a "$TEST_RESULTS_FILE"
    fi
else
    echo -e "${YELLOW}⚠️  WARNING${NC}"
    echo "  ⚠ Could not test" | tee -a "$TEST_RESULTS_FILE"
fi

echo -n "Test 2/2: Non-existent model fallback... "
if FALLBACK=$(curl -s -X POST "$API_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"test\",
    \"model\": \"fictional-model-xyz\",
    \"conversationId\": \"test-fallback\"
  }" 2>/dev/null); then
    if ! echo "$FALLBACK" | grep -q "500"; then
        echo -e "${GREEN}✅ PASS${NC}"
        echo "  ✓ Graceful fallback (no 500 error)" | tee -a "$TEST_RESULTS_FILE"
    else
        echo -e "${RED}❌ FAIL${NC}"
        echo "  ✗ Returns 500 error instead of fallback" | tee -a "$TEST_RESULTS_FILE"
    fi
else
    echo -e "${RED}❌ FAIL${NC}"
    echo "  ✗ Request failed" | tee -a "$TEST_RESULTS_FILE"
fi

echo ""

# Phase 6: Summary
echo -e "${YELLOW}[PHASE 6/6] Testing Summary${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "${BLUE}ℹ️   Test results saved to:${NC} ${YELLOW}$TEST_RESULTS_FILE${NC}"
echo ""
echo -e "${GREEN}✅ Automated E2E Testing Complete!${NC}"
echo ""
echo -e "${BLUE}Next Steps:${NC}"
echo "  1. Review results file: cat $TEST_RESULTS_FILE"
echo "  2. If all tests passed, you're ready for deployment!"
echo "  3. If any failed, check:"
echo "     - Backend logs: npm run dev:cofounder"
echo "     - Browser console: F12 on http://localhost:3001"
echo "     - Testing guide: NLP_AGENT_WORKFLOW_TESTING_GUIDE.md"
echo ""
