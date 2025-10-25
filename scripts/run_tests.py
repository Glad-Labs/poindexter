"""
Comprehensive Test Suite Documentation and Runner Configuration
Tests for GLAD Labs Settings Manager (API + UI)
"""

import subprocess
import sys
import json
from datetime import datetime
from pathlib import Path


class TestRunner:
    """Orchestrates running all test suites and collecting results"""

    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "backend_unit": None,
            "backend_integration": None,
            "frontend_unit": None,
            "frontend_integration": None,
            "coverage": None,
            "summary": {}
        }

    def run_backend_unit_tests(self):
        """Run Python unit tests for backend"""
        print("\n" + "=" * 70)
        print("BACKEND UNIT TESTS")
        print("=" * 70)
        
        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "pytest",
                    "src/cofounder_agent/tests/test_unit_settings_api.py",
                    "-v", "--tb=short"
                ],
                cwd=".",
                capture_output=True,
                text=True,
                timeout=120
            )

            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)

            self.results["backend_unit"] = {
                "return_code": result.returncode,
                "passed": result.returncode == 0,
                "timestamp": datetime.now().isoformat()
            }

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            print("❌ Backend unit tests timed out")
            self.results["backend_unit"] = {
                "return_code": 1,
                "passed": False,
                "error": "Timeout"
            }
            return False
        except Exception as e:
            print(f"❌ Error running backend unit tests: {e}")
            self.results["backend_unit"] = {
                "return_code": 1,
                "passed": False,
                "error": str(e)
            }
            return False

    def run_backend_integration_tests(self):
        """Run Python integration tests for backend"""
        print("\n" + "=" * 70)
        print("BACKEND INTEGRATION TESTS")
        print("=" * 70)
        
        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "pytest",
                    "src/cofounder_agent/tests/test_integration_settings.py",
                    "-v", "--tb=short"
                ],
                cwd=".",
                capture_output=True,
                text=True,
                timeout=120
            )

            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)

            self.results["backend_integration"] = {
                "return_code": result.returncode,
                "passed": result.returncode == 0,
                "timestamp": datetime.now().isoformat()
            }

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            print("❌ Backend integration tests timed out")
            self.results["backend_integration"] = {
                "return_code": 1,
                "passed": False,
                "error": "Timeout"
            }
            return False
        except Exception as e:
            print(f"❌ Error running backend integration tests: {e}")
            self.results["backend_integration"] = {
                "return_code": 1,
                "passed": False,
                "error": str(e)
            }
            return False

    def run_frontend_unit_tests(self):
        """Run React unit tests for frontend"""
        print("\n" + "=" * 70)
        print("FRONTEND UNIT TESTS")
        print("=" * 70)
        
        try:
            result = subprocess.run(
                [
                    "npm", "test",
                    "--",
                    "web/oversight-hub/__tests__/components/SettingsManager.test.jsx",
                    "--coverage", "--watchAll=false"
                ],
                cwd=".",
                capture_output=True,
                text=True,
                timeout=120
            )

            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)

            self.results["frontend_unit"] = {
                "return_code": result.returncode,
                "passed": result.returncode == 0,
                "timestamp": datetime.now().isoformat()
            }

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            print("❌ Frontend unit tests timed out")
            self.results["frontend_unit"] = {
                "return_code": 1,
                "passed": False,
                "error": "Timeout"
            }
            return False
        except Exception as e:
            print(f"❌ Error running frontend unit tests: {e}")
            self.results["frontend_unit"] = {
                "return_code": 1,
                "passed": False,
                "error": str(e)
            }
            return False

    def run_frontend_integration_tests(self):
        """Run React integration tests for frontend"""
        print("\n" + "=" * 70)
        print("FRONTEND INTEGRATION TESTS")
        print("=" * 70)
        
        try:
            result = subprocess.run(
                [
                    "npm", "test",
                    "--",
                    "web/oversight-hub/__tests__/integration/SettingsManager.integration.test.jsx",
                    "--coverage", "--watchAll=false"
                ],
                cwd=".",
                capture_output=True,
                text=True,
                timeout=120
            )

            print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)

            self.results["frontend_integration"] = {
                "return_code": result.returncode,
                "passed": result.returncode == 0,
                "timestamp": datetime.now().isoformat()
            }

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            print("❌ Frontend integration tests timed out")
            self.results["frontend_integration"] = {
                "return_code": 1,
                "passed": False,
                "error": "Timeout"
            }
            return False
        except Exception as e:
            print(f"❌ Error running frontend integration tests: {e}")
            self.results["frontend_integration"] = {
                "return_code": 1,
                "passed": False,
                "error": str(e)
            }
            return False

    def run_coverage_report(self):
        """Generate coverage reports"""
        print("\n" + "=" * 70)
        print("COVERAGE REPORT")
        print("=" * 70)
        
        try:
            # Python coverage
            result = subprocess.run(
                [
                    sys.executable, "-m", "pytest",
                    "src/cofounder_agent/tests/",
                    "--cov=src/cofounder_agent",
                    "--cov-report=html",
                    "--cov-report=term"
                ],
                cwd=".",
                capture_output=True,
                text=True,
                timeout=120
            )

            print("Python Coverage:")
            print(result.stdout)

            self.results["coverage"] = {
                "return_code": result.returncode,
                "passed": result.returncode == 0,
                "timestamp": datetime.now().isoformat()
            }

            return result.returncode == 0

        except Exception as e:
            print(f"⚠️ Error generating coverage report: {e}")
            self.results["coverage"] = {
                "return_code": 1,
                "passed": False,
                "error": str(e)
            }
            return False

    def print_summary(self):
        """Print test summary"""
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)

        total_passed = 0
        total_failed = 0

        for test_type, result in [
            ("Backend Unit", self.results["backend_unit"]),
            ("Backend Integration", self.results["backend_integration"]),
            ("Frontend Unit", self.results["frontend_unit"]),
            ("Frontend Integration", self.results["frontend_integration"]),
        ]:
            if result:
                status = "✅ PASSED" if result.get("passed") else "❌ FAILED"
                print(f"{test_type}: {status}")
                if result.get("passed"):
                    total_passed += 1
                else:
                    total_failed += 1

        print(f"\nTotal: {total_passed} passed, {total_failed} failed")

        return total_failed == 0

    def run_all_tests(self, coverage=False):
        """Run all test suites"""
        print("🧪 Starting comprehensive test suite...")
        print(f"Timestamp: {self.results['timestamp']}\n")

        all_passed = True

        # Backend tests
        if not self.run_backend_unit_tests():
            all_passed = False
        
        if not self.run_backend_integration_tests():
            all_passed = False

        # Frontend tests
        if not self.run_frontend_unit_tests():
            all_passed = False
        
        if not self.run_frontend_integration_tests():
            all_passed = False

        # Coverage
        if coverage:
            if not self.run_coverage_report():
                all_passed = False

        # Summary
        self.print_summary()

        return all_passed

    def save_results(self, filename="test_results.json"):
        """Save test results to file"""
        with open(filename, "w") as f:
            json.dump(self.results, f, indent=2)
        print(f"\n📊 Results saved to {filename}")


def main():
    """Main test runner entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Run GLAD Labs test suite")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--integration", action="store_true", help="Run integration tests only")
    parser.add_argument("--coverage", action="store_true", help="Generate coverage report")
    parser.add_argument("--backend", action="store_true", help="Run backend tests only")
    parser.add_argument("--frontend", action="store_true", help="Run frontend tests only")
    parser.add_argument("--save-results", action="store_true", help="Save results to JSON")

    args = parser.parse_args()

    runner = TestRunner()

    try:
        if not any([args.unit, args.integration, args.backend, args.frontend]):
            # Run all tests
            success = runner.run_all_tests(coverage=args.coverage)
        else:
            # Run selected tests
            if args.backend:
                if args.unit or not any([args.unit, args.integration]):
                    runner.run_backend_unit_tests()
                if args.integration or not any([args.unit, args.integration]):
                    runner.run_backend_integration_tests()
            
            if args.frontend:
                if args.unit or not any([args.unit, args.integration]):
                    runner.run_frontend_unit_tests()
                if args.integration or not any([args.unit, args.integration]):
                    runner.run_frontend_integration_tests()

            runner.print_summary()
            success = True

        if args.save_results:
            runner.save_results()

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n\n⚠️ Test suite interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
