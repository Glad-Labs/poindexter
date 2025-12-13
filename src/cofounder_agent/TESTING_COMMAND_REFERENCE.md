#!/bin/bash
# FastAPI Testing - Command Reference Script
# Usage: Source this file or run commands directly
# Location: src/cofounder_agent/testing_commands.sh

echo "🚀 FastAPI Testing Command Reference"
echo "===================================="
echo ""
echo "Quick Commands:"
echo ""
echo "  Test Execution:"
echo "    pytest                        # Run all tests"
echo "    pytest -v                     # Verbose output"
echo "    pytest -s                     # Show print statements"
echo "    pytest -q                     # Quiet output"
echo ""
echo "  By Category:"
echo "    pytest -m unit                # Unit tests only"
echo "    pytest -m integration         # Integration tests only"
echo "    pytest -m e2e                 # E2E tests only"
echo "    pytest -m api                 # API endpoint tests"
echo "    pytest -m performance         # Performance tests"
echo "    pytest -m security            # Security tests"
echo "    pytest -m 'not slow'          # Skip slow tests"
echo ""
echo "  Coverage:"
echo "    pytest --cov=.                # Show coverage"
echo "    pytest --cov=. --cov-report=html   # HTML report"
echo "    pytest --cov=. --cov-report=term-missing   # Show missing"
echo ""
echo "  Debugging:"
echo "    pytest -x                     # Stop on first failure"
echo "    pytest --pdb                  # Drop to debugger"
echo "    pytest -v -s test_file.py::test_func  # Debug specific test"
echo ""
echo "  Specific Tests:"
echo "    pytest tests/test_api_integration.py         # One file"
echo "    pytest tests/test_api_integration.py::TestClass  # One class"
echo "    pytest tests/test_api_integration.py::TestClass::test_method  # One method"
echo ""
echo "  Performance:"
echo "    pytest --durations=10         # Show slowest tests"
echo "    pytest --timeout=10           # Timeout after 10s"
echo ""
echo "  Reporting:"
echo "    pytest --collect-only         # List tests without running"
echo "    pytest --tb=short             # Short traceback"
echo "    pytest --tb=long              # Long traceback"
echo ""
echo "For more info, see: TESTING_QUICK_REFERENCE.md"
echo ""

# Define functions for common tasks
function test_all() {
    echo "Running all tests..."
    pytest -v
}

function test_unit() {
    echo "Running unit tests..."
    pytest -m unit -v
}

function test_integration() {
    echo "Running integration tests..."
    pytest -m integration -v
}

function test_coverage() {
    echo "Running tests with coverage..."
    pytest --cov=. --cov-report=html --cov-report=term-missing
    echo "Coverage report generated: htmlcov/index.html"
}

function test_fast() {
    echo "Running fast tests (excluding slow)..."
    pytest -m "not slow" -v
}

function test_debug() {
    echo "Running with debugger..."
    pytest --pdb -v
}

function test_watch() {
    echo "Running in watch mode..."
    pytest-watch -- -v
}

# Show usage if sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Available functions:"
    echo "  test_all        - Run all tests"
    echo "  test_unit       - Run unit tests"
    echo "  test_integration - Run integration tests"
    echo "  test_coverage   - Run with coverage report"
    echo "  test_fast       - Run excluding slow tests"
    echo "  test_debug      - Run with debugger"
    echo "  test_watch      - Run in watch mode"
    echo ""
    echo "Example:"
    echo "  ./testing_commands.sh test_unit"
fi

---

# Python version - testing_commands.py
#!/usr/bin/env python3
"""
FastAPI Testing Command Helper
Provides quick access to common testing commands
"""

import subprocess
import sys
from pathlib import Path

