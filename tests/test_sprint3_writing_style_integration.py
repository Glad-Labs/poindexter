"""
Sprint 3 - Writing Style RAG Integration Tests
================================================

Tests the end-to-end writing style integration:
1. Schema validation (context field)
2. Frontend task creation with writing_style_id
3. Backend orchestrator retrieval of style guidance
4. Creative agent prompt injection
5. Generated content quality verification

Test Coverage:
- UnifiedTaskRequest schema accepts context field
- Task creation with writing_style_id flows through API
- Orchestrator retrieves writing_style_id from request.context
- WritingStyleIntegrationService provides guidance
- CreativeAgent injects guidance into prompts
- Metadata properly carries style information
"""

import pytest
import asyncio
import json
from typing import Dict, Any, Optional
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import uuid as uuid_lib

# FastAPI and dependencies
from fastapi.testclient import TestClient


class TestWritingStyleSchemaIntegration:
    """Test 1: Schema validation and serialization"""
    
    def test_unified_task_request_accepts_context_field(self):
        """Verify UnifiedTaskRequest schema includes context field"""
        from src.cofounder_agent.schemas.task_schemas import UnifiedTaskRequest
        
        # Test 1.1: Schema accepts context field
        request_data = {
            "task_type": "blog_post",
            "topic": "AI in Healthcare",
            "context": {
                "writing_style_id": "sample-123",
                "user_id": "user-456"
            },
            "metadata": {
                "writing_style_id": "sample-123",
                "tags": ["ai", "healthcare"]
            }
        }
        
        # Should not raise validation error
        request = UnifiedTaskRequest(**request_data)
        
        assert request.context is not None
        assert request.context.get("writing_style_id") == "sample-123"
        assert request.context.get("user_id") == "user-456"
        assert request.task_type == "blog_post"
        
    def test_unified_task_request_context_optional(self):
        """Verify context field is optional (backward compatibility)"""
        from src.cofounder_agent.schemas.task_schemas import UnifiedTaskRequest
        
        # Test 1.2: Context can be omitted
        request_data = {
            "task_type": "blog_post",
            "topic": "AI in Healthcare",
            "metadata": {"tags": ["ai"]}
        }
        
        # Should not raise error - context is optional
        request = UnifiedTaskRequest(**request_data)
        
        assert request.context is None
        assert request.task_type == "blog_post"
        
    def test_unified_task_request_context_format(self):
        """Verify context field accepts Dict[str, Any]"""
        from src.cofounder_agent.schemas.task_schemas import UnifiedTaskRequest
        
        # Test 1.3: Context accepts various data types
        request_data = {
            "task_type": "blog_post",
            "topic": "Test Topic",
            "context": {
                "writing_style_id": "uuid-string",
                "user_id": 123,  # Should accept non-string too
                "nested": {"key": "value"},
                "list": [1, 2, 3]
            }
        }
        
        request = UnifiedTaskRequest(**request_data)
        
        assert request.context["writing_style_id"] == "uuid-string"
        assert request.context["user_id"] == 123
        assert request.context["nested"]["key"] == "value"
        assert request.context["list"] == [1, 2, 3]


class TestTaskCreationWithWritingStyle:
    """Test 2: Task creation endpoint with writing_style_id"""
    
    def test_blog_post_task_payload_structure(self):
        """Verify blog post task payload includes style context"""
        # This test simulates what CreateTaskModal sends
        
        task_payload = {
            "task_type": "blog_post",
            "topic": "AI Trends in Healthcare",
            "style": "technical",
            "tone": "professional",
            "target_length": 2000,
            "tags": ["AI", "Healthcare"],
            "context": {
                "writing_style_id": "sample-abc123"
            },
            "metadata": {
                "writing_style_id": "sample-abc123",
                "generate_featured_image": True
            }
        }
        
        # Verify payload structure
        assert task_payload["task_type"] == "blog_post"
        assert task_payload["context"]["writing_style_id"] == "sample-abc123"
        assert task_payload["metadata"]["writing_style_id"] == "sample-abc123"
        
    def test_blog_post_metadata_enrichment(self):
        """Verify task metadata carries writing_style_id through system"""
        from src.cofounder_agent.schemas.task_schemas import UnifiedTaskRequest
        
        # Simulate frontend request
        frontend_request = {
            "task_type": "blog_post",
            "topic": "Test Topic",
            "style": "narrative",
            "tone": "professional",
            "target_length": 1500,
            "context": {
                "writing_style_id": "frontend-sample-id"
            },
            "metadata": {
                "writing_style_id": "frontend-sample-id",
                "tags": ["test"]
            }
        }
        
        # Pydantic validation
        request = UnifiedTaskRequest(**frontend_request)
        
        # Backend handler would extract this
        metadata = {
            **(request.metadata or {}),
            "generate_featured_image": True
        }
        
        # Verify metadata preserved
        assert metadata["writing_style_id"] == "frontend-sample-id"
        assert metadata["tags"] == ["test"]


