#!/usr/bin/env python3
"""
Phase 1 Implementation Verification Tests
Tests for TemplateExecutionService and template execution endpoints
"""

import asyncio
import json
import sys
from datetime import datetime
from typing import Any, Dict

import httpx

# Test configuration
BASE_URL = "http://localhost:8000"
API_BASE = f"{BASE_URL}/api/workflows"

# ANSI colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_test_header(test_name: str):
    """Print test header"""
    print(f"\n{BOLD}{'=' * 80}{RESET}")
    print(f"{BOLD}TEST: {test_name}{RESET}")
    print(f"{BOLD}{'=' * 80}{RESET}")


def print_pass(message: str):
    """Print success message"""
    print(f"{GREEN}✅ {message}{RESET}")


def print_fail(message: str):
    """Print failure message"""
    print(f"{RED}❌ {message}{RESET}")


def print_info(message: str):
    """Print info message"""
    print(f"{YELLOW}ℹ️  {message}{RESET}")


async def test_templates_endpoint():
    """Test GET /templates endpoint"""
    print_test_header("Templates Endpoint")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{API_BASE}/templates")

            if response.status_code == 200:
                templates = response.json()
                print_pass(f"Retrieved {len(templates)} templates")

                # Verify all expected templates present
                template_names = [t["name"] for t in templates]
                expected = ["blog_post", "social_media", "email", "newsletter", "market_analysis"]

                for name in expected:
                    if name in template_names:
                        print_pass(f"  - Template '{name}' found")
                    else:
                        print_fail(f"  - Template '{name}' NOT found")
                        return False

                # Print template details
                for template in templates:
                    print_info(
                        f"{template['name']}: {len(template.get('phases', []))} phases, "
                        f"est. {template.get('estimated_duration_seconds', 0)}s"
                    )

                return True
            else:
                print_fail(f"HTTP {response.status_code}: {response.text}")
                return False

    except Exception as e:
        print_fail(f"Exception: {str(e)}")
        return False


async def test_execute_template():
    """Test POST /execute/{template_name} endpoint"""
    print_test_header("Execute Template Endpoint")

    test_cases = [
        {
            "template": "social_media",
            "input": {
                "topic": "AI and the future of work",
                "keywords": ["AI", "automation", "jobs"],
                "tone": "optimistic",
            },
        },
        {
            "template": "email",
            "input": {
                "campaign_name": "Summer Sales",
                "call_to_action": "Shop our Summer Collection",
                "discount": "20%",
            },
        },
    ]

    all_passed = True

    for test_case in test_cases:
        template_name = test_case["template"]
        task_input = test_case["input"]

        print_info(f"Testing template: {template_name}")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{API_BASE}/execute/{template_name}",
                    json=task_input,
                )

                if response.status_code == 200:
                    result = response.json()

                    # Verify response structure
                    required_fields = [
                        "execution_id",
                        "workflow_id",
                        "template",
                        "status",
                        "phase_results",
                    ]

                    for field in required_fields:
                        if field in result:
                            print_pass(f"  - Field '{field}' present")
                        else:
                            print_fail(f"  - Field '{field}' MISSING")
                            all_passed = False

                    # Get execution ID for next test
                    execution_id = result.get("execution_id")
                    status = result.get("status")
                    print_pass(
                        f"  - Execution ID: {execution_id[:8]}... "
                        f"Status: {status}"
                    )

                else:
                    print_fail(f"  - HTTP {response.status_code}: {response.text[:100]}")
                    all_passed = False

        except Exception as e:
            print_fail(f"  - Exception: {str(e)}")
            all_passed = False

    return all_passed


async def test_invalid_template():
    """Test error handling for invalid template"""
    print_test_header("Invalid Template Error Handling")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_BASE}/execute/nonexistent_template",
                json={"test": "data"},
            )

            if response.status_code == 404:
                print_pass("Returns HTTP 404 for invalid template")
                error_detail = response.json().get("detail", "")
                if "not found" in error_detail.lower():
                    print_pass("Error message mentions 'not found'")
                    return True
                else:
                    print_fail(f"Error message unclear: {error_detail}")
                    return False
            else:
                print_fail(f"Expected HTTP 404, got {response.status_code}")
                return False

    except Exception as e:
        print_fail(f"Exception: {str(e)}")
        return False


