#!/usr/bin/env bash
# Gemini Integration Testing Script
# Tests Gemini functionality in Glad Labs system
# Usage: bash scripts/test-gemini.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}Gemini Integration Test Suite${NC}"
echo -e "${BLUE}======================================${NC}\n"

# Configuration
BACKEND_URL="http://localhost:8000"
API_KEY="${GOOGLE_API_KEY}"
TEST_CONVERSATION_ID="gemini-test-$(date +%s)"
TESTS_PASSED=0
TESTS_FAILED=0

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

log_test() {
    echo -e "${YELLOW}[TEST]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((TESTS_PASSED++))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((TESTS_FAILED++))
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

check_service() {
    if curl -s "$1" > /dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# ============================================================================
# TEST 1: ENVIRONMENT CHECK
# ============================================================================

log_test "Environment Configuration"

if [ -z "$API_KEY" ]; then
    log_fail "GOOGLE_API_KEY not set in environment"
    echo "  Add to .env.local: GOOGLE_API_KEY=AIza..."
else
    log_pass "GOOGLE_API_KEY is configured (length: ${#API_KEY})"
fi

# ============================================================================
# TEST 2: BACKEND CONNECTIVITY
# ============================================================================

log_test "Backend Connectivity"

if check_service "$BACKEND_URL/api/health"; then
    log_pass "Backend is running on port 8000"
else
    log_fail "Backend not responding on port 8000"
    echo "  Start backend with: npm run dev:cofounder"
    exit 1
fi

# ============================================================================
# TEST 3: MODELS ENDPOINT
# ============================================================================

log_test "Available Models Endpoint"

MODELS_RESPONSE=$(curl -s "$BACKEND_URL/api/v1/models/available")

if echo "$MODELS_RESPONSE" | jq . > /dev/null 2>&1; then
    log_pass "Models endpoint returns valid JSON"
    GEMINI_MODELS=$(echo "$MODELS_RESPONSE" | jq '.models[] | select(.provider=="google") | .name' | wc -l)
    if [ "$GEMINI_MODELS" -gt 0 ]; then
        log_pass "Found $GEMINI_MODELS Gemini model(s)"
        echo "$MODELS_RESPONSE" | jq '.models[] | select(.provider=="google")' | grep '"name"'
    else
        log_fail "No Gemini models found in response"
        log_info "All available models:"
        echo "$MODELS_RESPONSE" | jq '.models[].provider' | sort | uniq -c
    fi
else
    log_fail "Models endpoint did not return valid JSON"
fi

# ============================================================================
# TEST 4: PROVIDER STATUS
# ============================================================================

log_test "Provider Status Check"

STATUS_RESPONSE=$(curl -s "$BACKEND_URL/api/v1/models/status")

if echo "$STATUS_RESPONSE" | jq . > /dev/null 2>&1; then
    log_pass "Provider status endpoint returns valid JSON"
    GOOGLE_STATUS=$(echo "$STATUS_RESPONSE" | jq '.providers.google.available // false')
    if [ "$GOOGLE_STATUS" = "true" ]; then
        log_pass "Google provider is available"
        echo "$STATUS_RESPONSE" | jq '.providers.google'
    else
        log_fail "Google provider is not available"
        echo "$STATUS_RESPONSE" | jq '.providers'
    fi
else
    log_fail "Provider status endpoint did not return valid JSON"
fi

# ============================================================================
# TEST 5: GEMINI CHAT TEST (Simple Message)
# ============================================================================

log_test "Gemini Chat - Simple Message"

CHAT_RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"conversationId\": \"$TEST_CONVERSATION_ID\",
    \"model\": \"gemini-1.5-pro\",
    \"message\": \"Say 'Hello from Gemini' and nothing else\"
  }")

if echo "$CHAT_RESPONSE" | jq . > /dev/null 2>&1; then
    RESPONSE_TEXT=$(echo "$CHAT_RESPONSE" | jq -r '.response // "ERROR"')
    PROVIDER=$(echo "$CHAT_RESPONSE" | jq -r '.provider // "unknown"')
    
    if [ "$PROVIDER" = "google" ]; then
        log_pass "Gemini response received"
        log_info "Response: $RESPONSE_TEXT"
        log_info "Provider: $PROVIDER"
    else
        log_fail "Wrong provider in response: $PROVIDER (expected: google)"
        log_info "This may indicate API key issue or rate limiting"
    fi
else
    log_fail "Chat endpoint did not return valid JSON"
    log_info "Response was: $CHAT_RESPONSE"
fi

# ============================================================================
# TEST 6: CONVERSATION HISTORY
# ============================================================================

log_test "Conversation History"

HISTORY_RESPONSE=$(curl -s "$BACKEND_URL/api/chat/history/$TEST_CONVERSATION_ID")

if echo "$HISTORY_RESPONSE" | jq . > /dev/null 2>&1; then
    MSG_COUNT=$(echo "$HISTORY_RESPONSE" | jq '.message_count // 0')
    if [ "$MSG_COUNT" -gt 0 ]; then
        log_pass "Conversation history retrieved ($MSG_COUNT messages)"
    else
        log_fail "No messages in conversation history"
    fi
else
    log_fail "History endpoint did not return valid JSON"
fi

# ============================================================================
# TEST 7: GEMINI CHAT TEST (Complex Message)
# ============================================================================

log_test "Gemini Chat - Complex Message"

COMPLEX_RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"conversationId\": \"${TEST_CONVERSATION_ID}-complex\",
    \"model\": \"gemini-1.5-pro\",
    \"message\": \"Write a 3-sentence summary of machine learning. Keep it concise.\"
  }")

