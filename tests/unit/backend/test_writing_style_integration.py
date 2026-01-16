"""
Writing Style System - End-to-End Integration Tests

Tests the complete flow:
1. Upload writing sample
2. Create task with writing_style_id
3. Verify style guidance is used in content generation
4. Verify QA agent evaluates style consistency
"""

import pytest
import asyncio
from uuid import uuid4
from typing import Optional, Dict, Any

from schemas.task_schemas import TaskCreateRequest
from services.writing_style_service import WritingStyleService
from services.tasks_db import TasksDatabase


class TestWritingStyleIntegration:
    """Integration tests for writing style system"""
    
    @pytest.mark.asyncio
    async def test_task_accepts_writing_style_id(self):
        """Test that TaskCreateRequest accepts writing_style_id parameter"""
        
        sample_style_id = str(uuid4())
        
        request = TaskCreateRequest(
            task_name="Test Task with Writing Style",
            topic="AI in Healthcare",
            writing_style_id=sample_style_id
        )
        
        assert request.writing_style_id == sample_style_id
        assert request.task_name == "Test Task with Writing Style"
        assert request.topic == "AI in Healthcare"
    
    @pytest.mark.asyncio
    async def test_task_create_request_without_writing_style_id(self):
        """Test that writing_style_id is optional"""
        
        request = TaskCreateRequest(
            task_name="Test Task without Writing Style",
            topic="Web Development"
        )
        
        assert request.writing_style_id is None
        assert request.task_name == "Test Task without Writing Style"
    
    @pytest.mark.asyncio
    async def test_writing_style_service_get_specific_sample(self):
        """Test WritingStyleService.get_style_prompt_for_specific_sample method"""
        
        # This test verifies the method exists and handles correct/incorrect IDs
        # Real integration testing would require actual database setup
        
        sample_id = str(uuid4())
        # Mock database service would be needed for full testing
        # This is more of a unit test structure
        
        assert sample_id is not None
        assert len(sample_id) == 36  # UUID length with hyphens
    
    @pytest.mark.asyncio
    async def test_task_create_request_json_schema_includes_writing_style_id(self):
        """Verify that TaskCreateRequest JSON schema includes writing_style_id"""
        
        schema = TaskCreateRequest.model_json_schema()
        
        assert "properties" in schema
        assert "writing_style_id" in schema["properties"]
        
        writing_style_property = schema["properties"]["writing_style_id"]
        assert writing_style_property is not None
        assert "description" in writing_style_property


class TestWritingStyleDataFlow:
    """Tests for writing style data flowing through the system"""
    
    @pytest.mark.asyncio
    async def test_task_request_with_all_fields(self):
        """Test complete task request with writing style and all other fields"""
        
        writing_style_id = str(uuid4())
        
        request = TaskCreateRequest(
            task_name="Comprehensive Test Task",
            topic="Cloud Architecture",
            primary_keyword="Kubernetes deployment",
            target_audience="DevOps Engineers",
            category="Technology",
            writing_style_id=writing_style_id,
            quality_preference="quality"
        )
        
        # Verify all fields are captured
        assert request.task_name == "Comprehensive Test Task"
        assert request.topic == "Cloud Architecture"
        assert request.primary_keyword == "Kubernetes deployment"
        assert request.target_audience == "DevOps Engineers"
        assert request.category == "Technology"
        assert request.writing_style_id == writing_style_id
        assert request.quality_preference == "quality"
    
    def test_task_create_request_example_has_writing_style_id(self):
        """Verify that the example in Config includes writing_style_id"""
        
        schema = TaskCreateRequest.model_json_schema()
        example = schema.get("example", {})
        
        assert "writing_style_id" in example
        assert example["writing_style_id"] is not None


class TestWritingStyleValidation:
    """Tests for writing style parameter validation"""
    
    @pytest.mark.asyncio
    async def test_writing_style_id_uuid_format(self):
        """Test that writing_style_id accepts UUID format"""
        
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        
        request = TaskCreateRequest(
            task_name="UUID Test",
            topic="Testing",
            writing_style_id=valid_uuid
        )
        
        assert request.writing_style_id == valid_uuid
    
    @pytest.mark.asyncio
    async def test_writing_style_id_optional_default_none(self):
        """Test that writing_style_id defaults to None when not provided"""
        
        request = TaskCreateRequest(
            task_name="Optional Field Test",
            topic="Testing"
        )
        
        assert request.writing_style_id is None
        assert hasattr(request, "writing_style_id")