class TestCommands:
    """Helper class for running test commands"""
    
    def __init__(self):
        self.project_root = Path(__file__).parent
    
    def run_command(self, cmd: str) -> int:
        """Run shell command and return exit code"""
        print(f"Running: {cmd}")
        print("=" * 80)
        result = subprocess.run(cmd, shell=True, cwd=self.project_root)
        return result.returncode
    
    def test_all(self) -> int:
        """Run all tests"""
        return self.run_command("pytest -v")
    
    def test_unit(self) -> int:
        """Run unit tests only"""
        return self.run_command("pytest -m unit -v")
    
    def test_integration(self) -> int:
        """Run integration tests only"""
        return self.run_command("pytest -m integration -v")
    
    def test_e2e(self) -> int:
        """Run E2E tests only"""
        return self.run_command("pytest -m e2e -v")
    
    def test_coverage(self) -> int:
        """Run tests with coverage"""
        return self.run_command(
            "pytest --cov=. --cov-report=html --cov-report=term-missing"
        )
    
    def test_coverage_html(self) -> int:
        """Run coverage and open HTML report"""
        result = self.test_coverage()
        if result == 0:
            print("\nOpening coverage report...")
            import webbrowser
            webbrowser.open((self.project_root / "htmlcov" / "index.html").as_uri())
        return result
    
    def test_fast(self) -> int:
        """Run fast tests (exclude slow)"""
        return self.run_command('pytest -m "not slow" -v')
    
    def test_performance(self) -> int:
        """Run performance tests"""
        return self.run_command("pytest -m performance -v")
    
    def test_security(self) -> int:
        """Run security tests"""
        return self.run_command("pytest -m security -v")
    
    def test_quiet(self) -> int:
        """Run tests quietly"""
        return self.run_command("pytest -q")
    
    def test_watch(self) -> int:
        """Run tests in watch mode"""
        return self.run_command("pytest-watch -- -v")
    
    def test_debug(self, test_name: str = "") -> int:
        """Run with debugger"""
        if test_name:
            return self.run_command(f"pytest --pdb -v {test_name}")
        return self.run_command("pytest --pdb -v")
    
    def show_coverage(self) -> int:
        """Show coverage report"""
        return self.run_command(
            "pytest --cov=. --cov-report=term-missing"
        )
    
    def show_slowest_tests(self, count: int = 10) -> int:
        """Show slowest tests"""
        return self.run_command(f"pytest --durations={count}")
    
    def list_tests(self) -> int:
        """List all tests"""
        return self.run_command("pytest --collect-only -q")
    
    def lint(self) -> int:
        """Run linting"""
        return self.run_command("pylint . --disable=all --enable=E,F")
    
    def help(self):
        """Show help"""
        print("""
FastAPI Testing Commands
=======================

Usage: python testing_commands.py <command>

Commands:
  all          - Run all tests
  unit         - Run unit tests only
  integration  - Run integration tests only
  e2e          - Run E2E tests only
  coverage     - Run tests with coverage
  coverage-html - Run coverage and show HTML report
  fast         - Run fast tests (exclude slow)
  performance  - Run performance tests
  security     - Run security tests
  quiet        - Run tests quietly
  watch        - Run tests in watch mode
  debug [test] - Run with debugger (optional: specific test)
  show-coverage - Show coverage report
  slowest [n]  - Show slowest tests (default: 10)
  list         - List all tests
  lint         - Run linting
  help         - Show this help

Examples:
  python testing_commands.py unit
  python testing_commands.py coverage-html
  python testing_commands.py debug tests/test_api_integration.py
  python testing_commands.py slowest 20
        """)

def main():
    commands = TestCommands()
    
    if len(sys.argv) < 2:
        commands.help()
        return 1
    
    command = sys.argv[1]
    
    if command == "all":
        return commands.test_all()
    elif command == "unit":
        return commands.test_unit()
    elif command == "integration":
        return commands.test_integration()
    elif command == "e2e":
        return commands.test_e2e()
    elif command == "coverage":
        return commands.test_coverage()
    elif command == "coverage-html":
        return commands.test_coverage_html()
    elif command == "fast":
        return commands.test_fast()
    elif command == "performance":
        return commands.test_performance()
    elif command == "security":
        return commands.test_security()
    elif command == "quiet":
        return commands.test_quiet()
    elif command == "watch":
        return commands.test_watch()
    elif command == "debug":
        test_name = sys.argv[2] if len(sys.argv) > 2 else ""
        return commands.test_debug(test_name)
    elif command == "show-coverage":
        return commands.show_coverage()
    elif command == "slowest":
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        return commands.show_slowest_tests(count)
    elif command == "list":
        return commands.list_tests()
    elif command == "lint":
        return commands.lint()
    elif command == "help" or command == "-h" or command == "--help":
        commands.help()
        return 0
    else:
        print(f"Unknown command: {command}")
        commands.help()
        return 1

if __name__ == "__main__":
    sys.exit(main())

---

# Usage Examples

## Using the Python version:

# Run all tests
python testing_commands.py all

# Run unit tests
python testing_commands.py unit

# Run with coverage
python testing_commands.py coverage

# Open coverage HTML report
python testing_commands.py coverage-html

# Debug specific test
python testing_commands.py debug tests/test_api_integration.py

# Show 20 slowest tests
python testing_commands.py slowest 20

# Run tests in watch mode
python testing_commands.py watch

---

# Using the Bash version:

source testing_commands.sh

test_all
test_unit
test_coverage
test_fast
test_debug

---

# Makefile version - add to Makefile

.PHONY: test test-unit test-integration test-e2e test-coverage test-fast test-performance test-debug test-watch test-list

test:
	pytest -v

test-unit:
	pytest -m unit -v

test-integration:
	pytest -m integration -v

test-e2e:
	pytest -m e2e -v

test-api:
	pytest -m api -v

test-coverage:
	pytest --cov=. --cov-report=html --cov-report=term-missing

test-fast:
	pytest -m "not slow" -v

test-performance:
	pytest -m performance -v

test-security:
	pytest -m security -v

test-debug:
	pytest --pdb -v

test-watch:
	pytest-watch -- -v

test-list:
	pytest --collect-only -q

test-quiet:
	pytest -q

# Usage: make test-unit
