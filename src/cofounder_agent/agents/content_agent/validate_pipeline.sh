#!/bin/bash
# Content Pipeline Pre-Flight Validation Script
# Run this before executing the content agent pipeline

set -e  # Exit on any error

echo "🔍 Glad Labs Content Pipeline Pre-Flight Check"
echo "=============================================="

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track failures
FAILURES=0

# Function to check environment variable
check_env_var() {
    local var_name=$1
    local required=${2:-true}
    
    if [ -z "${!var_name}" ]; then
        if [ "$required" = true ]; then
            echo -e "${RED}✗${NC} $var_name: NOT SET (REQUIRED)"
            ((FAILURES++))
        else
            echo -e "${YELLOW}⚠${NC} $var_name: NOT SET (OPTIONAL)"
        fi
    else
        echo -e "${GREEN}✓${NC} $var_name: SET"
    fi
}

# 1. Environment Variables Check
echo -e "\n${GREEN}1️⃣  Checking environment variables...${NC}"
echo "----------------------------------------"

# Critical variables
check_env_var "STRAPI_API_URL"
check_env_var "STRAPI_API_TOKEN"
check_env_var "FIRESTORE_PROJECT_ID"
check_env_var "GCS_BUCKET_NAME"
check_env_var "PUBSUB_TOPIC"
check_env_var "PUBSUB_SUBSCRIPTION"

# API Keys (at least one should be set)
echo -e "\n${GREEN}AI Provider API Keys (at least one required):${NC}"
HAS_AI_KEY=false
if [ -n "$OPENAI_API_KEY" ]; then
    echo -e "${GREEN}✓${NC} OPENAI_API_KEY: SET"
    HAS_AI_KEY=true
fi
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo -e "${GREEN}✓${NC} ANTHROPIC_API_KEY: SET"
    HAS_AI_KEY=true
fi
if [ -n "$GOOGLE_API_KEY" ]; then
    echo -e "${GREEN}✓${NC} GOOGLE_API_KEY: SET"
    HAS_AI_KEY=true
fi
if [ "$HAS_AI_KEY" = false ]; then
    echo -e "${RED}✗${NC} No AI provider API key found (need at least one)"
    ((FAILURES++))
fi

check_env_var "PEXELS_API_KEY" false
check_env_var "SERPER_API_KEY" false

# 2. Strapi Connectivity Check
echo -e "\n${GREEN}2️⃣  Checking Strapi connectivity...${NC}"
echo "----------------------------------------"

if [ -n "$STRAPI_API_URL" ] && [ -n "$STRAPI_API_TOKEN" ]; then
    if curl -sf -H "Authorization: Bearer $STRAPI_API_TOKEN" \
            "$STRAPI_API_URL/api/posts?pagination[limit]=1" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Strapi API accessible at $STRAPI_API_URL"
    else
        echo -e "${RED}✗${NC} Cannot connect to Strapi at $STRAPI_API_URL"
        echo "  Make sure Strapi is running: cd cms/strapi-main && npm run develop"
        ((FAILURES++))
    fi
else
    echo -e "${YELLOW}⚠${NC} Skipping Strapi check (missing URL or token)"
fi

# 3. Python Environment Check
echo -e "\n${GREEN}3️⃣  Checking Python environment...${NC}"
echo "----------------------------------------"

if command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
    echo -e "${GREEN}✓${NC} Python found: $PYTHON_VERSION"
    
    # Check if we're in the content agent directory
    if [ -f "requirements.txt" ]; then
        echo "  Installing dependencies..."
        pip install -q -r requirements.txt 2>&1 | grep -v "already satisfied" || true
        echo -e "${GREEN}✓${NC} Dependencies installed"
    else
        echo -e "${YELLOW}⚠${NC} requirements.txt not found (run from content agent directory)"
    fi
else
    echo -e "${RED}✗${NC} Python not found in PATH"
    ((FAILURES++))
fi

# 4. Module Import Check
echo -e "\n${GREEN}4️⃣  Checking Python modules...${NC}"
echo "----------------------------------------"

if python -c "from orchestrator import Orchestrator" 2>/dev/null; then
    echo -e "${GREEN}✓${NC} Orchestrator module imports successfully"
else
    echo -e "${RED}✗${NC} Cannot import Orchestrator module"
    echo "  Run from: src/agents/content_agent/"
    ((FAILURES++))
fi

# 5. Directory Structure Check
echo -e "\n${GREEN}5️⃣  Checking directory structure...${NC}"
echo "----------------------------------------"

check_dir() {
    if [ -d "$1" ]; then
        echo -e "${GREEN}✓${NC} $1 exists"
    else
        echo -e "${RED}✗${NC} $1 not found"
        ((FAILURES++))
    fi
}

check_file() {
    if [ -f "$1" ]; then
        echo -e "${GREEN}✓${NC} $1 exists"
    else
        echo -e "${YELLOW}⚠${NC} $1 not found"
    fi
}

check_dir "agents"
check_dir "services"
check_dir "utils"
check_dir "tests"
check_file "orchestrator.py"
check_file "config.py"
check_file "prompts.json"

# 6. Quick Smoke Test (if pytest available)
echo -e "\n${GREEN}6️⃣  Running smoke tests...${NC}"
echo "----------------------------------------"

if command -v pytest &> /dev/null && [ -d "tests" ]; then
    echo "Running orchestrator initialization test..."
    if cd tests && python -m pytest test_orchestrator_init.py::test_orchestrator_initializes -v 2>&1 | tail -n 20; then
        cd ..
        echo -e "${GREEN}✓${NC} Smoke test passed"
    else
        cd ..
        echo -e "${RED}✗${NC} Smoke test failed"
        ((FAILURES++))
    fi
else
    echo -e "${YELLOW}⚠${NC} pytest not available or tests directory not found"
    echo "  Install with: pip install pytest"
fi

# Final Summary
echo -e "\n=============================================="
if [ $FAILURES -eq 0 ]; then
    echo -e "${GREEN}✅ ALL CHECKS PASSED${NC}"
    echo ""
    echo "Content pipeline is ready to run!"
    echo ""
    echo "To start the pipeline:"
    echo "  cd src/agents/content_agent"
    echo "  python orchestrator.py"
    echo ""
    echo "To run with specific topic:"
    echo "  python create_task.py --topic 'Your Topic Here'"
    exit 0
else
    echo -e "${RED}❌ $FAILURES CHECK(S) FAILED${NC}"
    echo ""
    echo "Please fix the issues above before running the pipeline."
    echo ""
    echo "Common fixes:"
    echo "  - Set missing environment variables in .env file"
    echo "  - Start Strapi: cd cms/strapi-main && npm run develop"
    echo "  - Install Python deps: pip install -r requirements.txt"
    echo "  - Run from correct directory: cd src/agents/content_agent"
    exit 1
fi