if echo "$COMPLEX_RESPONSE" | jq . > /dev/null 2>&1; then
    log_pass "Complex message processed"
    RESPONSE_LENGTH=$(echo "$COMPLEX_RESPONSE" | jq -r '.response' | wc -w)
    log_info "Response length: ~$RESPONSE_LENGTH words"
else
    log_fail "Complex message processing failed"
fi

# ============================================================================
# TEST 8: ERROR HANDLING - Invalid Model
# ============================================================================

log_test "Error Handling - Invalid Model"

ERROR_RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"conversationId\": \"${TEST_CONVERSATION_ID}-error\",
    \"model\": \"invalid-model-xyz\",
    \"message\": \"test\"
  }")

if echo "$ERROR_RESPONSE" | jq . > /dev/null 2>&1; then
    HTTP_CODE=$(echo "$ERROR_RESPONSE" | jq -r '.status_code // .statusCode // "N/A"')
    if [[ "$HTTP_CODE" =~ ^(400|404|500)$ ]]; then
        log_pass "Invalid model properly rejected"
    else
        log_info "Request handled (may have fallen back to available model)"
    fi
else
    log_fail "Error response was invalid JSON"
fi

# ============================================================================
# TEST 9: PERFORMANCE - Response Time
# ============================================================================

log_test "Performance - Response Time"

START_TIME=$(date +%s%N)
PERF_RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"conversationId\": \"${TEST_CONVERSATION_ID}-perf\",
    \"model\": \"gemini-1.5-pro\",
    \"message\": \"What is 2+2?\"
  }")
END_TIME=$(date +%s%N)

RESPONSE_TIME=$(( (END_TIME - START_TIME) / 1000000 ))  # Convert to milliseconds

if [ "$RESPONSE_TIME" -lt 5000 ]; then
    log_pass "Response time: ${RESPONSE_TIME}ms (good)"
elif [ "$RESPONSE_TIME" -lt 10000 ]; then
    log_pass "Response time: ${RESPONSE_TIME}ms (acceptable)"
else
    log_fail "Response time: ${RESPONSE_TIME}ms (slow)"
fi

# ============================================================================
# TEST 10: FALLBACK BEHAVIOR
# ============================================================================

log_test "Fallback Chain Verification"

FALLBACK_RESPONSE=$(curl -s "$BACKEND_URL/api/v1/models/available" | jq '.models | group_by(.provider) | map({provider: .[0].provider, count: length})')

log_info "Available providers and model counts:"
echo "$FALLBACK_RESPONSE" | jq '.'

# ============================================================================
# TEST SUMMARY
# ============================================================================

echo ""
echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}======================================${NC}"

echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "${RED}Failed: $TESTS_FAILED${NC}"

TOTAL=$((TESTS_PASSED + TESTS_FAILED))
SUCCESS_RATE=$((TESTS_PASSED * 100 / TOTAL))

echo -e "Success Rate: ${SUCCESS_RATE}%"

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "\n${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "\n${RED}✗ Some tests failed. See above for details.${NC}"
    exit 1
fi
