#!/usr/bin/env python3
"""
Phase 4 Endpoint Verification Script
Comprehensive API endpoint testing for 50+ Phase 4 endpoints
"""

import requests
import json
import sys
from typing import Optional, Dict, Any
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"
TIMEOUT = 10  # seconds
HEADERS = {"Content-Type": "application/json"}

# Test counters
total_tests = 0
passed_tests = 0
failed_tests = 0

# Color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_success(msg: str):
    print(f"{GREEN}✅ {msg}{RESET}")


def print_error(msg: str):
    print(f"{RED}❌ {msg}{RESET}")


def print_warning(msg: str):
    print(f"{YELLOW}⚠️  {msg}{RESET}")


def print_info(msg: str):
    print(f"{CYAN}ℹ️  {msg}{RESET}")


def print_header(title: str):
    print(f"\n{BOLD}{'━' * 70}{RESET}")
    print(f"{BOLD}{title}{RESET}")
    print(f"{BOLD}{'━' * 70}{RESET}\n")


def test_endpoint(
    method: str,
    path: str,
    description: str,
    body: Optional[Dict] = None,
    expected_status: int = 200,
) -> Optional[Dict[str, Any]]:
    """Test an API endpoint and return response JSON if successful"""
    global total_tests, passed_tests, failed_tests

    total_tests += 1
    full_url = f"{BASE_URL}{path}"

    try:
        if method == "GET":
            response = requests.get(full_url, headers=HEADERS, timeout=TIMEOUT)
        elif method == "POST":
            response = requests.post(
                full_url, json=body, headers=HEADERS, timeout=TIMEOUT
            )
        else:
            print_error(f"{method} {path} - Unsupported HTTP method")
            failed_tests += 1
            return None

        if response.status_code == expected_status:
            print_success(f"{method} {path} - {description}")
            passed_tests += 1
            try:
                return response.json()
            except:
                return None
        else:
            print_error(
                f"{method} {path} - Expected {expected_status}, got {response.status_code}"
            )
            failed_tests += 1
            return None

    except requests.exceptions.Timeout:
        print_error(f"{method} {path} - Timeout ({TIMEOUT}s)")
        failed_tests += 1
    except requests.exceptions.ConnectionError:
        print_error(f"{method} {path} - Connection failed (backend not running?)")
        failed_tests += 1
    except Exception as e:
        print_error(f"{method} {path} - ERROR: {str(e)}")
        failed_tests += 1

    return None


