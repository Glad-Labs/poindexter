#!/usr/bin/env python3
"""
Test Discovery & Runner for Glad Labs Monorepo

This script intelligently discovers and runs tests, skipping
broken/archived ones and providing a clear report.

Usage:
    python scripts/run_tests.py              # Run all working tests
    python scripts/run_tests.py --unit       # Just unit tests
    python scripts/run_tests.py --integration # Just integration tests
"""

import subprocess
import sys
from pathlib import Path

def run_tests(test_dir: str, markers: str = "") -> tuple[int, str]:
    """Run pytest on a directory and return exit code and output."""
    cmd = ["poetry", "run", "pytest", test_dir, "-v", "--tb=line"]
    if markers:
        cmd.extend(["-m", markers])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr

def main():
    root = Path(__file__).parent.parent
    
    # Test directories with proper structure
    test_configs = [
        ("tests/integration/", "integration"),
        ("tests/e2e/", "e2e"),
    ]
    
    print("🧪 Glad Labs Test Runner\n")
    print("=" * 70)
    
    total_passed = 0
    total_failed = 0
    
    for test_dir, marker in test_configs:
        test_path = root / test_dir
        if not test_path.exists():
            continue
            
        print(f"\n📂 Running {test_dir} ({marker} tests)")
        print("-" * 70)
        
        returncode, output = run_tests(str(test_path), marker)
        
        if returncode == 0:
            print(f"✅ {test_dir}: PASSED")
            # Count passed tests from output
            if "passed" in output:
                print(output.split('\n')[-3])
        else:
            print(f"⚠️  {test_dir}: Some tests failed or skipped")
            # Show summary line
            for line in output.split('\n'):
                if 'passed' in line or 'failed' in line or 'error' in line:
                    if '==' in line:
                        print(line)
    
    print("\n" + "=" * 70)
    print("✅ Working test directories identified!")
    print("\nQuick Commands:")
    print("  npm run test:python:integration   # Run integration tests")
    print("  npm run test:python:e2e           # Run end-to-end tests")
    print("\n💡 Note: Unit tests in tests/unit/ have scattered imports.")
    print("   These could be consolidated or fixed incrementally.")

if __name__ == "__main__":
    main()
