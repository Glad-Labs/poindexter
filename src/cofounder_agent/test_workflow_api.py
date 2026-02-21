#!/usr/bin/env python
"""
Complete End-to-End Workflow API Test
Tests: Create → List → Execute → Monitor through actual API calls
"""
import json
import time
from datetime import datetime

import requests

BASE_URL = "http://localhost:8000"


def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def print_test(num, title):
    print(f"Test {num}: {title}")


def print_ok(msg):
    print(f"  [OK] {msg}")


def print_error(msg):
    print(f"  [ERROR] {msg}")


def main():
    print_section("COMPLETE WORKFLOW API TEST")
    workflow_id = None
    execution_id = None

    # Test 1: Verify backend is running
    print_test("1", "Backend Connectivity")
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=30)
        if resp.status_code == 200:
            print_ok(f"Backend is operational")
        else:
            print_error(f"Backend returned status {resp.status_code}")
            return 1
    except Exception as e:
        print_error(f"Backend not accessible: {e}")
        return 1
    print()

    # Test 2: Get available phases
    print_test("2", "Discover Available Phases")
    try:
        resp = requests.get(f"{BASE_URL}/api/workflows/available-phases", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            phases = data.get("phases", [])
            print_ok(f"Found {len(phases)} phases: {', '.join([p['name'] for p in phases[:3]])}")
            if len(phases) > 3:
                print(f"      ... and {len(phases) - 3} more")
        else:
            print_error(f"Failed to get phases: {resp.status_code}")
            print(f"Response: {resp.text[:200]}")
    except Exception as e:
        print_error(f"Request failed: {e}")
        return 1
    print()

    # Test 3: Create a custom workflow
    print_test("3", "Create Custom Workflow")
    try:
        workflow_def = {
            "name": "E2E Test Blog Pipeline",
            "description": "End-to-end test of blog content creation",
            "phases": [
                {
                    "index": 0,
                    "name": "research",
                    "user_inputs": {"topic": "Test Topic", "focus": "Testing"},
                },
                {
                    "index": 1,
                    "name": "draft",
                    "user_inputs": {
                        "prompt": "Draft content",
                        "content": "Test content",
                        "target_audience": "Testers",
                        "tone": "casual",
                    },
                },
                {
                    "index": 2,
                    "name": "assess",
                    "user_inputs": {
                        "content": "Test",
                        "criteria": "Good",
                        "quality_threshold": 0.7,
                    },
                },
            ],
        }
        resp = requests.post(
            f"{BASE_URL}/api/workflows/custom",
            json=workflow_def,
            headers={"Authorization": "Bearer dev-token-12345"},
            timeout=15,
        )
        if resp.status_code in [200, 201]:
            data = resp.json()
            workflow_id = data.get("id", data.get("workflow_id"))
            print_ok(f"Created workflow: {workflow_id}")
            print(f"      Name: {data.get('name')}")
            print(f"      Phases: {len(data.get('phases', []))}")
        else:
            print_error(f"Failed to create workflow: {resp.status_code}")
            print(f"Response: {resp.text[:300]}")
            return 1
    except Exception as e:
        print_error(f"Request failed: {e}")
        return 1
    print()

    # Test 4: List workflows
    if workflow_id:
        print_test("4", "List Custom Workflows")
        try:
            resp = requests.get(
                f"{BASE_URL}/api/workflows/custom",
                headers={"Authorization": "Bearer dev-token-12345"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("total", 0)
                print_ok(f"Found {total} workflow(s)")
                workflows = data.get("workflows", [])
                if workflows:
                    print(f"      Latest: {workflows[0].get('name')}")
            else:
                print_error(f"Failed to list workflows: {resp.status_code}")
        except Exception as e:
            print_error(f"Request failed: {e}")
        print()

    # Test 5: Get workflow details
    if workflow_id:
        print_test("5", "Get Workflow Details")
        try:
            resp = requests.get(
                f"{BASE_URL}/api/workflows/custom/{workflow_id}",
                headers={"Authorization": "Bearer dev-token-12345"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                print_ok(f"Retrieved workflow: {data.get('name')}")
                print(f"      ID: {data.get('id')}")
                print(f"      Phases: {len(data.get('phases', []))}")
                print(f"      Phase Details:")
                for phase in data.get("phases", [])[:3]:
                    print(f"        - {phase.get('index')}: {phase.get('name')}")
            else:
                print_error(f"Failed to get workflow: {resp.status_code}")
        except Exception as e:
            print_error(f"Request failed: {e}")
        print()

    # Test 6: Execute workflow
    if workflow_id:
        print_test("6", "Execute Workflow")
        try:
            exec_payload = {"initial_inputs": {"user_request": "Test execution"}}
            resp = requests.post(
                f"{BASE_URL}/api/workflows/custom/{workflow_id}/execute",
                json=exec_payload,
                headers={"Authorization": "Bearer dev-token-12345"},
                timeout=30,
            )
            if resp.status_code in [200, 201]:
                data = resp.json()
                execution_id = data.get("execution_id", data.get("id"))
                print_ok(f"Workflow executed: {execution_id}")

                # Show phase results
                results = data.get("phase_results", {})
                print(f"      Phase Results:")
                completed = sum(1 for r in results.values() if r.get("status") == "completed")
                failed = sum(1 for r in results.values() if r.get("status") == "failed")
                print(f"        Completed: {completed}/{len(results)}")
                if failed > 0:
                    print(f"        Failed: {failed}")

                # Show execution summary
                summary = data.get("summary", {})
                if summary:
                    print(f"      Execution Summary:")
                    print(
                        f"        Total Time: {summary.get('total_execution_time_seconds', 0):.4f}s"
                    )
                    print(f"        Status: {summary.get('status')}")
            else:
                print_error(f"Failed to execute workflow: {resp.status_code}")
                print(f"Response: {resp.text[:300]}")
        except Exception as e:
            print_error(f"Request failed: {e}")
        print()

    # Test 7: Clean up (optional delete)
    if workflow_id:
        print_test("7", "Delete Workflow")
        try:
            resp = requests.delete(
                f"{BASE_URL}/api/workflows/custom/{workflow_id}",
                headers={"Authorization": "Bearer dev-token-12345"},
                timeout=15,
            )
            if resp.status_code in [200, 204]:
                print_ok(f"Deleted workflow: {workflow_id}")
            else:
                print_error(f"Failed to delete: {resp.status_code}")
        except Exception as e:
            print_error(f"Request failed: {e}")
        print()

    print_section("API TEST COMPLETE")
    return 0


if __name__ == "__main__":
    exit(main())
