#!/usr/bin/env python3
"""
AI Co-Founder Test Runner
Comprehensive test execution with reporting and analysis
"""

import subprocess
import sys
import os
import argparse
import json
from datetime import datetime
from pathlib import Path
import time

class TestRunner:
    """Comprehensive test runner for AI Co-Founder system"""
    
    def __init__(self):
        self.test_dir = Path(__file__).parent
        self.project_root = self.test_dir.parent.parent.parent
        self.results = {
            "start_time": None,
            "end_time": None,
            "test_runs": [],
            "summary": {}
        }
    
    def run_test_suite(self, test_type="all", verbose=True, coverage=True):
        """Run specified test suite"""
        
        print(f"🚀 Starting AI Co-Founder Test Suite")
        print(f"📁 Test Directory: {self.test_dir}")
        print(f"🏠 Project Root: {self.project_root}")
        print("="*80)
        
        self.results["start_time"] = datetime.now()
        
        # Test configurations
        test_configs = {
            "unit": {
                "files": ["test_unit_comprehensive.py"],
                "markers": ["-m", "unit"],
                "description": "Unit Tests - Component isolation and logic validation"
            },
            "integration": {
                "files": ["test_api_integration.py"], 
                "markers": ["-m", "integration or api"],
                "description": "Integration Tests - API endpoints and service connections"
            },
            "e2e": {
                "files": ["test_e2e_comprehensive.py"],
                "markers": ["-m", "e2e"],
                "description": "End-to-End Tests - Complete user workflows"
            },
            "performance": {
                "files": ["test_unit_comprehensive.py", "test_e2e_comprehensive.py"],
                "markers": ["-m", "performance"],
                "description": "Performance Tests - System benchmarks and load testing"
            },
            "smoke": {
                "files": ["test_unit_comprehensive.py::TestIntelligentCoFounder::test_cofounder_initialization"],
                "markers": ["-m", "smoke or (unit and not slow)"],
                "description": "Smoke Tests - Quick validation of core functionality"
            },
            "all": {
                "files": ["test_unit_comprehensive.py", "test_api_integration.py", "test_e2e_comprehensive.py"],
                "markers": [],
                "description": "Complete Test Suite - All tests"
            }
        }
        
        if test_type not in test_configs:
            print(f"❌ Unknown test type: {test_type}")
            print(f"Available types: {', '.join(test_configs.keys())}")
            return False
        
        config = test_configs[test_type]
        print(f"🎯 Running: {config['description']}")
        print(f"📋 Test Type: {test_type.upper()}")
        
        # Build pytest command
        cmd = ["python", "-m", "pytest"]
        
        # Add test files
        if config["files"]:
            cmd.extend(config["files"])
        
        # Add markers if specified
        if config["markers"]:
            cmd.extend(config["markers"])
        
        # Add verbosity
        if verbose:
            cmd.append("-v")
        else:
            cmd.append("-q")
        
        # Add coverage if requested
        if coverage and test_type != "smoke":
            cmd.extend([
                "--cov=../",
                "--cov-report=html:htmlcov",
                "--cov-report=term-missing",
                "--cov-branch"
            ])
        
        # Add output formatting
        cmd.extend([
            "--tb=short",
            "-ra",
            f"--junit-xml=test_results_{test_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml"
        ])
        
        print(f"🔧 Command: {' '.join(cmd)}")
        print("="*80)
        
        try:
            # Change to test directory
            os.chdir(self.test_dir)
            
            # Run tests
            start_time = time.time()
            result = subprocess.run(cmd, capture_output=False, text=True)
            end_time = time.time()
            duration = end_time - start_time
            
            # Record results
            test_run = {
                "test_type": test_type,
                "command": " ".join(cmd),
                "return_code": result.returncode,
                "duration": duration,
                "timestamp": datetime.now().isoformat(),
                "success": result.returncode == 0
            }
            
            self.results["test_runs"].append(test_run)
            
            # Print results
            if result.returncode == 0:
                print(f"\n✅ {config['description']} - PASSED")
                print(f"⏱️  Duration: {duration:.2f} seconds")
            else:
                print(f"\n❌ {config['description']} - FAILED")
                print(f"⏱️  Duration: {duration:.2f} seconds")
                print(f"🚨 Exit code: {result.returncode}")
            
            return result.returncode == 0
            
        except Exception as e:
            print(f"💥 Error running tests: {e}")
            return False
    
    def run_quick_validation(self):
        """Run quick smoke tests to validate system"""
        print("⚡ Quick Validation - Smoke Tests")
        return self.run_test_suite("smoke", verbose=False, coverage=False)
    
    def run_full_suite(self):
        """Run complete test suite with all test types"""
        print("🏁 Full Test Suite Execution")
        
        test_sequence = ["smoke", "unit", "integration", "e2e", "performance"]
        all_passed = True
        
        for test_type in test_sequence:
            print(f"\n{'='*20} {test_type.upper()} TESTS {'='*20}")
            success = self.run_test_suite(test_type, verbose=True, coverage=(test_type == "unit"))
            
            if not success:
                all_passed = False
                print(f"⚠️  {test_type.upper()} tests failed. Continuing with remaining tests...")
            
            # Short pause between test suites
            time.sleep(2)
        
        return all_passed
    
    def generate_report(self):
        """Generate comprehensive test report"""
        self.results["end_time"] = datetime.now()
        
        if self.results["start_time"]:
            total_duration = (self.results["end_time"] - self.results["start_time"]).total_seconds()
            self.results["total_duration"] = total_duration
        
        # Calculate summary
        total_runs = len(self.results["test_runs"])
        successful_runs = sum(1 for run in self.results["test_runs"] if run["success"])
        
        self.results["summary"] = {
            "total_test_runs": total_runs,
            "successful_runs": successful_runs,
            "failed_runs": total_runs - successful_runs,
            "success_rate": successful_runs / total_runs if total_runs > 0 else 0,
            "total_duration": self.results.get("total_duration", 0),
            "average_duration": sum(run["duration"] for run in self.results["test_runs"]) / total_runs if total_runs > 0 else 0
        }
        
        # Save report
        report_filename = f"test_execution_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_path = self.test_dir / report_filename
        
        with open(report_path, 'w') as f:
            json.dump(self.results, f, indent=2, default=str)
        
        # Print summary
        self.print_summary()
        
        return report_path
    
    def print_summary(self):
        """Print test execution summary"""
        summary = self.results["summary"]
        
        print("\n" + "="*80)
        print("📊 TEST EXECUTION SUMMARY")
        print("="*80)
        
        print(f"🏃 Total Test Runs: {summary['total_test_runs']}")
        print(f"✅ Successful Runs: {summary['successful_runs']}")
        print(f"❌ Failed Runs: {summary['failed_runs']}")
        print(f"📈 Success Rate: {summary['success_rate']:.1%}")
        print(f"⏱️  Total Duration: {summary['total_duration']:.2f} seconds")
        print(f"📊 Average Duration: {summary['average_duration']:.2f} seconds per run")
        
        print(f"\n📋 Test Run Details:")
        for i, run in enumerate(self.results["test_runs"], 1):
            status = "✅" if run["success"] else "❌"
            print(f"   {i}. {status} {run['test_type'].upper()} - {run['duration']:.2f}s")
        
        # Overall status
        if summary["success_rate"] >= 1.0:
            print(f"\n🎉 Excellent! All test suites passed!")
        elif summary["success_rate"] >= 0.8:
            print(f"\n✅ Good! Most test suites passed.")
        elif summary["success_rate"] >= 0.6:
            print(f"\n⚠️  Warning! Some test suites failed.")
        else:
            print(f"\n🚨 Alert! Multiple test suite failures.")
        
        print("="*80)

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="AI Co-Founder Test Runner")
    
    parser.add_argument(
        "test_type",
        nargs="?",
        default="all",
        choices=["unit", "integration", "e2e", "performance", "smoke", "all", "quick", "full"],
        help="Type of tests to run"
    )
    
    parser.add_argument(
        "--no-coverage",
        action="store_true",
        help="Skip coverage reporting"
    )
    
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity"
    )
    
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Generate report from existing results only"
    )
    
    args = parser.parse_args()
    
    runner = TestRunner()
    
    if args.report_only:
        report_path = runner.generate_report()
        print(f"📄 Report generated: {report_path}")
        return
    
    print("🧪 AI CO-FOUNDER SYSTEM TEST RUNNER")
    print(f"🕐 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    success = True
    
    try:
        if args.test_type == "quick":
            success = runner.run_quick_validation()
        elif args.test_type == "full":
            success = runner.run_full_suite()
        else:
            success = runner.run_test_suite(
                test_type=args.test_type,
                verbose=not args.quiet,
                coverage=not args.no_coverage
            )
    except KeyboardInterrupt:
        print("\n⚠️  Test execution interrupted by user")
        success = False
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        success = False
    finally:
        # Always generate report
        report_path = runner.generate_report()
        print(f"\n📄 Detailed report saved to: {report_path}")
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()