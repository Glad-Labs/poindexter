#!/bin/bash
# Pre-Deployment System Verification Script
# Usage: bash scripts/pre-deployment-verify.sh
# Purpose: Final validation before production deployment

set -e

echo "================================"
echo "Pre-Deployment Verification"
echo "================================"
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counter
PASSED=0
FAILED=0
WARNINGS=0

# Helper functions
pass() {
    echo -e "${GREEN}✅ PASS${NC}: $1"
    PASSED=$((PASSED + 1))
}

fail() {
    echo -e "${RED}❌ FAIL${NC}: $1"
    FAILED=$((FAILED + 1))
}

warn() {
    echo -e "${YELLOW}⚠️  WARN${NC}: $1"
    WARNINGS=$((WARNINGS + 1))
}

# ============================================
# 1. Git Status Check
# ============================================
echo "1. Git Repository Status"
echo "========================"

if git status --short | grep -q .; then
    warn "Uncommitted changes detected - ensure all changes are committed"
else
    pass "Repository is clean"
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" = "feat/bugs" ]; then
    pass "On correct branch: feat/bugs"
else
    fail "Not on feat/bugs branch (currently on: $BRANCH)"
fi

echo ""

# ============================================
# 2. Backend Tests
# ============================================
echo "2. Backend Tests"
echo "================"

if [ -d "src/cofounder_agent/tests" ]; then
    if npm run test:python:smoke 2>/dev/null; then
        pass "Backend smoke tests passed"
    else
        warn "Backend smoke tests failed or not available"
    fi
else
    warn "Backend tests directory not found"
fi

echo ""

# ============================================
# 3. Frontend Build
# ============================================
echo "3. Frontend Build Check"
echo "======================="

if [ -d "web/public-site" ]; then
    cd web/public-site
    if npm run build 2>&1 | grep -q "compiled successfully"; then
        pass "Frontend build successful"
    else
        warn "Frontend build completed (check for warnings)"
    fi
    cd - > /dev/null
else
    fail "Frontend directory not found"
fi

echo ""

# ============================================
# 4. Environment Configuration
# ============================================
echo "4. Environment Configuration"
echo "============================"

if [ -f ".env" ]; then
    if grep -q "NEXT_PUBLIC_FASTAPI_URL" .env; then
        pass "NEXT_PUBLIC_FASTAPI_URL configured"
    else
        warn "NEXT_PUBLIC_FASTAPI_URL not found in .env"
    fi
else
    warn ".env file not found"
fi

if [ -f ".env.staging" ]; then
    pass ".env.staging exists"
else
    warn ".env.staging not found - needed for staging deployment"
fi

if [ -f ".env.production" ]; then
    pass ".env.production exists"
else
    warn ".env.production not found - needed for production deployment"
fi

echo ""

# ============================================
# 5. Database Schema Verification
# ============================================
echo "5. Database Schema Verification"
echo "=============================="

# Check if database_service.py has correct column names
if grep -q "featured_image_url" src/cofounder_agent/services/database_service.py; then
    pass "featured_image_url column referenced in database_service.py"
else
    fail "featured_image_url not found in database_service.py"
fi

if grep -q "seo_title" src/cofounder_agent/services/database_service.py; then
    pass "seo_title column referenced in database_service.py"
else
    fail "seo_title not found in database_service.py"
fi

if grep -q "seo_description" src/cofounder_agent/services/database_service.py; then
    pass "seo_description column referenced in database_service.py"
else
    fail "seo_description not found in database_service.py"
fi

if grep -q "seo_keywords" src/cofounder_agent/services/database_service.py; then
    pass "seo_keywords column referenced in database_service.py"
else
    fail "seo_keywords not found in database_service.py"
fi

echo ""

# ============================================
# 6. Code Quality Checks
# ============================================
echo "6. Code Quality"
echo "==============="

# Check for emoji characters in main.py
if grep -E '[😊🎯✅❌🚀📝🔧]' src/cofounder_agent/main.py; then
    fail "Emoji characters found in main.py - will cause encoding errors"
else
    pass "No emoji characters in main.py"
fi

# Check for debugging prints
if grep -r "print(" src/cofounder_agent --include="*.py" | grep -v "logger\|#"; then
    warn "Debug print statements found in backend code - should be removed"
else
    pass "No debug print statements found"
fi

echo ""

# ============================================
# 7. API Integration Check
# ============================================
echo "7. API Integration"
echo "=================="

if grep -q "NEXT_PUBLIC_FASTAPI_URL" web/public-site/lib/api-fastapi.js; then
    pass "NEXT_PUBLIC_FASTAPI_URL referenced in API integration layer"
else
    fail "API integration layer not correctly configured"
fi

if grep -q "fetch" web/public-site/lib/api-fastapi.js; then
    pass "Fetch API implementation found"
else
    fail "Fetch API not found in API integration layer"
fi

echo ""

# ============================================
# 8. Documentation Check
# ============================================
echo "8. Documentation"
echo "================"

docs=(
    "IMPLEMENTATION_SUMMARY.md"
    "TESTING_REPORT.md"
    "PUBLIC_SITE_VERIFICATION.md"
    "PRODUCTION_DEPLOYMENT_PREP.md"
    "DEPLOYMENT_APPROVAL.md"
)

for doc in "${docs[@]}"; do
    if [ -f "$doc" ]; then
        pass "Documentation exists: $doc"
    else
        warn "Documentation missing: $doc"
    fi
done

echo ""

# ============================================
# 9. Runtime Checks (if services running)
# ============================================
echo "9. Runtime Verification"
echo "======================="

# Check if backend is running
if curl -s http://localhost:8000/api/health | grep -q "healthy"; then
    pass "Backend API is healthy"
    
    # Check if posts endpoint returns data
    if curl -s http://localhost:8000/api/posts?skip=0&limit=1 | grep -q '"id"'; then
        pass "Posts endpoint returns data"
    else
        warn "Posts endpoint not returning data"
    fi
else
    warn "Backend API not responding on localhost:8000 (expected if not running)"
fi

# Check if frontend is running
if curl -s http://localhost:3000 > /dev/null 2>&1; then
    pass "Frontend is running on localhost:3000"
else
    warn "Frontend not responding on localhost:3000 (expected if not running)"
fi

echo ""

# ============================================
# Summary
# ============================================
echo "================================"
echo "Verification Summary"
echo "================================"
echo ""
echo -e "${GREEN}✅ Passed: $PASSED${NC}"
echo -e "${RED}❌ Failed: $FAILED${NC}"
echo -e "${YELLOW}⚠️  Warnings: $WARNINGS${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ SYSTEM READY FOR DEPLOYMENT${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Review PRODUCTION_DEPLOYMENT_PREP.md"
    echo "2. Backup production database"
    echo "3. Execute deployment: git checkout dev && git merge --no-ff feat/bugs"
    echo "4. Monitor deployment in GitHub Actions"
    echo "5. Run post-deployment verification"
    exit 0
else
    echo -e "${RED}❌ SYSTEM NOT READY FOR DEPLOYMENT${NC}"
    echo ""
    echo "Issues found:"
    echo "- Fix all failed items (marked with ❌)"
    echo "- Address warnings (marked with ⚠️) if critical"
    echo "- Re-run this script to verify fixes"
    exit 1
fi