def main():
    print(f"\n{BOLD}{'═' * 70}{RESET}")
    print(f"{BOLD}  Phase 4 Endpoint Verification - Comprehensive Test Suite{RESET}")
    print(f"{BOLD}{'═' * 70}{RESET}\n")

    # ========================================================================
    # SECTION 1: CORE HEALTH & STATUS ENDPOINTS (5 endpoints)
    # ========================================================================

    print_header("SECTION 1: Core Health & Status Endpoints (5)")

    print_info("1.1 Health Check")
    test_endpoint("GET", "/health", "System health status")

    print_info("1.2 Status")
    test_endpoint("GET", "/status", "Detailed system status", expected_status=200)

    print_info("1.3 Version")
    test_endpoint("GET", "/version", "API version", expected_status=200)

    print_info("1.4 Metrics")
    test_endpoint("GET", "/metrics", "System metrics", expected_status=200)

    print_info("1.5 Ready Check")
    test_endpoint("GET", "/ready", "Readiness probe", expected_status=200)

    # ========================================================================
    # SECTION 2: AGENT REGISTRY ENDPOINTS (11 endpoints)
    # ========================================================================

    print_header("SECTION 2: Agent Registry Endpoints (11)")

    print_info("2.1 List All Agents")
    agent_list = test_endpoint(
        "GET", "/api/agents/list", "Get all agent names"
    )

    print_info("2.2 Get Agent Registry")
    agent_registry = test_endpoint(
        "GET", "/api/agents/registry", "Full agent registry with metadata"
    )

    # Extract service names from registry if available
    services = []
    if agent_registry and "agents" in agent_registry:
        services = [agent.get("name") for agent in agent_registry["agents"]]

    print_info("2.3 Get Specific Agent (content_service)")
    test_endpoint(
        "GET",
        "/api/agents/content_service",
        "Get content_service metadata",
        expected_status=200,
    )

    print_info("2.4 Get Agent Phases (content_service)")
    test_endpoint(
        "GET",
        "/api/agents/content_service/phases",
        "Get phases for content_service",
        expected_status=200,
    )

    print_info("2.5 Get Agent Capabilities (content_service)")
    test_endpoint(
        "GET",
        "/api/agents/content_service/capabilities",
        "Get capabilities for content_service",
        expected_status=200,
    )

    print_info("2.6 Get Agents by Phase (research)")
    test_endpoint(
        "GET",
        "/api/agents/by-phase/research",
        "Get agents handling research phase",
        expected_status=200,
    )

    print_info("2.7 Get Agents by Capability (content_generation)")
    test_endpoint(
        "GET",
        "/api/agents/by-capability/content_generation",
        "Get agents with content_generation capability",
        expected_status=200,
    )

    print_info("2.8 Get Agents by Category (content)")
    test_endpoint(
        "GET",
        "/api/agents/by-category/content",
        "Get all content agents",
        expected_status=200,
    )

    print_info("2.9 Search Agents (phase=draft&category=content)")
    test_endpoint(
        "GET",
        "/api/agents/search?phase=draft&category=content",
        "Search agents by filters",
        expected_status=200,
    )

    print_info("2.10 List agents (financial category)")
    test_endpoint(
        "GET",
        "/api/agents/by-category/financial",
        "Get financial agents",
        expected_status=200,
    )

    print_info("2.11 List agents (compliance category)")
    test_endpoint(
        "GET",
        "/api/agents/by-category/compliance",
        "Get compliance agents",
        expected_status=200,
    )

    # ========================================================================
    # SECTION 3: SERVICE REGISTRY ENDPOINTS (7 endpoints)
    # ========================================================================

    print_header("SECTION 3: Service Registry Endpoints (7)")

    print_info("3.1 Get Service Registry")
    test_endpoint(
        "GET", "/api/services/registry", "Full service registry schema"
    )

    print_info("3.2 List Services")
    test_endpoint("GET", "/api/services/list", "Get all service names")

    print_info("3.3 Get Service Metadata (content_service)")
    test_endpoint(
        "GET",
        "/api/services/content_service",
        "Get content_service metadata",
        expected_status=200,
    )

    print_info("3.4 Get Service Actions (content_service)")
    test_endpoint(
        "GET",
        "/api/services/content_service/actions",
        "Get actions for content_service",
        expected_status=200,
    )

    print_info("3.5 Get Action Details")
    test_endpoint(
        "GET",
        "/api/services/content_service/actions/generate_content",
        "Get specific action details",
        expected_status=200,
    )

    print_info("3.6 List financial service")
    test_endpoint(
        "GET",
        "/api/services/financial_service",
        "Get financial_service metadata",
        expected_status=200,
    )

    print_info("3.7 List compliance service")
    test_endpoint(
        "GET",
        "/api/services/compliance_service",
        "Get compliance_service metadata",
        expected_status=200,
    )

    # ========================================================================
    # SECTION 4: TASK ENDPOINTS (8 endpoints)
    # ========================================================================

    print_header("SECTION 4: Task Management Endpoints (8)")

    print_info("4.1 List Tasks")
    test_endpoint("GET", "/api/tasks", "Get all tasks", expected_status=200)

    print_info("4.2 List Tasks with limit")
    test_endpoint("GET", "/api/tasks?limit=10", "Get tasks with pagination", expected_status=200)

    print_info("4.3 Create Task")
    task_body = {
        "title": "Test Task",
        "description": "Phase 4 verification task",
        "task_type": "content_generation",
    }
    test_endpoint("POST", "/api/tasks", "Create new task", body=task_body, expected_status=201)

    print_info("4.4 Get Task Status")
    test_endpoint("GET", "/api/tasks?status=pending", "Get pending tasks", expected_status=200)

    print_info("4.5 Get Recent Tasks")
    test_endpoint(
        "GET",
        "/api/tasks?status=completed&limit=5",
        "Get completed tasks",
        expected_status=200,
    )

    print_info("4.6 Task History")
    test_endpoint("GET", "/api/tasks/history", "Get task history", expected_status=200)

    print_info("4.7 Task Statistics")
    test_endpoint("GET", "/api/tasks/stats", "Get task statistics", expected_status=200)

    print_info("4.8 Task Filters (by created_by)")
    test_endpoint(
        "GET", "/api/tasks?created_by=system", "Filter tasks by creator", expected_status=200
    )

    # ========================================================================
    # SECTION 5: WORKFLOW ENDPOINTS (5 endpoints)
    # ========================================================================

    print_header("SECTION 5: Workflow Endpoints (5)")

    print_info("5.1 List Workflow Templates")
    test_endpoint(
        "GET", "/api/workflows", "Get available workflow templates", expected_status=200
    )

    print_info("5.2 Get Workflow Templates")
    test_endpoint("GET", "/api/workflows/templates", "Get workflow templates", expected_status=200)

    print_info("5.3 Get Workflow History")
    test_endpoint("GET", "/api/workflows/history", "Get workflow execution history", expected_status=200)

    print_info("5.4 Get Running Workflows")
    test_endpoint(
        "GET", "/api/workflows/running", "Get active workflows", expected_status=200
    )

    print_info("5.5 Workflow Statistics")
    test_endpoint("GET", "/api/workflows/stats", "Get workflow statistics", expected_status=200)

    # ========================================================================
    # SECTION 6: MODEL & LLM ENDPOINTS (6 endpoints)
    # ========================================================================

    print_header("SECTION 6: Model & LLM Endpoints (6)")

    print_info("6.1 List Available Models")
    test_endpoint("GET", "/api/models", "Get available LLM models", expected_status=200)

    print_info("6.2 Get Model Health")
    test_endpoint("GET", "/api/models/health", "Check model provider health", expected_status=200)

    print_info("6.3 Ollama Models")
    test_endpoint("GET", "/api/models/ollama", "Get Ollama models", expected_status=200)

    print_info("6.4 OpenAI Models")
    test_endpoint("GET", "/api/models/openai", "Get OpenAI models", expected_status=200)

    print_info("6.5 Model Configuration")
    test_endpoint("GET", "/api/models/config", "Get model configuration", expected_status=200)

    print_info("6.6 Model Routing Info")
    test_endpoint(
        "GET", "/api/models/routing", "Get model routing configuration", expected_status=200
    )

    # ========================================================================
    # SECTION 7: ANALYTICS & MONITORING (8 endpoints)
    # ========================================================================

    print_header("SECTION 7: Analytics & Monitoring (8)")

    print_info("7.1 System Analytics")
    test_endpoint("GET", "/api/analytics", "Get system analytics", expected_status=200)

    print_info("7.2 Agent Analytics")
    test_endpoint("GET", "/api/analytics/agents", "Get agent analytics", expected_status=200)

    print_info("7.3 Content Analytics")
    test_endpoint("GET", "/api/analytics/content", "Get content analytics", expected_status=200)

    print_info("7.4 Cost Analytics")
    test_endpoint("GET", "/api/analytics/costs", "Get cost tracking data", expected_status=200)

    print_info("7.5 Performance Analytics")
    test_endpoint("GET", "/api/analytics/performance", "Get performance metrics", expected_status=200)

    print_info("7.6 Usage Analytics")
    test_endpoint("GET", "/api/analytics/usage", "Get usage statistics", expected_status=200)

    print_info("7.7 Error Analytics")
    test_endpoint("GET", "/api/analytics/errors", "Get error tracking", expected_status=200)

    print_info("7.8 Dashboard Data")
    test_endpoint("GET", "/api/analytics/dashboard", "Get dashboard data", expected_status=200)

    # ========================================================================
    # SUMMARY
    # ========================================================================

    print_header("TEST SUMMARY")

    success_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0

    print(f"Total Tests:    {total_tests}")
    print_success(f"Passed:         {passed_tests}")
    print_error(f"Failed:         {failed_tests}")
    print(f"Success Rate:   {success_rate:.1f}%\n")

    if passed_tests == total_tests:
        print_success("All Phase 4 endpoints are operational! ✨\n")
        return 0
    else:
        print_warning("Some endpoints failed - review output above\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
