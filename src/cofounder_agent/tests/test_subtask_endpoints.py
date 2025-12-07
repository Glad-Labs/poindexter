"""
Comprehensive tests for Subtask API endpoints

Tests cover:
- Individual subtask execution (research, creative, QA, images, format)
- Task dependency chaining (research → creative → QA)
- Input validation
- Error handling
- Database state verification
- Authentication and authorization

Run with: pytest tests/test_subtask_endpoints.py -v
"""

import pytest
from fastapi.testclient import TestClient
from uuid import uuid4
from typing import Dict, Any


# ============================================================================
# NOTE: All pytest fixtures are defined in conftest.py
# This includes: auth_headers, invalid_auth_headers, client, and all
# sample_*_request fixtures. Do NOT redefine them here.
# ============================================================================

# ============================================================================
# RESEARCH SUBTASK TESTS
# ============================================================================

class TestResearchSubtask:
    """Test suite for research subtask endpoint"""

    def test_research_subtask_success(self, client: TestClient, auth_headers: Dict, sample_research_request: Dict):
        """Test successful research subtask execution"""
        response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json=sample_research_request
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "subtask_id" in data
        assert data["stage"] == "research"
        assert data["status"] == "completed"
        assert "result" in data
        assert "metadata" in data

        # Verify result content
        assert "research_data" in data["result"]
        assert data["result"]["topic"] == sample_research_request["topic"]
        assert data["result"]["keywords"] == sample_research_request["keywords"]

        # Verify metadata
        assert "duration_ms" in data["metadata"]
        assert "tokens_used" in data["metadata"]
        assert "model" in data["metadata"]

    def test_research_missing_topic(self, client: TestClient, auth_headers: Dict):
        """Test validation - missing required topic field"""
        response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json={
                "keywords": ["ML", "AI"]  # Missing 'topic'
            }
        )

        assert response.status_code == 422  # Validation error
        data = response.json()
        assert "detail" in data

    def test_research_empty_keywords(self, client: TestClient, auth_headers: Dict):
        """Test with empty keywords (should succeed, keywords optional)"""
        response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json={
                "topic": "AI trends",
                "keywords": []
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["keywords"] == []

    def test_research_with_parent_task_id(self, client: TestClient, auth_headers: Dict):
        """Test research with parent task ID for chaining"""
        parent_id = str(uuid4())
        response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json={
                "topic": "Test topic",
                "parent_task_id": parent_id
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["parent_task_id"] == parent_id

    def test_research_no_auth_returns_403(self, client: TestClient, sample_research_request: Dict):
        """Test that missing auth header returns 403"""
        response = client.post(
            "/api/content/subtasks/research",
            json=sample_research_request
        )

        # Expecting 403 Forbidden or 401 Unauthorized depending on auth setup
        assert response.status_code in [401, 403]


# ============================================================================
# CREATIVE SUBTASK TESTS
# ============================================================================

class TestCreativeSubtask:
    """Test suite for creative subtask endpoint"""

    def test_creative_subtask_success(self, client: TestClient, auth_headers: Dict, sample_creative_request: Dict):
        """Test successful creative subtask execution"""
        response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json=sample_creative_request
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["stage"] == "creative"
        assert data["status"] == "completed"

        # Verify result contains generated content
        assert "title" in data["result"] or "content" in data["result"]
        assert data["result"]["style"] == sample_creative_request["style"]
        assert data["result"]["tone"] == sample_creative_request["tone"]

    def test_creative_with_research_output(self, client: TestClient, auth_headers: Dict):
        """Test creative stage using research output"""
        response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "topic": "AI trends",
                "research_output": "Key findings: AI market growing 40% YoY...",
                "style": "professional",
                "tone": "persuasive",
                "target_length": 3000
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "content" in data["result"]

    def test_creative_without_research_output(self, client: TestClient, auth_headers: Dict):
        """Test creative stage without research (standalone mode)"""
        response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "topic": "Machine Learning basics",
                "style": "casual",
                "tone": "friendly"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "content" in data["result"]

    def test_creative_missing_topic(self, client: TestClient, auth_headers: Dict):
        """Test validation - missing topic"""
        response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "research_output": "Some research...",
                "style": "professional"
            }
        )

        assert response.status_code == 422

    def test_creative_custom_target_length(self, client: TestClient, auth_headers: Dict):
        """Test creative with custom target length"""
        target_length = 5000
        response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "topic": "Deep learning",
                "target_length": target_length
            }
        )

        assert response.status_code == 200
        data = response.json()
        # Note: Can't verify exact length due to LLM variability
        assert "content" in data["result"]