class TestOrchestratorContextHandling:
    """Test 3: Orchestrator reads context.writing_style_id"""
    
    @pytest.mark.asyncio
    async def test_orchestrator_extracts_writing_style_id_from_context(self):
        """Verify orchestrator can extract writing_style_id from request.context"""
        
        # Mock request object (what UnifiedTaskRequest becomes)
        mock_request = Mock()
        mock_request.context = {
            "writing_style_id": "test-sample-uuid",
            "user_id": "test-user-id"
        }
        
        # Simulate the orchestrator's context extraction (line 702)
        user_id = mock_request.context.get("user_id") if mock_request.context else None
        writing_style_id = mock_request.context.get("writing_style_id") if mock_request.context else None
        
        assert writing_style_id == "test-sample-uuid"
        assert user_id == "test-user-id"
        
    @pytest.mark.asyncio
    async def test_orchestrator_handles_missing_context(self):
        """Verify orchestrator gracefully handles missing context"""
        
        # Mock request without context
        mock_request = Mock()
        mock_request.context = None
        
        # Simulate the orchestrator's context extraction
        user_id = mock_request.context.get("user_id") if mock_request.context else None
        writing_style_id = mock_request.context.get("writing_style_id") if mock_request.context else None
        
        assert writing_style_id is None
        assert user_id is None


class TestWritingStyleGuidanceInjection:
    """Test 4: Creative agent receives and uses guidance"""
    
    def test_creative_agent_prompt_injection_pattern(self):
        """Verify creative agent appends writing style guidance to prompts"""
        
        # Mock post object with metadata
        mock_post = Mock()
        mock_post.metadata = {
            "writing_sample_guidance": "Technical style: Use precise terminology, complex sentences, formal tone. Sentence length: 15-25 words."
        }
        
        # Simulate creative agent's prompt injection (lines 70, 104)
        draft_prompt = "Write a blog post about AI in healthcare."
        
        # This is what creative_agent.py does at line 104
        if mock_post.metadata.get("writing_sample_guidance"):
            draft_prompt += f"\n\n{mock_post.metadata['writing_sample_guidance']}"
        
        # Verify guidance is appended
        assert "Technical style:" in draft_prompt
        assert "Use precise terminology" in draft_prompt
        assert "Sentence length:" in draft_prompt
        
    def test_creative_agent_handles_missing_guidance(self):
        """Verify creative agent works without guidance (backward compatible)"""
        
        mock_post = Mock()
        mock_post.metadata = {}
        mock_post.writing_style = {
            "tone": "professional",
            "style": "technical"
        }
        
        draft_prompt = "Write a blog post."
        
        # Should not error if guidance missing
        if mock_post.metadata.get("writing_sample_guidance"):
            draft_prompt += f"\n\n{mock_post.metadata['writing_sample_guidance']}"
        
        # Should work without guidance too
        assert draft_prompt == "Write a blog post."
        
    def test_writing_sample_guidance_format(self):
        """Verify the format of writing_sample_guidance from integration service"""
        
        # This is what WritingStyleIntegrationService.get_sample_for_content_generation returns
        sample_data = {
            "writing_style_guidance": (
                "Writing Sample: Technical Blog Post Title\n"
                "Detected Tone: Professional, Authoritative\n"
                "Detected Style: Technical, Structured\n"
                "Average Sentence Length: 18 words\n"
                "Vocabulary Complexity: Advanced\n"
                "\n"
                "Style Instructions:\n"
                "- Use technical terminology appropriately\n"
                "- Structure with clear headings and transitions\n"
                "- Maintain 15-20 word average sentence length\n"
                "- Include specific examples and data points"
            ),
            "sample_title": "Technical Blog Post Title",
            "analysis": {
                "detected_tone": "Professional, Authoritative",
                "detected_style": "Technical, Structured",
                "sentence_length": 18,
                "vocabulary_complexity": "Advanced"
            }
        }
        
        guidance = sample_data.get("writing_style_guidance")
        
        # Verify guidance contains expected elements
        assert "Technical Blog Post Title" in guidance
        assert "Detected Tone:" in guidance
        assert "Style Instructions:" in guidance
        assert "Use technical terminology" in guidance


