#!/usr/bin/env python3
"""
Quick Start Script for Ollama Pipeline Testing

This script automates the setup and execution of Ollama pipeline tests.

Features:
- Checks Ollama connectivity
- Verifies backend is running
- Runs comprehensive tests
- Generates quality reports
- Saves results

Usage:
    python run_ollama_tests.py
"""

import subprocess
import sys
import time
import json
from pathlib import Path
from typing import Tuple


def print_header(text: str):
    """Print formatted header"""
    print("\n" + "=" * 100)
    print(f"🚀 {text}")
    print("=" * 100)


def print_step(number: int, text: str):
    """Print formatted step"""
    print(f"\n📍 STEP {number}: {text}")
    print("─" * 100)


def check_ollama() -> bool:
    """Check if Ollama is running"""
    print_step(1, "Check Ollama Connectivity")

    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                models = data.get('models', [])
                print(f"✅ Ollama is running")
                print(f"   Available models: {len(models)}")
                for model in models:
                    print(f"   - {model.get('name', 'unknown')}")
                return True
            except json.JSONDecodeError:
                print("❌ Ollama responded but with invalid JSON")
                return False
        else:
            print("❌ Ollama is not responding")
            return False

    except subprocess.TimeoutExpired:
        print("❌ Ollama connection timeout")
        return False
    except Exception as e:
        print(f"❌ Error checking Ollama: {e}")
        return False


def check_backend() -> bool:
    """Check if backend is running"""
    print_step(2, "Check Backend API")

    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:8000/api/health"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                status = data.get('status', 'unknown')
                print(f"✅ Backend is running")
                print(f"   Status: {status}")
                return True
            except json.JSONDecodeError:
                print("❌ Backend responded but with invalid JSON")
                return False
        else:
            print("❌ Backend is not responding")
            return False

    except subprocess.TimeoutExpired:
        print("❌ Backend connection timeout")
        return False
    except Exception as e:
        print(f"❌ Error checking backend: {e}")
        return False


def run_connectivity_test() -> bool:
    """Run connectivity tests"""
    print_step(3, "Run Connectivity Tests")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_ollama_generation_pipeline.py::test_ollama_connectivity",
             "-v", "-s"],
            cwd="src/cofounder_agent",
            timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Test error: {e}")
        return False


def run_generation_tests() -> bool:
    """Run generation tests"""
    print_step(4, "Run Generation Tests")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_ollama_generation_pipeline.py::test_mistral_generation",
             "tests/test_ollama_generation_pipeline.py::test_llama2_generation",
             "-v", "-s"],
            cwd="src/cofounder_agent",
            timeout=300  # 5 minutes
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Test error: {e}")
        return False


def run_quality_tests() -> bool:
    """Run quality assessment tests"""
    print_step(5, "Run Quality Assessment Tests")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_quality_assessor.py",
             "-v", "-s"],
            cwd="src/cofounder_agent",
            timeout=120
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Test error: {e}")
        return False


def run_e2e_pipeline() -> bool:
    """Run end-to-end pipeline test"""
    print_step(6, "Run End-to-End Pipeline Test")

    try:
        result = subprocess.run(
            [sys.executable, "test_ollama_e2e.py"],
            cwd="src/cofounder_agent",
            timeout=600  # 10 minutes
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Test error: {e}")
        return False


def check_results() -> bool:
    """Check if results file was generated"""
    results_file = Path("src/cofounder_agent/ollama_e2e_results.json")
    if results_file.exists():
        with open(results_file, 'r') as f:
            results = json.load(f)

        print_step(7, "Review Results")
        print(f"✅ Results saved to: {results_file}")
        print(f"\n📊 SUMMARY:")
        summary = results.get('summary', {})
        for key, value in summary.items():
            print(f"   {key}: {value}")

        return True
    else:
        print(f"❌ Results file not found: {results_file}")
        return False


def main():
    """Main execution"""
    print_header("OLLAMA PIPELINE TESTING - QUICK START")

    # Step 1: Check prerequisites
    print_step(0, "Check Prerequisites")

    if not check_ollama():
        print("\n❌ ABORT: Ollama is not running")
        print("   Start Ollama in another terminal: ollama serve")
        return False

    if not check_backend():
        print("\n❌ ABORT: Backend is not running")
        print("   Start backend: python -m uvicorn src.cofounder_agent.main:app --reload")
        return False

    print("\n✅ All prerequisites met. Starting tests...\n")
    time.sleep(2)

    # Run tests
    results = {
        'connectivity': run_connectivity_test(),
        'generation': run_generation_tests(),
        'quality': run_quality_tests(),
        'e2e_pipeline': run_e2e_pipeline(),
    }

    # Check results
    check_results()

    # Summary
    print_header("TEST SUMMARY")

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    print(f"\n✅ Tests Passed: {passed}/{total}")
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"   {test_name}: {status}")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        return True
    else:
        print(f"\n⚠️ {total - passed} tests failed. Review output above.")
        return False


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⛔ Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        sys.exit(1)