# ============================================================================
# QA SUBTASK TESTS
# ============================================================================

class TestQASubtask:
    """Test suite for QA subtask endpoint"""

    def test_qa_subtask_success(self, client: TestClient, auth_headers: Dict, sample_qa_request: Dict):
        """Test successful QA subtask execution"""
        response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json=sample_qa_request
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["stage"] == "qa"
        assert data["status"] == "completed"

        # Verify QA-specific result fields
        assert "content" in data["result"]
        assert "feedback" in data["result"]
        assert "quality_score" in data["result"]
        assert isinstance(data["result"]["quality_score"], (int, float))
        assert 0 <= data["result"]["quality_score"] <= 10

    def test_qa_missing_creative_output(self, client: TestClient, auth_headers: Dict):
        """Test validation - missing creative_output"""
        response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json={
                "topic": "Test",
                # Missing creative_output
                "research_output": "Some research"
            }
        )

        assert response.status_code == 422

    def test_qa_with_max_iterations(self, client: TestClient, auth_headers: Dict):
        """Test QA with custom max iterations"""
        response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json={
                "topic": "AI trends",
                "creative_output": "Draft content...",
                "max_iterations": 4
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["iterations"] <= 4

    def test_qa_max_iterations_validation(self, client: TestClient, auth_headers: Dict):
        """Test QA max_iterations bounds (1-5)"""
        # Test below minimum
        response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json={
                "topic": "Test",
                "creative_output": "Content",
                "max_iterations": 0  # Invalid: must be >= 1
            }
        )
        assert response.status_code == 422

        # Test above maximum
        response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json={
                "topic": "Test",
                "creative_output": "Content",
                "max_iterations": 10  # Invalid: must be <= 5
            }
        )
        assert response.status_code == 422

    def test_qa_quality_score_in_metadata(self, client: TestClient, auth_headers: Dict):
        """Test QA returns quality score in metadata"""
        response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json={
                "topic": "Test",
                "creative_output": "Content to review"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "quality_score" in data["metadata"]


# ============================================================================
# IMAGE SUBTASK TESTS
# ============================================================================

class TestImageSubtask:
    """Test suite for image subtask endpoint"""

    def test_image_subtask_success(self, client: TestClient, auth_headers: Dict, sample_image_request: Dict):
        """Test successful image subtask execution"""
        response = client.post(
            "/api/content/subtasks/images",
            headers=auth_headers,
            json=sample_image_request
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["stage"] == "images"
        assert data["status"] == "completed"

        # Verify image-specific result
        assert "featured_image_url" in data["result"]
        assert data["result"]["topic"] == sample_image_request["topic"]
        assert data["result"]["number_requested"] == sample_image_request["number_of_images"]

    def test_image_missing_topic(self, client: TestClient, auth_headers: Dict):
        """Test validation - missing topic"""
        response = client.post(
            "/api/content/subtasks/images",
            headers=auth_headers,
            json={
                "content": "Some article content"
                # Missing topic
            }
        )

        assert response.status_code == 422

    def test_image_with_context(self, client: TestClient, auth_headers: Dict):
        """Test image subtask with article content for context"""
        response = client.post(
            "/api/content/subtasks/images",
            headers=auth_headers,
            json={
                "topic": "Neural networks",
                "content": "Full article about neural networks...",
                "number_of_images": 2
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "featured_image_url" in data["result"]

    def test_image_number_of_images_validation(self, client: TestClient, auth_headers: Dict):
        """Test number_of_images bounds (1-5)"""
        # Test below minimum
        response = client.post(
            "/api/content/subtasks/images",
            headers=auth_headers,
            json={
                "topic": "Test",
                "number_of_images": 0  # Invalid
            }
        )
        assert response.status_code == 422

        # Test above maximum
        response = client.post(
            "/api/content/subtasks/images",
            headers=auth_headers,
            json={
                "topic": "Test",
                "number_of_images": 10  # Invalid
            }
        )
        assert response.status_code == 422

    def test_image_default_number_of_images(self, client: TestClient, auth_headers: Dict):
        """Test default number_of_images is 1"""
        response = client.post(
            "/api/content/subtasks/images",
            headers=auth_headers,
            json={
                "topic": "Test topic"
                # number_of_images not specified, should default to 1
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["number_requested"] == 1


# ============================================================================
# FORMAT SUBTASK TESTS
# ============================================================================

class TestFormatSubtask:
    """Test suite for format subtask endpoint"""

    def test_format_subtask_success(self, client: TestClient, auth_headers: Dict, sample_format_request: Dict):
        """Test successful format subtask execution"""
        response = client.post(
            "/api/content/subtasks/format",
            headers=auth_headers,
            json=sample_format_request
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["stage"] == "format"
        assert data["status"] == "completed"

        # Verify format-specific result
        assert "formatted_content" in data["result"]
        assert "excerpt" in data["result"]
        assert data["result"]["tags"] == sample_format_request["tags"]
        assert data["result"]["category"] == sample_format_request["category"]

    def test_format_missing_content(self, client: TestClient, auth_headers: Dict):
        """Test validation - missing content field"""
        response = client.post(
            "/api/content/subtasks/format",
            headers=auth_headers,
            json={
                "topic": "Test",
                # Missing content
                "tags": ["tag1", "tag2"]
            }
        )

        assert response.status_code == 422

    def test_format_missing_topic(self, client: TestClient, auth_headers: Dict):
        """Test validation - missing topic field"""
        response = client.post(
            "/api/content/subtasks/format",
            headers=auth_headers,
            json={
                "content": "# Content\n\nHere...",
                # Missing topic
                "tags": ["tag1"]
            }
        )

        assert response.status_code == 422

    def test_format_with_image(self, client: TestClient, auth_headers: Dict):
        """Test format with featured image"""
        response = client.post(
            "/api/content/subtasks/format",
            headers=auth_headers,
            json={
                "topic": "Test topic",
                "content": "Content here...",
                "featured_image_url": "https://example.com/image.jpg"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "formatted_content" in data["result"]

    def test_format_with_tags_and_category(self, client: TestClient, auth_headers: Dict):
        """Test format with tags and category"""
        tags = ["AI", "tech", "innovation"]
        category = "technology"
        response = client.post(
            "/api/content/subtasks/format",
            headers=auth_headers,
            json={
                "topic": "AI trends",
                "content": "Article content...",
                "tags": tags,
                "category": category
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["tags"] == tags
        assert data["result"]["category"] == category

    def test_format_empty_tags(self, client: TestClient, auth_headers: Dict):
        """Test format with empty tags (should be allowed)"""
        response = client.post(
            "/api/content/subtasks/format",
            headers=auth_headers,
            json={
                "topic": "Test",
                "content": "Content",
                "tags": []
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["tags"] == []


# ============================================================================
# TASK CHAINING / DEPENDENCY TESTS
# ============================================================================

class TestSubtaskChaining:
    """Test suite for chaining subtasks together"""

    def test_research_to_creative_chaining(self, client: TestClient, auth_headers: Dict):
        """Test chaining research → creative with output dependency"""
        
        # Step 1: Run research
        research_response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json={
                "topic": "Quantum computing",
                "keywords": ["quantum", "computing", "algorithms"]
            }
        )
        assert research_response.status_code == 200
        research_data = research_response.json()
        research_id = research_data["subtask_id"]
        research_output = research_data["result"]["research_data"]

        # Step 2: Use research output in creative
        creative_response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "topic": "Quantum computing",
                "research_output": research_output,
                "style": "professional",
                "parent_task_id": research_id
            }
        )
        assert creative_response.status_code == 200
        creative_data = creative_response.json()

        # Verify chaining
        assert creative_data["parent_task_id"] == research_id
        assert "content" in creative_data["result"]

    def test_creative_to_qa_chaining(self, client: TestClient, auth_headers: Dict):
        """Test chaining creative → QA"""
        
        # Step 1: Create sample draft
        creative_response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "topic": "Blockchain basics",
                "style": "casual",
                "target_length": 2000
            }
        )
        assert creative_response.status_code == 200
        creative_data = creative_response.json()
        creative_id = creative_data["subtask_id"]
        creative_output = creative_data["result"]["content"]

        # Step 2: Run QA on creative output
        qa_response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json={
                "topic": "Blockchain basics",
                "creative_output": creative_output,
                "max_iterations": 2,
                "parent_task_id": creative_id
            }
        )
        assert qa_response.status_code == 200
        qa_data = qa_response.json()

        # Verify chaining and QA results
        assert qa_data["parent_task_id"] == creative_id
        assert "quality_score" in qa_data["result"]

    def test_full_pipeline_chaining(self, client: TestClient, auth_headers: Dict):
        """Test full pipeline: research → creative → QA → format"""
        
        # 1. Research
        research_response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json={"topic": "Web3", "keywords": ["blockchain", "decentralized"]}
        )
        assert research_response.status_code == 200
        research_data = research_response.json()
        research_output = research_data["result"]["research_data"]

        # 2. Creative with research
        creative_response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "topic": "Web3",
                "research_output": research_output,
                "style": "professional"
            }
        )
        assert creative_response.status_code == 200
        creative_data = creative_response.json()
        creative_output = creative_data["result"]["content"]

        # 3. QA with creative
        qa_response = client.post(
            "/api/content/subtasks/qa",
            headers=auth_headers,
            json={
                "topic": "Web3",
                "creative_output": creative_output,
                "max_iterations": 2
            }
        )
        assert qa_response.status_code == 200
        qa_data = qa_response.json()
        final_content = qa_data["result"]["content"]

        # 4. Format with QA output
        format_response = client.post(
            "/api/content/subtasks/format",
            headers=auth_headers,
            json={
                "topic": "Web3",
                "content": final_content,
                "tags": ["web3", "blockchain"],
                "category": "technology"
            }
        )
        assert format_response.status_code == 200
        format_data = format_response.json()

        # Verify full pipeline succeeded
        assert "formatted_content" in format_data["result"]
        assert format_data["result"]["tags"] == ["web3", "blockchain"]


# ============================================================================
# RESPONSE STRUCTURE AND METADATA TESTS
# ============================================================================

class TestSubtaskResponseStructure:
    """Test consistency of subtask response structure"""

    def test_all_subtasks_return_standard_response(self, client: TestClient, auth_headers: Dict):
        """Test that all subtask endpoints return consistent response structure"""
        
        requests = [
            ("research", {"topic": "Test"}),
            ("creative", {"topic": "Test", "style": "professional"}),
            ("qa", {"topic": "Test", "creative_output": "Content"}),
            ("images", {"topic": "Test"}),
            ("format", {"topic": "Test", "content": "Content"})
        ]

        for stage, request_body in requests:
            response = client.post(
                f"/api/content/subtasks/{stage}",
                headers=auth_headers,
                json=request_body
            )

            assert response.status_code == 200, f"Failed for {stage} subtask"
            data = response.json()

            # Verify standard response fields
            assert "subtask_id" in data
            assert data["stage"] == stage
            assert "status" in data
            assert data["status"] in ["completed", "pending", "failed"]
            assert "result" in data
            assert isinstance(data["result"], dict)
            assert "metadata" in data
            assert isinstance(data["metadata"], dict)

            # Verify metadata fields
            assert "duration_ms" in data["metadata"]
            assert "tokens_used" in data["metadata"]
            assert "model" in data["metadata"]

    def test_subtask_id_is_unique(self, client: TestClient, auth_headers: Dict):
        """Test that each subtask gets a unique ID"""
        
        subtask_ids = set()
        
        for i in range(3):
            response = client.post(
                "/api/content/subtasks/research",
                headers=auth_headers,
                json={"topic": f"Topic {i}"}
            )
            assert response.status_code == 200
            subtask_id = response.json()["subtask_id"]
            subtask_ids.add(subtask_id)

        # Verify all IDs are unique
        assert len(subtask_ids) == 3


# ============================================================================
# ERROR HANDLING AND EDGE CASES
# ============================================================================

class TestSubtaskErrorHandling:
    """Test error handling and edge cases"""

    def test_empty_json_body(self, client: TestClient, auth_headers: Dict):
        """Test sending empty JSON object"""
        response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json={}
        )

        # Should fail validation
        assert response.status_code == 422

    def test_malformed_json(self, client: TestClient, auth_headers: Dict):
        """Test with malformed JSON"""
        response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            data="{ invalid json }"
        )

        # Should fail with 422 or 400
        assert response.status_code in [400, 422]

    def test_extra_fields_in_request(self, client: TestClient, auth_headers: Dict):
        """Test with extra unknown fields (should be ignored)"""
        response = client.post(
            "/api/content/subtasks/research",
            headers=auth_headers,
            json={
                "topic": "Test",
                "unknown_field": "should be ignored",
                "another_extra": 123
            }
        )

        # Should succeed (extra fields ignored by Pydantic)
        assert response.status_code == 200

    def test_null_values_for_optional_fields(self, client: TestClient, auth_headers: Dict):
        """Test with null values for optional fields"""
        response = client.post(
            "/api/content/subtasks/creative",
            headers=auth_headers,
            json={
                "topic": "Test",
                "research_output": None,
                "style": None,
                "tone": None,
                "target_length": None
            }
        )

        # Should use defaults for None values
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["style"] is not None  # Should have default


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
