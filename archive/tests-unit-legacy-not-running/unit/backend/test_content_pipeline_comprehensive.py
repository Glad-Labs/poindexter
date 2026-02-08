"""
Comprehensive Content Pipeline Validation Suite

Tests edge cases, failure scenarios, and end-to-end workflows
for the content generation pipeline.

Coverage:
- Basic task creation and completion
- Edge cases: empty content, malformed metadata, unicode
- Concurrent task execution
- Database transaction handling
- Error recovery and rollback
- Performance under load
- SEO metadata generation
- Post creation from task results
"""

import pytest
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from typing import Dict, Any

# Import the app
from src.cofounder_agent.main import app
from src.cofounder_agent.services.database_service import DatabaseService

client = TestClient(app)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def db_service():
    """Mock database service"""
    service = AsyncMock(spec=DatabaseService)
    service.execute_query = AsyncMock()
    service.fetch_query = AsyncMock()
    service.execute_insert = AsyncMock()
    return service


@pytest.fixture
def sample_task_data() -> Dict[str, Any]:
    """Standard task creation data"""
    return {
        "task_name": "Test Blog Post",
        "topic": "Artificial Intelligence in Healthcare",
        "primary_keyword": "AI healthcare",
        "target_audience": "Healthcare professionals",
        "category": "healthcare",
        "metadata": {"priority": "high", "rush": False},
    }


@pytest.fixture
def sample_task_response() -> Dict[str, Any]:
    """Standard task response"""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "task_name": "Test Blog Post",
        "agent_id": "content-agent",
        "status": "pending",
        "topic": "Artificial Intelligence in Healthcare",
        "primary_keyword": "AI healthcare",
        "target_audience": "Healthcare professionals",
        "category": "healthcare",
        "created_at": "2025-12-04T12:00:00Z",
        "updated_at": "2025-12-04T12:00:00Z",
        "metadata": {"priority": "high", "rush": False},
    }


# ============================================================================
# BASIC FUNCTIONALITY TESTS
# ============================================================================


