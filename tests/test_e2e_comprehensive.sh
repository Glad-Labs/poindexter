#!/bin/bash
# Comprehensive End-to-End Test Script
# Tests the entire task-to-post publishing pipeline

BACKEND_URL="${1:-http://localhost:8000}"
WAIT_SECONDS="${2:-10}"

# Color output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Counters
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Helper functions
pass() { echo -e "${GREEN}[✓]${NC} $1"; ((PASSED_TESTS++)); }
fail() { echo -e "${RED}[✗]${NC} $1"; ((FAILED_TESTS++)); }
info() { echo -e "${CYAN}[i]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
start_test() { echo -e "\n${CYAN}$1${NC}"; ((TOTAL_TESTS++)); }

# Test 1: Server Health Check
start_test "Test 1: Verifying backend server is running..."
HEALTH=$(curl -s "$BACKEND_URL/api/health" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', ''))" 2>/dev/null)
if [ "$HEALTH" = "healthy" ]; then
    pass "Backend server is healthy"
else
    fail "Server health check failed"
    exit 1
fi

# Test 2: Create Task
start_test "Test 2: Creating test task..."
TASK_RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "E2E Test - Microservices Architecture Patterns",
    "type": "content_generation",
    "topic": "Microservices Architecture Patterns",
    "category": "technology"
  }')

TASK_ID=$(echo "$TASK_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('id', ''))" 2>/dev/null)
if [ -n "$TASK_ID" ]; then
    pass "Task created with ID: $TASK_ID"
else
    fail "Failed to create task"
    exit 1
fi

# Test 3: Wait for Task Completion
start_test "Test 3: Waiting for task completion (${WAIT_SECONDS}s)..."
sleep "$WAIT_SECONDS"

TASK_STATUS=$(curl -s "$BACKEND_URL/api/tasks/$TASK_ID")
STATUS=$(echo "$TASK_STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', ''))" 2>/dev/null)
POST_CREATED=$(echo "$TASK_STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('post_created', False))" 2>/dev/null)
CONTENT_LEN=$(echo "$TASK_STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('content_length', 0))" 2>/dev/null)

if [ "$STATUS" = "completed" ]; then
    pass "Task completed successfully (Status: $STATUS)"
    info "  Post Created: $POST_CREATED"
    info "  Content Length: $CONTENT_LEN chars"
    
    if [ "$POST_CREATED" = "True" ]; then
        pass "Post was created successfully"
    else
        fail "Task completed but post was not created"
    fi
else
    fail "Task did not complete. Status: $STATUS"
fi

# Test 4: Verify Post in Database
start_test "Test 4: Verifying post was created in database..."
DB_RESULT=$(psql "postgresql://postgres:postgres@localhost:5432/glad_labs_dev" -t -c "
SELECT COUNT(*) FROM posts 
WHERE created_at > NOW() - INTERVAL '5 minutes';" 2>/dev/null)

if [ "$DB_RESULT" -gt 0 ]; then
    pass "Found $DB_RESULT recent post(s) in database"
    
    # Get details
    psql "postgresql://postgres:postgres@localhost:5432/glad_labs_dev" -t -c "
    SELECT 'Title: ' || title || E'\n' ||
           'Slug: ' || slug || E'\n' ||
           'Chars: ' || LENGTH(content) || E'\n' ||
           'Status: ' || status || E'\n' ||
           'SEO Title: ' || COALESCE(seo_title, 'N/A')
    FROM posts
    WHERE created_at > NOW() - INTERVAL '5 minutes'
    ORDER BY created_at DESC LIMIT 1;" 2>/dev/null | sed 's/^/    /'
else
    warn "Could not verify posts in database"
fi

# Test 5: Create Multiple Tasks
start_test "Test 5: Creating multiple tasks to test concurrent processing..."
TOPICS=("Artificial Intelligence in Healthcare" "Blockchain Technology Revolution" "Quantum Computing Future")
TASK_IDS=()
SUCCESS_COUNT=0

for topic in "${TOPICS[@]}"; do
    RESPONSE=$(curl -s -X POST "$BACKEND_URL/api/tasks" \
      -H "Content-Type: application/json" \
      -d "{
        \"task_name\": \"E2E Multi-Test: $topic\",
        \"type\": \"content_generation\",
        \"topic\": \"$topic\"
      }")
    
    ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('id', ''))" 2>/dev/null)
    if [ -n "$ID" ]; then
        TASK_IDS+=("$ID")
        ((SUCCESS_COUNT++))
        info "  Created task for: $topic"
    fi
done

if [ "$SUCCESS_COUNT" -eq "${#TOPICS[@]}" ]; then
    pass "All ${#TOPICS[@]} concurrent tasks created"
else
    warn "Only $SUCCESS_COUNT/${#TOPICS[@]} tasks created"
fi

# Test 6: Wait for Concurrent Tasks
start_test "Test 6: Waiting for concurrent tasks to complete..."
sleep "$WAIT_SECONDS"

COMPLETED_COUNT=0
POSTS_CREATED_COUNT=0

for id in "${TASK_IDS[@]}"; do
    RESPONSE=$(curl -s "$BACKEND_URL/api/tasks/$id")
    STATUS=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', ''))" 2>/dev/null)
    POST_CREATED=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('result', {}).get('post_created', False))" 2>/dev/null)
    
    if [ "$STATUS" = "completed" ]; then
        ((COMPLETED_COUNT++))
        if [ "$POST_CREATED" = "True" ]; then
            ((POSTS_CREATED_COUNT++))
        fi
    fi
done

if [ "$COMPLETED_COUNT" -eq "${#TASK_IDS[@]}" ]; then
    pass "All ${#TASK_IDS[@]} concurrent tasks completed"
    if [ "$POSTS_CREATED_COUNT" -eq "${#TASK_IDS[@]}" ]; then
        pass "All ${#TASK_IDS[@]} posts were created"
    else
        warn "Only $POSTS_CREATED_COUNT/${#TASK_IDS[@]} posts created"
    fi
else
    warn "Only $COMPLETED_COUNT/${#TASK_IDS[@]} tasks completed"
fi

# Test 7: Verify API Response Times
start_test "Test 7: Checking API response performance..."
TIMES=()

for i in {1..3}; do
    START=$(date +%s%N)
    curl -s "$BACKEND_URL/api/health" > /dev/null
    END=$(date +%s%N)
    ELAPSED=$(( (END - START) / 1000000 ))  # Convert to milliseconds
    TIMES+=($ELAPSED)
done

AVG_TIME=$(( (${TIMES[0]} + ${TIMES[1]} + ${TIMES[2]}) / 3 ))
if [ "$AVG_TIME" -lt 1000 ]; then
    pass "Average API response time: ${AVG_TIME}ms"
else
    warn "Average API response time: ${AVG_TIME}ms (slower than expected)"
fi

# Summary
echo ""
echo "=========================================="
echo "TEST SUMMARY"
echo "=========================================="
echo "Total Tests:    $TOTAL_TESTS"
echo -e "Passed:         ${GREEN}$PASSED_TESTS${NC}"
echo -e "Failed:         ${RED}$FAILED_TESTS${NC}"
echo "=========================================="

# Exit code
if [ "$FAILED_TESTS" -eq 0 ]; then
    pass "All tests passed!"
    exit 0
else
    fail "$FAILED_TESTS test(s) failed"
    exit 1
fi