async def test_status_endpoint():
    """Test GET /status/{execution_id} endpoint"""
    print_test_header("Status Endpoint")

    # First, create a workflow execution
    print_info("Creating workflow execution...")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Execute a workflow
            execute_response = await client.post(
                f"{API_BASE}/execute/social_media",
                json={"topic": "Test", "tone": "casual"},
            )

            if execute_response.status_code != 200:
                print_fail(f"Failed to create execution: HTTP {execute_response.status_code}")
                return False

            execution_id = execute_response.json().get("execution_id")
            print_pass(f"Created execution: {execution_id[:8]}...")

            # Query status
            print_info("Querying execution status...")
            status_response = await client.get(f"{API_BASE}/status/{execution_id}")

            if status_response.status_code == 200:
                status_data = status_response.json()

                required_fields = ["execution_id", "status", "workflow_id"]
                for field in required_fields:
                    if field in status_data:
                        print_pass(f"  - Field '{field}' present")
                    else:
                        print_fail(f"  - Field '{field}' MISSING")
                        return False

                print_pass(f"Status: {status_data.get('status')}")
                return True
            else:
                print_fail(f"HTTP {status_response.status_code}: {status_response.text}")
                return False

    except Exception as e:
        print_fail(f"Exception: {str(e)}")
        return False


async def test_history_endpoint():
    """Test GET /history endpoint"""
    print_test_header("History Endpoint")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_BASE}/history?limit=10&offset=0")

            if response.status_code == 200:
                result = response.json()

                required_fields = ["executions", "total_count"]
                for field in required_fields:
                    if field in result:
                        print_pass(f"  - Field '{field}' present")
                    else:
                        print_fail(f"  - Field '{field}' MISSING")
                        return False

                executions = result.get("executions", [])
                total_count = result.get("total_count", 0)

                print_pass(f"Retrieved {len(executions)} executions (total: {total_count})")

                # Verify execution structure
                if executions:
                    first_exec = executions[0]
                    exec_fields = ["execution_id", "workflow_id", "status"]
                    for field in exec_fields:
                        if field in first_exec:
                            print_pass(f"  - Execution field '{field}' present")
                        else:
                            print_fail(f"  - Execution field '{field}' MISSING")
                            return False

                return True
            else:
                print_fail(f"HTTP {response.status_code}: {response.text}")
                return False

    except Exception as e:
        print_fail(f"Exception: {str(e)}")
        return False


async def main():
    """Run all tests"""
    print(f"\n{BOLD}{'=' * 80}")
    print("PHASE 1 IMPLEMENTATION VERIFICATION")
    print("=" * 80 + RESET)
    print(f"Backend: {BASE_URL}")
    print(f"Time: {datetime.now().isoformat()}\n")

    # Wait for backend to be ready
    print_info("Waiting for backend to be ready...")
    for attempt in range(10):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{BASE_URL}/health")
                if response.status_code == 200:
                    print_pass("Backend is ready")
                    break
        except:
            if attempt == 9:
                print_fail("Backend not responding")
                return False
            await asyncio.sleep(1)

    # Run tests
    results = {}

    results["Templates"] = await test_templates_endpoint()
    results["Invalid Template"] = await test_invalid_template()
    results["Execute Template"] = await test_execute_template()
    results["Status Endpoint"] = await test_status_endpoint()
    results["History Endpoint"] = await test_history_endpoint()

    # Print summary
    print(f"\n{BOLD}{'=' * 80}")
    print("TEST SUMMARY")
    print("=" * 80 + RESET)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, passed_test in results.items():
        status = f"{GREEN}PASS{RESET}" if passed_test else f"{RED}FAIL{RESET}"
        print(f"  {status} - {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print(f"\n{GREEN}{BOLD}✅ All tests passed!{RESET}")
        return True
    else:
        print(f"\n{RED}{BOLD}❌ Some tests failed{RESET}")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