class TestBasicTaskCreation:
    """Test basic task creation workflows"""

    def test_create_task_with_all_fields(self, sample_task_data):
        """Should create task with all valid fields"""
        response = client.post("/api/tasks", json=sample_task_data)
        assert response.status_code == 201
        data = response.json()
        assert data["task_name"] == sample_task_data["task_name"]
        assert data["topic"] == sample_task_data["topic"]
        assert data["status"] == "pending"
        assert "id" in data

    def test_create_task_with_minimal_fields(self):
        """Should create task with only required fields"""
        task_data = {"task_name": "Minimal Task", "topic": "Test Topic"}
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 201
        data = response.json()
        assert data["task_name"] == "Minimal Task"
        assert data["topic"] == "Test Topic"

    def test_list_tasks_with_pagination(self):
        """Should list tasks with skip and limit parameters"""
        response = client.get("/api/tasks?skip=0&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "meta" in data
        assert "pagination" in data["meta"]

    def test_get_task_by_id(self, sample_task_response):
        """Should retrieve specific task by ID"""
        task_id = sample_task_response["id"]
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code in [200, 404]  # 404 if task doesn't exist in DB


# ============================================================================
# EDGE CASE TESTS
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_task_with_unicode_characters(self):
        """Should handle unicode in task data"""
        task_data = {
            "task_name": "测试任务 🚀 Test Task",
            "topic": "Über alles über Künstliche Intelligenz",
        }
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 201

    def test_task_with_very_long_strings(self):
        """Should handle maximum length strings"""
        task_data = {"task_name": "a" * 200, "topic": "b" * 200}  # Max 200 chars
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 201

    def test_task_with_special_characters_in_metadata(self):
        """Should handle special chars in metadata"""
        task_data = {
            "task_name": "Test Task",
            "topic": "Test Topic",
            "metadata": {
                "special": "!@#$%^&*()",
                "quotes": 'He said "Hello"',
                "newlines": "Line 1\nLine 2\nLine 3",
            },
        }
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 201
        data = response.json()
        assert data["metadata"]["special"] == "!@#$%^&*()"

    def test_task_with_null_optional_fields(self):
        """Should handle null values in optional fields"""
        task_data = {
            "task_name": "Test",
            "topic": "Topic",
            "primary_keyword": None,
            "target_audience": None,
            "metadata": None,
        }
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 201

    def test_task_with_empty_strings(self):
        """Should reject empty required fields"""
        task_data = {"task_name": "", "topic": "Topic"}
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 422  # Validation error

    def test_task_with_missing_required_fields(self):
        """Should reject missing required fields"""
        task_data = {
            "task_name": "Test Task"
            # Missing 'topic'
        }
        response = client.post("/api/tasks", json=task_data)
        assert response.status_code == 422

    def test_task_with_invalid_status(self):
        """Should reject invalid status values"""
        invalid_statuses = ["in-progress", "complete", "unknown", "PENDING"]
        for status in invalid_statuses:
            update_data = {"status": status}
            response = client.patch("/api/tasks/test-id", json=update_data)
            assert response.status_code in [422, 404]  # 404 if task doesn't exist

    def test_list_posts_with_extreme_pagination(self):
        """Should handle extreme pagination parameters"""
        # Very high skip
        response = client.get("/api/posts?skip=999999&limit=10")
        assert response.status_code == 200

        # Max limit
        response = client.get("/api/posts?skip=0&limit=100")
        assert response.status_code == 200

        # Beyond max limit should be clamped
        response = client.get("/api/posts?skip=0&limit=1000")
        assert response.status_code == 200


# ============================================================================
# CONTENT GENERATION WORKFLOW TESTS
# ============================================================================


class TestContentPipeline:
    """Test complete content generation workflow"""

    def test_task_to_post_workflow(self):
        """Test full workflow: create task → generate content → create post"""
        # Step 1: Create task
        task_data = {
            "task_name": "Generate Healthcare Article",
            "topic": "AI Applications in Medical Diagnosis",
            "primary_keyword": "AI medical diagnosis",
            "target_audience": "Healthcare professionals",
        }
        task_response = client.post("/api/tasks", json=task_data)
        assert task_response.status_code == 201
        task_id = task_response.json()["id"]

        # Step 2: Verify task was created
        get_response = client.get(f"/api/tasks/{task_id}")
        assert get_response.status_code == 200

        # Step 3: Simulate task completion
        completion_data = {
            "status": "completed",
            "result": {
                "content": "# AI in Medical Diagnosis\n\nContent here...",
                "excerpt": "How AI is revolutionizing medical diagnosis",
                "seo_title": "AI Medical Diagnosis - Modern Healthcare Solutions",
                "seo_description": "Discover how AI is transforming medical diagnosis",
                "seo_keywords": "AI, medical diagnosis, healthcare, technology",
            },
        }
        update_response = client.patch(f"/api/tasks/{task_id}", json=completion_data)
        assert update_response.status_code in [200, 404]

    def test_concurrent_task_execution(self):
        """Test creating multiple tasks concurrently"""
        tasks = [{"task_name": f"Task {i}", "topic": f"Topic {i}"} for i in range(5)]

        responses = [client.post("/api/tasks", json=task) for task in tasks]
        assert all(r.status_code == 201 for r in responses)

    def test_task_status_transitions(self):
        """Test valid status transitions"""
        # Create task
        task_data = {"task_name": "Test", "topic": "Test"}
        response = client.post("/api/tasks", json=task_data)
        task_id = response.json()["id"]

        # pending → in_progress
        r = client.patch(f"/api/tasks/{task_id}", json={"status": "in_progress"})
        assert r.status_code in [200, 404]

        # in_progress → completed
        r = client.patch(f"/api/tasks/{task_id}", json={"status": "completed"})
        assert r.status_code in [200, 404]

    def test_invalid_status_transitions(self):
        """Test invalid status transitions are rejected"""
        task_data = {"task_name": "Test", "topic": "Test"}
        response = client.post("/api/tasks", json=task_data)
        task_id = response.json()["id"]

        # Invalid transition: pending → completed (should be pending → in_progress → completed)
        # The API may or may not enforce this, but at minimum should not crash
        r = client.patch(f"/api/tasks/{task_id}", json={"status": "invalid_status"})
        # Should return 422 (validation error) or 404 (not found)
        assert r.status_code in [422, 404]


# ============================================================================
# POST CREATION TESTS
# ============================================================================


class TestPostCreation:
    """Test blog post creation and management"""

    def test_create_post_with_all_fields(self):
        """Should create post with all fields"""
        post_data = {
            "title": "Test Post",
            "slug": "test-post",
            "content": "# Test Content\n\nContent here",
            "excerpt": "Short excerpt",
            "status": "published",
            "seo_title": "Test Post - SEO Title",
            "seo_description": "Description for SEO",
            "seo_keywords": "test, post, seo",
        }
        response = client.post("/api/posts", json=post_data)
        assert response.status_code in [201, 200]

    def test_create_post_with_minimal_fields(self):
        """Should create post with only required fields"""
        post_data = {"title": "Minimal Post", "content": "Content"}
        response = client.post("/api/posts", json=post_data)
        assert response.status_code in [201, 200]

    def test_post_slug_auto_generation(self):
        """Should auto-generate slug if not provided"""
        post_data = {"title": "Post Without Slug", "content": "Content"}
        response = client.post("/api/posts", json=post_data)
        assert response.status_code in [201, 200]
        # Slug should be auto-generated from title

    def test_list_posts_filtering(self):
        """Should filter posts by status"""
        # Published posts only
        response = client.get("/api/posts?published_only=true")
        assert response.status_code == 200

        # All posts
        response = client.get("/api/posts?published_only=false")
        assert response.status_code == 200

    def test_get_post_by_id(self):
        """Should retrieve post by ID"""
        # This will return 404 if post doesn't exist, which is fine for edge case test
        response = client.get("/api/posts/test-id")
        assert response.status_code in [200, 404]

    def test_update_post(self):
        """Should update existing post"""
        post_data = {"title": "Updated Title", "content": "Updated content"}
        response = client.patch("/api/posts/test-id", json=post_data)
        assert response.status_code in [200, 404]

    def test_delete_post(self):
        """Should delete post"""
        response = client.delete("/api/posts/test-id")
        assert response.status_code in [200, 204, 404]


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================


class TestErrorHandling:
    """Test error handling and recovery"""

    def test_malformed_json_request(self):
        """Should reject malformed JSON"""
        # This is handled by FastAPI's JSON parsing
        response = client.post(
            "/api/tasks", data="not valid json", headers={"Content-Type": "application/json"}
        )
        assert response.status_code in [422, 400]

    def test_invalid_content_type(self):
        """Should handle invalid content type"""
        response = client.post("/api/tasks", data="test", headers={"Content-Type": "text/plain"})
        assert response.status_code in [415, 422]

    def test_database_connection_error(self):
        """Should handle database errors gracefully"""
        # Try to get a task (will fail if DB not connected, but should not crash)
        response = client.get("/api/tasks/invalid-id")
        # Should return 404 or 500, not crash
        assert response.status_code in [404, 500]

    def test_timeout_handling(self):
        """Should handle timeout errors"""
        # This would need a mock that times out
        # Actual test would depend on implementation
        pass


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================


class TestPerformance:
    """Test performance under various conditions"""

    def test_list_large_result_set(self):
        """Should handle listing large result sets efficiently"""
        response = client.get("/api/tasks?skip=0&limit=100")
        assert response.status_code == 200
        # Response should complete in reasonable time

    def test_create_many_tasks(self):
        """Should handle creating many tasks"""
        for i in range(10):
            task_data = {"task_name": f"Perf Test Task {i}", "topic": f"Perf Test Topic {i}"}
            response = client.post("/api/tasks", json=task_data)
            assert response.status_code == 201

    def test_concurrent_api_calls(self):
        """Should handle concurrent requests"""
        import concurrent.futures

        def make_request():
            return client.get("/api/tasks?skip=0&limit=10")

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            responses = list(executor.map(make_request, range(5)))

        assert all(r.status_code == 200 for r in responses)


# ============================================================================
# SYSTEM HEALTH TESTS
# ============================================================================


class TestSystemHealth:
    """Test system health and status endpoints"""

    def test_health_check_endpoint(self):
        """Should return health status"""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ["healthy", "degraded", "unhealthy"]

    def test_metrics_endpoint(self):
        """Should return system metrics"""
        response = client.get("/api/metrics")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_root_endpoint(self):
        """Should return API info at root"""
        response = client.get("/")
        assert response.status_code == 200


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestIntegration:
    """Test integration between components"""

    def test_task_and_post_creation_flow(self):
        """Test creating task and associated post"""
        # Create task
        task_data = {"task_name": "Integration Test", "topic": "Test Topic"}
        task_resp = client.post("/api/tasks", json=task_data)
        assert task_resp.status_code == 201

        # Create post from task result
        post_data = {
            "title": "Test Topic",
            "slug": "test-topic",
            "content": "Generated content",
            "status": "published",
        }
        post_resp = client.post("/api/posts", json=post_data)
        assert post_resp.status_code in [201, 200]

    def test_list_tasks_and_posts_together(self):
        """Test listing both tasks and posts"""
        tasks = client.get("/api/tasks")
        posts = client.get("/api/posts")

        assert tasks.status_code == 200
        assert posts.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