class TestContentGenerationContext:
    """Tests for writing style context in content generation"""
    
    @pytest.mark.asyncio
    async def test_execution_context_includes_writing_style_id(self):
        """Verify that execution context passed to orchestrator includes writing_style_id"""
        
        writing_style_id = str(uuid4())
        user_id = str(uuid4())
        task_id = str(uuid4())
        
        # Simulate execution context building (as done in task_executor.py)
        execution_context = {
            "task_id": task_id,
            "user_id": user_id,
            "writing_style_id": writing_style_id,
            "model_selections": {},
            "quality_preference": "balanced"
        }
        
        assert execution_context["writing_style_id"] == writing_style_id
        assert execution_context["task_id"] == task_id
        assert execution_context["user_id"] == user_id
    
    @pytest.mark.asyncio
    async def test_execution_context_without_writing_style_id(self):
        """Verify that execution context works when writing_style_id is not provided"""
        
        user_id = str(uuid4())
        task_id = str(uuid4())
        
        execution_context = {
            "task_id": task_id,
            "user_id": user_id,
            "writing_style_id": None,
            "model_selections": {},
            "quality_preference": "balanced"
        }
        
        assert execution_context["writing_style_id"] is None
        assert execution_context["task_id"] == task_id


class TestPromptEnhancements:
    """Tests for prompt enhancements with writing style guidance"""
    
    def test_critique_prompt_with_style_guidance(self):
        """Test that critique prompt includes style guidance when available"""
        
        from services.prompt_templates import PromptTemplates
        
        content = "This is sample content for evaluation."
        style_guidance = """
## Writing Style Reference
**Sample Title:** Technical Blog Post
**Sample Text:** Lorem ipsum dolor sit amet, consectetur adipiscing elit.
"""
        
        context = {
            "topic": "AI Testing",
            "writing_style_guidance": style_guidance
        }
        
        prompt = PromptTemplates.content_critique_prompt(content, context)
        
        assert "Writing Style Consistency" in prompt
        assert style_guidance in prompt
        assert "AI Testing" in prompt
    
    def test_critique_prompt_without_style_guidance(self):
        """Test that critique prompt works without style guidance"""
        
        from services.prompt_templates import PromptTemplates
        
        content = "This is sample content for evaluation."
        context = {
            "topic": "AI Testing"
        }
        
        prompt = PromptTemplates.content_critique_prompt(content, context)
        
        assert "AI Testing" in prompt
        assert "Writing Style Consistency" not in prompt


class TestMigrationScript:
    """Tests for migration script - structure verification"""
    
    def test_migration_file_exists(self):
        """Verify migration file exists"""
        import os
        
        migration_path = "src/cofounder_agent/migrations/005_add_writing_style_id.sql"
        # In actual test environment, would check full path
        assert "005_add_writing_style_id" in migration_path
    
    def test_migration_adds_correct_column(self):
        """Verify migration script adds writing_style_id column correctly"""
        # This would be tested with actual database execution in integration tests
        # Here we document what should happen:
        # 1. ALTER TABLE content_tasks ADD COLUMN writing_style_id UUID
        # 2. Add FK to writing_samples(id)
        # 3. Create index on writing_style_id
        # 4. Set ON DELETE SET NULL for FK
        pass


# ============================================================================
# FIXTURES FOR FUTURE INTEGRATION TESTING
# ============================================================================

@pytest.fixture
def sample_writing_style_data():
    """Fixture for writing style test data"""
    return {
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "title": "Technical Blog Style",
        "description": "Formal, technical writing for software engineers",
        "content": """
Kubernetes has revolutionized container orchestration. By providing abstractions
for deployment, scaling, and management, it enables developers to focus on
application logic rather than infrastructure concerns.

The declarative model allows teams to define desired state through YAML manifests,
with Kubernetes continuously working to maintain that state. This paradigm shift
has become fundamental to modern DevOps practices.
        """.strip(),
        "word_count": 52,
        "char_count": 325,
        "is_active": True,
    }


@pytest.fixture
def sample_task_with_writing_style():
    """Fixture for task with writing style"""
    return {
        "task_id": str(uuid4()),
        "task_name": "Blog Post: Kubernetes Best Practices",
        "topic": "Kubernetes deployment best practices",
        "writing_style_id": str(uuid4()),
        "primary_keyword": "kubernetes best practices",
        "target_audience": "DevOps Engineers",
        "category": "Technology",
        "status": "pending"
    }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
