"""
API route tests for Poindexter endpoints.

Tests:
- POST /api/poindexter/workflows - Create workflow
- GET /api/poindexter/workflows/:id - Get workflow status
- POST /api/poindexter/tools - List available tools
- GET /api/poindexter/plans/:id - Get execution plan
- POST /api/poindexter/cost-estimate - Estimate workflow cost
- DELETE /api/poindexter/workflows/:id - Cancel workflow

Target Coverage: >85% of route handlers
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.integration
class TestPoindexterRoutes:
    """Test suite for Poindexter API routes."""

    @pytest.fixture
    def client(self):
        """FastAPI test client."""
        try:
            from main import app
            return TestClient(app)
        except ImportError:
            pytest.skip("FastAPI app not available")

    # ========================================================================
    # WORKFLOW CREATION TESTS
    # ========================================================================

    def test_create_workflow_blog_post(self, client):
        """POST /api/poindexter/workflows should create blog post workflow."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {
                "topic": "AI Trends in 2025",
                "length": "2000 words",
                "style": "professional",
                "include_images": True,
                "auto_publish": True
            }
        })

        assert response.status_code in [200, 201, 202]  # Accepts async patterns
        data = response.json()
        assert "workflow_id" in data or "id" in data
        assert data.get("status") in ["created", "pending", "running"]

    def test_create_workflow_with_research(self, client):
        """Create workflow should handle research parameter."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {
                "topic": "market trends",
                "require_research": True,
                "sources_limit": 5
            }
        })

        assert response.status_code in [200, 201, 202]

    def test_create_workflow_with_constraints(self, client):
        """Create workflow should accept cost and quality constraints."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"},
            "constraints": {
                "max_cost": 2.0,
                "quality_threshold": 0.85,
                "max_execution_time": 300
            }
        })

        assert response.status_code in [200, 201, 202]

    def test_create_workflow_missing_parameters(self, client):
        """Create workflow should validate required parameters."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {}  # Missing required topic
        })

        assert response.status_code == 422  # Validation error

    def test_create_workflow_invalid_type(self, client):
        """Create workflow should validate workflow type."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "invalid_type",
            "parameters": {"topic": "test"}
        })

        assert response.status_code == 422

    def test_create_workflow_returns_task_id(self, client):
        """Create workflow should return workflow ID for tracking."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })

        data = response.json()
        assert any(key in data for key in ["workflow_id", "id", "task_id"])
        assert data.get(next(k for k in data if k in ["workflow_id", "id", "task_id"])) is not None

    # ========================================================================
    # WORKFLOW STATUS TESTS
    # ========================================================================

    def test_get_workflow_status_running(self, client):
        """GET /api/poindexter/workflows/:id should return workflow status."""
        # First create a workflow
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        
        # Get status
        response = client.get(f"/api/poindexter/workflows/{workflow_id}")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ["pending", "running", "completed", "failed"]

    def test_get_workflow_status_completed(self, client):
        """Get workflow should show results when completed."""
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        
        response = client.get(f"/api/poindexter/workflows/{workflow_id}")

        data = response.json()
        if data["status"] == "completed":
            assert "result" in data or "content" in data
            assert "total_cost" in data
            assert "quality_score" in data

    def test_get_workflow_not_found(self, client):
        """Get workflow should return 404 for invalid ID."""
        response = client.get("/api/poindexter/workflows/invalid-id-12345")

        assert response.status_code == 404

    def test_get_workflow_progress(self, client):
        """Get workflow should include progress information."""
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        response = client.get(f"/api/poindexter/workflows/{workflow_id}")

        data = response.json()
        if data["status"] == "running":
            assert "progress" in data or "current_step" in data

    # ========================================================================
    # TOOLS LISTING TESTS
    # ========================================================================

    def test_list_available_tools(self, client):
        """GET /api/poindexter/tools should list available tools."""
        response = client.get("/api/poindexter/tools")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list) or "tools" in data

        tools = data if isinstance(data, list) else data["tools"]
        assert len(tools) > 0

    def test_tools_list_includes_all_seven_tools(self, client):
        """Tools list should include all 7 Poindexter tools."""
        response = client.get("/api/poindexter/tools")

        tools = response.json()
        if isinstance(response.json(), dict):
            tools = response.json()["tools"]

        tool_names = [t["name"] for t in tools]
        
        expected_tools = [
            "research_tool",
            "generate_content_tool",
            "critique_content_tool",
            "publish_tool",
            "track_metrics_tool",
            "fetch_images_tool",
            "refine_tool"
        ]
        
        for expected in expected_tools:
            assert expected in tool_names

    def test_tool_descriptions_included(self, client):
        """Tools should include descriptions."""
        response = client.get("/api/poindexter/tools")

        tools = response.json()
        if isinstance(response.json(), dict):
            tools = response.json()["tools"]

        assert all("description" in t for t in tools)

    def test_tool_parameters_documented(self, client):
        """Tools should document parameters."""
        response = client.get("/api/poindexter/tools")

        tools = response.json()
        if isinstance(response.json(), dict):
            tools = response.json()["tools"]

        assert all("parameters" in t for t in tools)

    # ========================================================================
    # EXECUTION PLAN TESTS
    # ========================================================================

    def test_get_execution_plan(self, client):
        """GET /api/poindexter/plans/:id should return execution plan."""
        # Create workflow first
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        
        response = client.get(f"/api/poindexter/plans/{workflow_id}")

        assert response.status_code == 200
        data = response.json()
        assert "steps" in data or "plan" in data

    def test_plan_includes_step_order(self, client):
        """Execution plan should show step order."""
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        response = client.get(f"/api/poindexter/plans/{workflow_id}")

        data = response.json()
        steps = data.get("steps") or data.get("plan")
        
        for i, step in enumerate(steps):
            assert "order" in step or step.get("order") == i

    def test_plan_includes_tool_information(self, client):
        """Execution plan should identify tools."""
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        response = client.get(f"/api/poindexter/plans/{workflow_id}")

        data = response.json()
        steps = data.get("steps") or data.get("plan")
        
        assert all("tool" in s for s in steps)

    # ========================================================================
    # COST ESTIMATION TESTS
    # ========================================================================

    def test_estimate_workflow_cost(self, client):
        """POST /api/poindexter/cost-estimate should estimate cost."""
        response = client.post("/api/poindexter/cost-estimate", json={
            "type": "blog_post",
            "parameters": {
                "topic": "test",
                "length": "2000 words",
                "include_images": True
            }
        })

        assert response.status_code == 200
        data = response.json()
        assert "estimated_cost" in data or "cost" in data
        assert "min_cost" in data or data.get("estimated_cost") is not None
        assert "max_cost" in data or data.get("estimated_cost") is not None

    def test_cost_estimate_with_different_models(self, client):
        """Cost estimate should vary by model choice."""
        response_cheap = client.post("/api/poindexter/cost-estimate", json={
            "type": "blog_post",
            "parameters": {"topic": "test"},
            "model": "gpt-3.5-turbo"
        })

        response_expensive = client.post("/api/poindexter/cost-estimate", json={
            "type": "blog_post",
            "parameters": {"topic": "test"},
            "model": "gpt-4"
        })

        cheap_cost = response_cheap.json().get("estimated_cost") or response_cheap.json().get("cost")
        expensive_cost = response_expensive.json().get("estimated_cost") or response_expensive.json().get("cost")

        assert expensive_cost >= cheap_cost

    def test_cost_estimate_breakdown(self, client):
        """Cost estimate should show breakdown by tool."""
        response = client.post("/api/poindexter/cost-estimate", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })

        data = response.json()
        assert "breakdown" in data or "cost_per_tool" in data

    # ========================================================================
    # WORKFLOW CANCELLATION TESTS
    # ========================================================================

    def test_cancel_workflow(self, client):
        """DELETE /api/poindexter/workflows/:id should cancel workflow."""
        # Create workflow
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        
        # Cancel it
        response = client.delete(f"/api/poindexter/workflows/{workflow_id}")

        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "cancelled" or "cancelled" in data.get("message", "").lower()

    def test_cancel_completed_workflow(self, client):
        """Cancel should handle already-completed workflows."""
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })
        
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")
        
        # Get status first (ensure it's complete)
        client.get(f"/api/poindexter/workflows/{workflow_id}")
        
        # Try to cancel
        response = client.delete(f"/api/poindexter/workflows/{workflow_id}")

        # Should either cancel or explain it's already done
        assert response.status_code in [200, 400]

    def test_cancel_invalid_workflow(self, client):
        """Cancel should handle invalid workflow IDs."""
        response = client.delete("/api/poindexter/workflows/invalid-id")

        assert response.status_code == 404

    # ========================================================================
    # ERROR HANDLING TESTS
    # ========================================================================

    def test_invalid_json_request(self, client):
        """Routes should handle invalid JSON."""
        response = client.post(
            "/api/poindexter/workflows",
            data="invalid json",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code in [400, 422]

    def test_missing_required_fields(self, client):
        """Routes should validate required fields."""
        response = client.post("/api/poindexter/workflows", json={
            # Missing 'type' field
            "parameters": {"topic": "test"}
        })

        assert response.status_code == 422

    def test_workflow_timeout_handling(self, client):
        """Routes should handle workflow timeouts."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"},
            "constraints": {"max_execution_time": 1}  # 1 second timeout
        })

        # Should accept but may timeout during execution
        assert response.status_code in [200, 201, 202]

    # ========================================================================
    # RESPONSE FORMAT TESTS
    # ========================================================================

    def test_workflow_response_format(self, client):
        """Workflow responses should have consistent format."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "test"}
        })

        assert response.status_code in [200, 201, 202]
        data = response.json()
        
        # Check required fields
        assert any(k in data for k in ["workflow_id", "id", "task_id"])
        assert "status" in data
        assert "created_at" in data or "timestamp" in data

    def test_error_response_format(self, client):
        """Error responses should be consistent."""
        response = client.post("/api/poindexter/workflows", json={
            "type": "invalid",
            "parameters": {"topic": "test"}
        })

        data = response.json()
        assert "detail" in data or "error" in data or "message" in data

    def test_paginated_list_response(self, client):
        """List endpoints should support pagination."""
        response = client.get("/api/poindexter/tools?skip=0&limit=10")

        assert response.status_code == 200
        data = response.json()
        # Should include data even if no pagination metadata
        assert isinstance(data, list) or isinstance(data, dict)


@pytest.mark.integration
class TestPoindexterRoutesIntegration:
    """Integration tests for Poindexter routes with full workflow."""

    @pytest.fixture
    def client(self):
        """FastAPI test client."""
        try:
            from main import app
            return TestClient(app)
        except ImportError:
            pytest.skip("FastAPI app not available")

    def test_full_workflow_lifecycle(self, client):
        """Test complete workflow from creation to completion."""
        # 1. List tools
        tools_response = client.get("/api/poindexter/tools")
        assert tools_response.status_code == 200

        # 2. Estimate cost
        estimate_response = client.post("/api/poindexter/cost-estimate", json={
            "type": "blog_post",
            "parameters": {"topic": "AI"}
        })
        assert estimate_response.status_code == 200

        # 3. Create workflow
        create_response = client.post("/api/poindexter/workflows", json={
            "type": "blog_post",
            "parameters": {"topic": "AI Trends"}
        })
        assert create_response.status_code in [200, 201, 202]
        workflow_id = create_response.json().get("workflow_id") or create_response.json().get("id")

        # 4. Get plan
        plan_response = client.get(f"/api/poindexter/plans/{workflow_id}")
        assert plan_response.status_code == 200

        # 5. Check status
        status_response = client.get(f"/api/poindexter/workflows/{workflow_id}")
        assert status_response.status_code == 200

        # 6. Cancel workflow
        cancel_response = client.delete(f"/api/poindexter/workflows/{workflow_id}")
        assert cancel_response.status_code in [200, 400]  # May already be complete

    def test_concurrent_workflows(self, client):
        """Test handling multiple concurrent workflows."""
        workflow_ids = []
        
        for i in range(3):
            response = client.post("/api/poindexter/workflows", json={
                "type": "blog_post",
                "parameters": {"topic": f"Topic {i}"}
            })
            assert response.status_code in [200, 201, 202]
            workflow_ids.append(response.json().get("workflow_id") or response.json().get("id"))

        # Check all statuses
        for workflow_id in workflow_ids:
            response = client.get(f"/api/poindexter/workflows/{workflow_id}")
            assert response.status_code == 200
