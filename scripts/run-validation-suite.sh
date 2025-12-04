#!/bin/bash

# Content Pipeline Validation Suite Runner
# Comprehensive testing of edge cases and pipeline workflows
# Usage: ./scripts/run-validation-suite.sh [full|quick|edge-cases|performance]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COFOUNDER_DIR="$PROJECT_ROOT/src/cofounder_agent"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Mode selection
MODE="${1:-full}"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Content Pipeline Validation Suite                            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Function to print section header
print_header() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Function to print status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
        exit 1
    fi
}

# Check if we're in the right directory
if [ ! -d "$COFOUNDER_DIR" ]; then
    echo -e "${RED}Error: cofounder_agent directory not found at $COFOUNDER_DIR${NC}"
    exit 1
fi

cd "$COFOUNDER_DIR"

# Run different test modes
case "$MODE" in
    full)
        print_header "Running Full Validation Suite"
        echo "This runs all 32+ tests covering edge cases, pipeline workflow, and performance"
        echo ""
        
        echo -e "${YELLOW}Testing System Health...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestSystemHealth -v --tb=short
        print_status $? "System health tests passed"
        echo ""
        
        echo -e "${YELLOW}Testing Basic Functionality...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestBasicTaskCreation -v --tb=short
        print_status $? "Basic functionality tests passed"
        echo ""
        
        echo -e "${YELLOW}Testing Edge Cases...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestEdgeCases -v --tb=short
        print_status $? "Edge case tests passed"
        echo ""
        
        echo -e "${YELLOW}Testing Content Pipeline Workflow...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestContentPipeline -v --tb=short
        print_status $? "Content pipeline tests passed"
        echo ""
        
        echo -e "${YELLOW}Testing Post Creation...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestPostCreation -v --tb=short
        print_status $? "Post creation tests passed"
        echo ""
        
        echo -e "${YELLOW}Testing Error Handling...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestErrorHandling -v --tb=short
        print_status $? "Error handling tests passed"
        echo ""
        
        echo -e "${YELLOW}Testing Performance...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestPerformance -v --tb=short
        print_status $? "Performance tests passed"
        echo ""
        
        echo -e "${YELLOW}Testing Integration...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestIntegration -v --tb=short
        print_status $? "Integration tests passed"
        echo ""
        
        echo -e "${YELLOW}Running All Tests with Coverage Report...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py -v --cov=. --cov-report=term --cov-report=html
        print_status $? "Full test suite with coverage completed"
        ;;
        
    quick)
        print_header "Running Quick Smoke Test (< 5 minutes)"
        echo "This runs only critical health and basic functionality tests"
        echo ""
        
        echo -e "${YELLOW}Running quick validation tests...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestSystemHealth \
                         tests/test_content_pipeline_comprehensive.py::TestBasicTaskCreation::test_create_task_with_minimal_fields \
                         -v --tb=short
        print_status $? "Quick validation tests passed"
        ;;
        
    edge-cases)
        print_header "Running Edge Case Tests Only"
        echo "Tests unicode, long strings, special characters, boundary conditions"
        echo ""
        
        echo -e "${YELLOW}Running edge case tests...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestEdgeCases -v --tb=short
        print_status $? "Edge case tests passed"
        ;;
        
    performance)
        print_header "Running Performance Baseline Tests"
        echo "Tests concurrent operations, large datasets, response times"
        echo ""
        
        echo -e "${YELLOW}Running performance tests...${NC}"
        python -m pytest tests/test_content_pipeline_comprehensive.py::TestPerformance -v --tb=short
        print_status $? "Performance tests passed"
        
        echo ""
        echo -e "${YELLOW}Testing API response times manually...${NC}"
        python3 << 'EOF'
from fastapi.testclient import TestClient
from src.cofounder_agent.main import app
import time
import statistics

client = TestClient(app)

print("📊 API Response Time Baselines:\n")

# Task creation
times = []
for i in range(5):
    start = time.time()
    response = client.post('/api/tasks', json={
        'task_name': f'Performance Test {i}',
        'topic': 'AI Trends',
        'primary_keyword': 'ai'
    })
    end = time.time()
    times.append((end-start)*1000)

print(f"Task Creation:")
print(f"  Average: {statistics.mean(times):.2f}ms")
print(f"  Min: {min(times):.2f}ms, Max: {max(times):.2f}ms")

# List tasks
times = []
for i in range(5):
    start = time.time()
    response = client.get('/api/tasks?skip=0&limit=20')
    end = time.time()
    times.append((end-start)*1000)

print(f"\nList Tasks:")
print(f"  Average: {statistics.mean(times):.2f}ms")
print(f"  Min: {min(times):.2f}ms, Max: {max(times):.2f}ms")

# Get health
times = []
for i in range(10):
    start = time.time()
    response = client.get('/api/health')
    end = time.time()
    times.append((end-start)*1000)

print(f"\nHealth Check:")
print(f"  Average: {statistics.mean(times):.2f}ms")
print(f"  Min: {min(times):.2f}ms, Max: {max(times):.2f}ms")

print("\n✅ Performance baseline collected")
EOF
        ;;
        
    *)
        echo -e "${RED}Unknown mode: $MODE${NC}"
        echo ""
        echo "Usage: $0 [full|quick|edge-cases|performance]"
        echo ""
        echo "Modes:"
        echo "  full         - Run all 32+ tests with coverage (recommended)"
        echo "  quick        - Quick smoke test (~2 minutes)"
        echo "  edge-cases   - Edge case tests only"
        echo "  performance  - Performance and load tests"
        exit 1
        ;;
esac

echo ""
print_header "Validation Complete"
echo -e "${GREEN}✅ All selected tests passed successfully${NC}"
echo ""
echo "Next steps:"
echo "  1. Review test results above"
echo "  2. Check htmlcov/index.html for coverage report (if using 'full')"
echo "  3. Update Oversight Hub components to use new API client"
echo "  4. Deploy to staging environment"
echo ""
