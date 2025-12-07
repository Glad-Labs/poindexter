#!/bin/bash

##############################################################################
# Coverage Measurement Script for Glad Labs
# 
# Measures test coverage and generates reports with >80% threshold enforcement
# 
# Usage:
#   ./scripts/measure-coverage.sh [html|json|term|all]
#
# Examples:
#   ./scripts/measure-coverage.sh html      # Generate HTML report
#   ./scripts/measure-coverage.sh json      # Generate JSON report
#   ./scripts/measure-coverage.sh term      # Print terminal report
#   ./scripts/measure-coverage.sh all       # Generate all reports
##############################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_PATH="${PROJECT_ROOT}/src/cofounder_agent"
COVERAGE_THRESHOLD=80
REPORT_TYPE="${1:-all}"

##############################################################################
# Functions
##############################################################################

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_dependencies() {
    log_info "Checking dependencies..."
    
    if ! command -v python &> /dev/null; then
        log_error "Python is not installed"
        exit 1
    fi
    
    if ! python -c "import coverage" 2>/dev/null; then
        log_warning "coverage.py is not installed. Installing..."
        pip install coverage
    fi
    
    if ! python -c "import pytest" 2>/dev/null; then
        log_error "pytest is not installed. Please run: pip install pytest"
        exit 1
    fi
    
    log_success "All dependencies are available"
}

measure_coverage() {
    log_info "Measuring test coverage..."
    log_info "Python path: ${PYTHON_PATH}"
    log_info "Test files: ${PROJECT_ROOT}/src/cofounder_agent/tests/"
    echo ""
    
    cd "${PROJECT_ROOT}"
    
    # Run tests with coverage measurement
    coverage run \
        --source="${PYTHON_PATH}" \
        --omit="*/tests/*,*/test_*.py,*/__pycache__/*" \
        -m pytest \
        src/cofounder_agent/tests/ \
        -v \
        --tb=short \
        -m "not slow"
    
    if [ $? -eq 0 ]; then
        log_success "Tests completed successfully"
    else
        log_error "Some tests failed"
        exit 1
    fi
}

generate_terminal_report() {
    log_info "Generating terminal coverage report..."
    echo ""
    
    coverage report \
        --fail-under=${COVERAGE_THRESHOLD} \
        --precision=2 \
        --show-missing \
        --skip-covered
    
    COVERAGE_RESULT=$?
    
    if [ ${COVERAGE_RESULT} -eq 0 ]; then
        log_success "Coverage threshold met (>=${COVERAGE_THRESHOLD}%)"
    else
        log_error "Coverage below threshold (${COVERAGE_THRESHOLD}%)"
        return 1
    fi
}

generate_html_report() {
    log_info "Generating HTML coverage report..."
    
    coverage html
    
    if [ -f "${PROJECT_ROOT}/htmlcov/index.html" ]; then
        log_success "HTML report generated: ${PROJECT_ROOT}/htmlcov/index.html"
        
        # Try to open in browser (if available)
        if command -v open &> /dev/null; then
            open "${PROJECT_ROOT}/htmlcov/index.html"
        elif command -v xdg-open &> /dev/null; then
            xdg-open "${PROJECT_ROOT}/htmlcov/index.html"
        fi
    else
        log_error "Failed to generate HTML report"
        return 1
    fi
}

generate_json_report() {
    log_info "Generating JSON coverage report..."
    
    coverage json
    
    if [ -f "${PROJECT_ROOT}/coverage.json" ]; then
        log_success "JSON report generated: ${PROJECT_ROOT}/coverage.json"
        
        # Parse and display summary
        if command -v python &> /dev/null; then
            python << 'EOF'
import json
import sys

try:
    with open('coverage.json', 'r') as f:
        data = json.load(f)
    
    summary = data.get('totals', {})
    pct_covered = summary.get('percent_covered', 0)
    
    print(f"\n📊 Coverage Summary:")
    print(f"   Overall: {pct_covered:.1f}%")
    print(f"   Lines covered: {summary.get('covered_lines', 0)}")
    print(f"   Lines missing: {summary.get('missing_lines', 0)}")
    print(f"   Total lines: {summary.get('num_statements', 0)}")
    
except Exception as e:
    print(f"Error parsing coverage.json: {e}", file=sys.stderr)
EOF
        fi
    else
        log_error "Failed to generate JSON report"
        return 1
    fi
}

generate_xml_report() {
    log_info "Generating XML coverage report (for CI/CD)..."
    
    coverage xml
    
    if [ -f "${PROJECT_ROOT}/coverage.xml" ]; then
        log_success "XML report generated: ${PROJECT_ROOT}/coverage.xml"
    else
        log_error "Failed to generate XML report"
        return 1
    fi
}

display_summary() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║         Coverage Measurement Complete                      ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Generated Reports:"
    
    [ -f "${PROJECT_ROOT}/htmlcov/index.html" ] && echo "  ✓ HTML:   ${PROJECT_ROOT}/htmlcov/index.html"
    [ -f "${PROJECT_ROOT}/coverage.json" ] && echo "  ✓ JSON:   ${PROJECT_ROOT}/coverage.json"
    [ -f "${PROJECT_ROOT}/coverage.xml" ] && echo "  ✓ XML:    ${PROJECT_ROOT}/coverage.xml"
    
    echo ""
    echo "Threshold: ${COVERAGE_THRESHOLD}%"
    echo ""
    echo "To view detailed report: open htmlcov/index.html"
    echo ""
}

##############################################################################
# Main Execution
##############################################################################

main() {
    log_info "═══════════════════════════════════════════════════════════"
    log_info "Glad Labs - Test Coverage Measurement"
    log_info "═══════════════════════════════════════════════════════════"
    echo ""
    
    check_dependencies
    measure_coverage
    
    # Generate requested reports
    case "${REPORT_TYPE}" in
        html)
            generate_html_report
            ;;
        json)
            generate_json_report
            ;;
        xml)
            generate_xml_report
            ;;
        term|terminal)
            generate_terminal_report
            ;;
        all)
            generate_terminal_report || true
            generate_html_report || true
            generate_json_report || true
            generate_xml_report || true
            ;;
        *)
            log_error "Unknown report type: ${REPORT_TYPE}"
            echo "Usage: measure-coverage.sh [html|json|xml|term|all]"
            exit 1
            ;;
    esac
    
    display_summary
}

# Run main
main