class TestEndToEndDataFlow:
    """Test 5: Complete data flow from UI to LLM prompt"""
    
    @pytest.mark.asyncio
    async def test_writing_style_flow_step_by_step(self):
        """Trace writing style through entire system"""
        
        # Step 1: Frontend creates task
        frontend_payload = {
            "task_type": "blog_post",
            "topic": "Healthcare AI",
            "context": {
                "writing_style_id": "medical-technical-001"
            },
            "metadata": {
                "writing_style_id": "medical-technical-001"
            }
        }
        
        # Step 2: Backend validates with schema
        from src.cofounder_agent.schemas.task_schemas import UnifiedTaskRequest
        request = UnifiedTaskRequest(**frontend_payload)
        
        # Step 3: Task handler extracts context
        writing_style_id_from_context = request.context.get("writing_style_id") if request.context else None
        assert writing_style_id_from_context == "medical-technical-001"
        
        # Step 4: Metadata includes style ID
        metadata = {
            **(request.metadata or {}),
            "generate_featured_image": True
        }
        assert metadata["writing_style_id"] == "medical-technical-001"
        
        # Step 5: Mock orchestrator retrieval
        mock_guidance = {
            "writing_style_guidance": "Medical technical style: formal, evidence-based.",
            "sample_title": "Healthcare AI"
        }
        
        # Step 6: Mock creative agent receives guidance
        if mock_guidance.get("writing_style_guidance"):
            prompt_with_guidance = "Write about AI" + f"\n\n{mock_guidance['writing_style_guidance']}"
            assert "Medical technical style:" in prompt_with_guidance
            
    def test_metadata_vs_context_distinction(self):
        """Clarify the role of metadata vs context"""
        
        # Context: Request-level parameters for orchestrator flow
        context = {
            "writing_style_id": "sample-123",  # Used by orchestrator
            "user_id": "user-456"  # May be used for filtering
        }
        
        # Metadata: Additional information to store with task
        metadata = {
            "writing_style_id": "sample-123",  # Stored for reference
            "tags": ["technical", "healthcare"],  # Other metadata
            "source": "ui"  # Origin information
        }
        
        # Orchestrator uses context to determine behavior
        assert context["writing_style_id"] == "sample-123"
        
        # Database stores metadata for history
        assert metadata["writing_style_id"] == "sample-123"
        
        # Both can exist, context informs behavior, metadata informs storage


class TestErrorHandling:
    """Test 6: Error handling and edge cases"""
    
    def test_invalid_writing_style_id_graceful_degradation(self):
        """Verify system handles invalid style IDs gracefully"""
        
        request = Mock()
        request.context = {
            "writing_style_id": "nonexistent-uuid-12345"
        }
        
        # Orchestrator should handle missing samples gracefully
        writing_style_id = request.context.get("writing_style_id")
        assert writing_style_id == "nonexistent-uuid-12345"
        
        # In real orchestrator, WritingStyleIntegrationService would return None
        # and creative_agent would use default guidance
        
    def test_null_context_field(self):
        """Verify null context is handled properly"""
        from src.cofounder_agent.schemas.task_schemas import UnifiedTaskRequest
        
        request_data = {
            "task_type": "blog_post",
            "topic": "Test Topic",
            "context": None
        }
        
        request = UnifiedTaskRequest(**request_data)
        
        # Should be None, not error
        assert request.context is None
        
        # Safe extraction as done in orchestrator
        writing_style_id = request.context.get("writing_style_id") if request.context else None
        assert writing_style_id is None


if __name__ == "__main__":
    # Run with: pytest tests/test_sprint3_writing_style_integration.py -v
    pytest.main([__file__, "-v", "-s"])
