#!/bin/bash
# Test Implementation Checklist & Runner Script
# Usage: bash TESTING_ACTION_PLAN.sh [command]

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Function to print section headers
print_section() {
    echo -e "\n${BLUE}=== $1 ===${NC}\n"
}

# Function to print status
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

# Function to print warning
print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Function to print error
print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Show current test status
test_status() {
    print_section "TEST STATUS REPORT"
    
    echo "Backend Test Files:"
    find src/cofounder_agent/tests -name "test_*.py" -type f | wc -l
    echo " test files found"
    
    echo -e "\nFrontend Test Files:"
    find web -name "*.test.js" -o -name "*.test.jsx" | wc -l
    echo " test files found"
    
    echo -e "\nRunning smoke tests (should all pass)..."
    npm run test:python:smoke 2>&1 | tail -20
}

# Run all backend tests and generate report
test_backend() {
    print_section "RUNNING BACKEND TESTS"
    
    echo "Running pytest..."
    cd src/cofounder_agent
    python -m pytest tests/ -v --tb=short 2>&1 | tee test_output.log
    
    # Count results
    PASSED=$(grep -c "PASSED" test_output.log || echo "0")
    FAILED=$(grep -c "FAILED" test_output.log || echo "0")
    SKIPPED=$(grep -c "SKIPPED" test_output.log || echo "0")
    
    echo -e "\n${GREEN}Test Results:${NC}"
    echo "  Passed: $PASSED"
    echo "  Failed: $FAILED"
    echo "  Skipped: $SKIPPED"
    
    cd "$PROJECT_ROOT"
}

# Run frontend tests
test_frontend() {
    print_section "RUNNING FRONTEND TESTS"
    
    echo "Running Jest tests..."
    npm run test:ci 2>&1 | tail -30
}

# Generate coverage report
test_coverage() {
    print_section "GENERATING COVERAGE REPORTS"
    
    echo "Backend coverage..."
    npm run test:python:coverage 2>&1 | tail -20
    
    echo -e "\n${GREEN}Coverage reports generated!${NC}"
    echo "Open: src/cofounder_agent/htmlcov/index.html"
}

# List all test files
list_tests() {
    print_section "ALL TEST FILES"
    
    echo -e "${BLUE}Backend Tests:${NC}"
    find src/cofounder_agent/tests -name "test_*.py" -type f | sort | nl
    
    echo -e "\n${BLUE}Frontend Tests:${NC}"
    find web -name "*.test.js" -o -name "*.test.jsx" | sort | nl
}

# Run specific test file
run_test_file() {
    local test_file=$1
    if [ -z "$test_file" ]; then
        print_error "Please specify a test file"
        return 1
    fi
    
    print_section "RUNNING: $test_file"
    
    if [[ $test_file == *.py ]]; then
        cd src/cofounder_agent
        python -m pytest "tests/$test_file" -v --tb=short
        cd "$PROJECT_ROOT"
    else
        npm test -- "$test_file" --watch=false
    fi
}

# Fix common test issues
fix_tests() {
    print_section "ATTEMPTING TO FIX COMMON TEST ISSUES"
    
    print_warning "Clearing pytest cache..."
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    
    print_warning "Clearing jest cache..."
    find . -type d -name ".jest" -exec rm -rf {} + 2>/dev/null || true
    
    print_warning "Clearing coverage..."
    find . -type d -name ".coverage" -o -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
    
    print_status "Cache cleaned. Reinstalling dependencies..."
    npm run install:all
    
    print_status "Ready to run tests again: npm run test:python"
}

# Show test writing guide
test_guide() {
    print_section "TEST WRITING GUIDE"
    
    cat << 'EOF'

BACKEND TEST TEMPLATE (Python/pytest):
--------------------------------------
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

@pytest.mark.unit
@pytest.mark.api
class TestMyRoute:
    """Test MyRoute endpoints"""
    
    def test_successful_call(self):
        """Test successful endpoint call"""
        response = client.get("/api/myroute")
        assert response.status_code == 200
    
    def test_error_handling(self):
        """Test error handling"""
        response = client.get("/api/myroute/invalid")
        assert response.status_code in [404, 400]

FRONTEND TEST TEMPLATE (JavaScript/Jest):
------------------------------------------
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MyComponent from './MyComponent';

describe('MyComponent', () => {
  it('renders correctly', () => {
    render(<MyComponent />);
    expect(screen.getByText('Expected')).toBeInTheDocument();
  });
  
  it('handles user interaction', async () => {
    render(<MyComponent />);
    const button = screen.getByRole('button', { name: /click/i });
    await userEvent.click(button);
    expect(screen.getByText('Updated')).toBeInTheDocument();
  });
});

RUNNING TESTS:
--------------
npm run test:python                    # All backend tests
npm run test:python:smoke              # Fast smoke tests
npm run test                           # All frontend tests
npm run test:all                       # Everything
pytest -m unit                         # Only unit tests
pytest -m integration                  # Only integration tests
pytest -m security                     # Only security tests

EOF
}

# Priority: Fix these tests first
priority_tests() {
    print_section "PRIORITY TEST FIXES"
    
    cat << 'EOF'

HIGH PRIORITY (Core functionality):
1. test_auth_unified.py - Authentication is critical
2. test_content_routes_unit.py - Core feature
3. test_task_routes.py - Core feature
4. test_e2e_fixed.py - Should already pass (5/5 ✅)

MEDIUM PRIORITY (Important features):
5. test_model_selection_routes.py - Model routing
6. test_command_queue_routes.py - Task execution
7. test_bulk_task_routes.py - Bulk operations
8. test_database_service.py - Data persistence

LOWER PRIORITY (Nice to have):
9. test_websocket_routes.py - Real-time features
10. test_analytics_routes.py - Reporting

Next step: Run high priority tests first
$ npm run test:python -- tests/test_auth_unified.py -v

EOF
}

# Show help
show_help() {
    cat << 'EOF'

Usage: bash TESTING_ACTION_PLAN.sh [command]

Commands:
  status              - Show current test status
  backend             - Run all backend tests
  frontend            - Run all frontend tests
  coverage            - Generate coverage reports
  list                - List all test files
  run FILE            - Run specific test file
  fix                 - Fix common test issues
  guide               - Show test writing guide
  priority            - Show priority fixes
  help                - Show this help message

Examples:
  bash TESTING_ACTION_PLAN.sh status
  bash TESTING_ACTION_PLAN.sh run test_auth_unified.py
  bash TESTING_ACTION_PLAN.sh coverage
  bash TESTING_ACTION_PLAN.sh fix

EOF
}

# Main command router
case "${1:-help}" in
    status)
        test_status
        ;;
    backend)
        test_backend
        ;;
    frontend)
        test_frontend
        ;;
    coverage)
        test_coverage
        ;;
    list)
        list_tests
        ;;
    run)
        run_test_file "$2"
        ;;
    fix)
        fix_tests
        ;;
    guide)
        test_guide
        ;;
    priority)
        priority_tests
        ;;
    help)
        show_help
        ;;
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac

print_status "Done!"
