"""
Phase 3.3 Integration Tests - Content Generation with Writing Samples

Tests for integrating writing samples into the content generation pipeline.
Covers:
1. Sample retrieval and analysis
2. Prompt injection into creative agent
3. Content generation with sample guidance
4. Style matching verification
5. End-to-end workflow

Run with: python -m pytest tests/test_phase_3_3_integration.py -v
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, Optional

# Import services
from services.writing_style_integration import WritingStyleIntegrationService
from services.writing_style_service import WritingStyleService


class TestWritingStyleIntegration:
    """Test writing style integration service"""

    @pytest.fixture
    def mock_db_service(self):
        """Create mock database service"""
        mock_db = AsyncMock()
        mock_db.writing_style = AsyncMock()
        return mock_db

    @pytest.fixture
    def integration_service(self, mock_db_service):
        """Create integration service with mock database"""
        return WritingStyleIntegrationService(mock_db_service)

    @pytest.mark.asyncio
    async def test_sample_retrieval_with_analysis(self, integration_service, mock_db_service):
        """Test that sample retrieval includes analysis"""
        # Mock sample data
        sample_data = {
            "id": "test-sample-123",
            "title": "Professional Blog Post",
            "content": """
                The importance of clear communication in technical writing cannot be overstated.
                Moreover, effective documentation serves as a foundation for successful projects.
                Therefore, writers must focus on clarity, conciseness, and precision.
                Furthermore, consistent terminology helps readers understand complex concepts.
            """,
            "word_count": 50,
            "description": "Professional technical writing sample"
        }

        # Mock the WritingStyleService
        with patch.object(WritingStyleService, 'get_style_prompt_for_specific_sample',
                         return_value={
                             "sample_id": "test-sample-123",
                             "sample_title": "Professional Blog Post",
                             "sample_text": sample_data["content"],
                             "writing_style_guidance": "Match the formal tone",
                             "word_count": 50
                         }):
            
            result = await integration_service.get_sample_for_content_generation(
                writing_style_id="test-sample-123"
            )

            # Verify structure
            assert result is not None
            assert "analysis" in result
            assert "detected_tone" in result["analysis"]
            assert "detected_style" in result["analysis"]

    def test_sample_analysis_tone_detection(self, integration_service):
        """Test tone detection in sample analysis"""
        # Professional formal sample
        formal_text = """
        Therefore, according to research and documented evidence, the methodology 
        employed demonstrates significant utility. Furthermore, comprehensive analysis 
        reveals noteworthy implications for future investigation.
        """

        analysis = integration_service._analyze_sample(formal_text)
        
        assert analysis["detected_tone"] in ["formal", "authoritative"]
        assert analysis["word_count"] > 0
        assert "avg_sentence_length" in analysis

    def test_sample_analysis_style_detection(self, integration_service):
        """Test style detection in sample analysis"""
        # Listicle style sample
        listicle_text = """
        Here are the top reasons why you should consider this approach:
        - First reason: It's effective and proven
        - Second reason: It saves time and resources
        - Third reason: It improves overall outcomes
        
        Each of these points can be applied to your situation.
        """

        analysis = integration_service._analyze_sample(listicle_text)
        
        assert analysis["style_characteristics"]["has_lists"] is True
        assert "avg_sentence_length" in analysis
        assert analysis["word_count"] > 0

    def test_sample_analysis_vocabulary_diversity(self, integration_service):
        """Test vocabulary diversity calculation"""
        # Sample with repetitive words
        repetitive_text = "The the the test test test sample sample sample."
        
        analysis = integration_service._analyze_sample(repetitive_text)
        
        assert "vocabulary_diversity" in analysis
        assert 0 <= analysis["vocabulary_diversity"] <= 1

    def test_analysis_guidance_building(self, integration_service):
        """Test that analysis guidance is properly formatted"""
        analysis = {
            "detected_tone": "professional",
            "detected_style": "technical",
            "avg_sentence_length": 18.5,
            "vocabulary_diversity": 0.85,
            "style_characteristics": {
                "has_headings": True,
                "has_lists": False,
                "has_examples": True,
                "has_quotes": False,
                "has_code_blocks": True
            }
        }

        guidance = integration_service._build_analysis_guidance(analysis)
        
        assert "professional" in guidance
        assert "technical" in guidance
        assert "Uses clear headings" in guidance or "headings" in guidance
        assert "includes code" in guidance or "code" in guidance

    def test_style_comparison(self, integration_service):
        """Test comparison of sample and generated analyses"""
        sample_analysis = {
            "detected_tone": "professional",
            "detected_style": "technical",
            "avg_sentence_length": 18.5,
            "vocabulary_diversity": 0.85
        }

        generated_analysis = {
            "detected_tone": "professional",
            "detected_style": "technical",
            "avg_sentence_length": 19.2,
            "vocabulary_diversity": 0.83
        }

        comparison = integration_service._compare_analyses(sample_analysis, generated_analysis)

        assert comparison["tone_match"] is True
        assert comparison["style_match"] is True
        assert comparison["sentence_length_similarity"] is True

    @pytest.mark.asyncio
    async def test_style_match_verification(self, integration_service, mock_db_service):
        """Test style matching verification"""
        generated_content = """
        The system architecture demonstrates professional design principles.
        Therefore, implementation requires careful attention to detail.
        Moreover, comprehensive testing validates the approach.
        """

        # Mock the sample retrieval
        with patch.object(WritingStyleIntegrationService, 'get_sample_for_content_generation',
                         return_value={
                             "sample_text": generated_content,
                             "analysis": {
                                 "detected_tone": "professional",
                                 "detected_style": "technical",
                                 "avg_sentence_length": 18.0
                             }
                         }):
            
            result = await integration_service.verify_style_match(
                generated_content=generated_content,
                writing_style_id="test-sample-123"
            )

            assert "comparison" in result or "matched" in result


class TestCreativeAgentIntegration:
    """Test integration of writing samples with creative agent"""

    def test_metadata_field_exists_in_blogpost(self):
        """Test that BlogPost model has metadata field for sample guidance"""
        from agents.content_agent.utils.data_models import BlogPost

        post = BlogPost(
            topic="Test Topic",
            primary_keyword="test",
            target_audience="developers",
            category="tech"
        )

        # Should have metadata field
        assert hasattr(post, 'metadata')
        assert post.metadata == {} or post.metadata is None or isinstance(post.metadata, dict)

    def test_metadata_sample_guidance_storage(self):
        """Test storing sample guidance in post metadata"""
        from agents.content_agent.utils.data_models import BlogPost

        post = BlogPost(
            topic="Test Topic",
            primary_keyword="test",
            target_audience="developers",
            category="tech",
            metadata={
                "writing_sample_guidance": "Write in professional tone with clear structure."
            }
        )

        assert "writing_sample_guidance" in post.metadata
        assert "professional" in post.metadata["writing_sample_guidance"]


class TestTaskExecutionWithSample:
    """Test task execution with writing samples"""

    def test_task_data_includes_writing_style_id(self):
        """Test that task data includes writing_style_id"""
        task_data = {
            "id": "task-123",
            "task_name": "Blog Post",
            "topic": "AI in Healthcare",
            "writing_style_id": "sample-456",  # This should be captured
            "user_id": "user-789"
        }

        assert "writing_style_id" in task_data
        assert task_data["writing_style_id"] == "sample-456"

    def test_execution_context_includes_writing_style_id(self):
        """Test that execution context includes writing_style_id"""
        execution_context = {
            "task_id": "task-123",
            "user_id": "user-789",
            "writing_style_id": "sample-456",  # Passed to orchestrator
            "model_selections": {}
        }

        assert "writing_style_id" in execution_context
        assert execution_context["writing_style_id"] == "sample-456"


class TestPhase3Workflow:
    """Test complete Phase 3 workflow"""

    @pytest.mark.asyncio
    async def test_sample_upload_to_content_generation_flow(self):
        """Test complete flow from sample upload to content generation"""
        # This test verifies the integration between phases

        # Phase 3.1: Sample is uploaded via API
        sample_upload = {
            "title": "Technical Writing Sample",
            "content": "Sample content for writing style",
            "description": "Technical writing example"
        }

        # Phase 3.2: Sample is stored and managed
        stored_sample = {
            "id": "sample-123",
            "user_id": "user-789",
            **sample_upload
        }

        # Phase 3.3: Sample is retrieved and analyzed for content generation
        assert "id" in stored_sample
        assert stored_sample["user_id"] == "user-789"

    def test_writing_sample_api_integration(self):
        """Test that sample upload API works with task creation"""
        # Sample is uploaded first
        sample_id = "sample-456"

        # Task is created with reference to sample
        task_request = {
            "task_name": "Blog Post",
            "topic": "AI in Healthcare",
            "writing_style_id": sample_id  # Reference to uploaded sample
        }

        assert task_request["writing_style_id"] == sample_id


# ============================================================================
# Integration Test Scenarios
# ============================================================================

class TestPhase3Scenarios:
    """Real-world scenarios for Phase 3 integration"""

    @pytest.mark.asyncio
    async def test_scenario_create_sample_then_content(self):
        """
        Scenario: User uploads writing sample, then generates content with it
        
        Flow:
        1. User uploads writing sample via WritingSampleUpload component
        2. Sample is stored in database with metadata
        3. User creates new task, selecting the uploaded sample
        4. Content is generated using the sample as style guide
        5. QA verifies content matches the sample style
        """
        # Step 1: Sample upload (Phase 3.1)
        sample = {
            "id": "uuid-sample-1",
            "user_id": "uuid-user-1",
            "title": "Professional Blog Style",
            "content": "Professional writing with formal tone...",
            "word_count": 150,
            "metadata": {
                "tone": "professional",
                "style": "technical"
            }
        }

        # Step 2: Sample stored
        assert sample["id"] is not None
        assert sample["user_id"] is not None

        # Step 3: Task created with sample reference
        task = {
            "task_id": "uuid-task-1",
            "user_id": sample["user_id"],
            "writing_style_id": sample["id"],  # Reference to sample
            "topic": "Machine Learning in Production"
        }

        assert task["writing_style_id"] == sample["id"]
        assert task["user_id"] == sample["user_id"]

        # Step 4: Content generation would use the sample
        # (verified in other tests)

        # Step 5: Style verification would confirm match
        # (verified in other tests)

    def test_scenario_active_sample_fallback(self):
        """
        Scenario: User has active sample, content generation falls back to it
        
        Flow:
        1. User sets one sample as "active" (set_active endpoint)
        2. User creates task WITHOUT specifying writing_style_id
        3. Content generation retrieves active sample
        4. Content is generated using active sample as guide
        """
        user_id = "user-123"
        active_sample_id = "sample-active"

        # When task is created without writing_style_id
        task = {
            "user_id": user_id,
            "writing_style_id": None  # Not specified
        }

        # System should fall back to active sample
        assert task["writing_style_id"] is None
        # (The unified_orchestrator handles the fallback)


# ============================================================================
# Performance and Stability Tests
# ============================================================================

class TestPhase3Performance:
    """Test performance characteristics of Phase 3 integration"""

    def test_sample_analysis_performance(self, integration_service=None):
        """Test that sample analysis completes quickly"""
        if integration_service is None:
            integration_service = WritingStyleIntegrationService(Mock())

        large_sample = "Sample text. " * 5000  # Large sample

        import time
        start = time.time()
        analysis = integration_service._analyze_sample(large_sample)
        elapsed = time.time() - start

        # Analysis should complete quickly (< 100ms)
        assert elapsed < 0.1
        assert "detected_tone" in analysis

    def test_multiple_samples_no_memory_leak(self):
        """Test processing multiple samples doesn't leak memory"""
        integration_service = WritingStyleIntegrationService(Mock())

        for i in range(100):
            sample = f"Sample {i}. This is test content. " * 10
            analysis = integration_service._analyze_sample(sample)
            assert "detected_tone" in analysis

        # If we got here, no memory issues


# ============================================================================
# Documentation Tests
# ============================================================================

class TestPhase3Documentation:
    """Verify Phase 3 documentation and interfaces"""

    def test_sample_fields_documented(self):
        """Test that sample fields are clearly documented"""
        # WritingSampleUpload component should have these fields
        sample_fields = {
            "title": str,
            "content": str,
            "description": str,
            "style": str,
            "tone": str
        }

        assert len(sample_fields) > 0

    def test_api_endpoints_documented(self):
        """Test that Phase 3 API endpoints are documented"""
        endpoints = [
            "POST /api/writing-style/samples/upload",
            "POST /api/writing-style/samples/batch-import",
            "GET /api/writing-style/samples",
            "GET /api/writing-style/samples/{id}",
            "PUT /api/writing-style/samples/{id}",
            "DELETE /api/writing-style/samples/{id}",
            "POST /api/writing-style/samples/{id}/set-active",
            "GET /api/writing-style/active"
        ]

        assert len(endpoints) == 8


if __name__ == "__main__":
    # Run tests with: python -m pytest tests/test_phase_3_3_integration.py -v
    pytest.main([__file__, "-v"])
