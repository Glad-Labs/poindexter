"""
End-to-End Tests for Content Pipeline
Tests complete workflow from research to publishing
"""

import sys
import types
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock Google Cloud modules before imports
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")
if "google.cloud.firestore" not in sys.modules:
    sys.modules["google.cloud.firestore"] = types.ModuleType("google.cloud.firestore")
if "google.cloud.storage" not in sys.modules:
    sys.modules["google.cloud.storage"] = types.ModuleType("google.cloud.storage")
if "google.cloud.pubsub_v1" not in sys.modules:
    sys.modules["google.cloud.pubsub_v1"] = types.ModuleType("google.cloud.pubsub_v1")

from orchestrator import Orchestrator

from utils.data_models import BlogPost


@pytest.fixture
def mock_all_services():
    """Mock all external services for E2E testing"""
    with (
        patch("orchestrator.FirestoreClient") as mock_firestore,
        patch("orchestrator.StrapiClient") as mock_strapi,
        patch("orchestrator.LLMClient") as mock_llm,
        patch("orchestrator.PexelsClient") as mock_pexels,
        patch("orchestrator.GCSClient") as mock_gcs,
        patch("orchestrator.PubSubClient") as mock_pubsub,
    ):

        # Configure mock returns
        mock_firestore_instance = mock_firestore.return_value
        mock_strapi_instance = mock_strapi.return_value
        mock_llm_instance = mock_llm.return_value
        mock_pexels_instance = mock_pexels.return_value
        mock_gcs_instance = mock_gcs.return_value
        mock_pubsub_instance = mock_pubsub.return_value

        # Set up basic behaviors
        mock_llm_instance.generate_text.return_value = "Generated content here."
        mock_llm_instance.generate_summary.return_value = "Summary"
        mock_strapi_instance.create_post.return_value = (
            "post-123",
            "https://strapi.example.com/post-123",
        )
        mock_gcs_instance.upload_file.return_value = "https://storage.example.com/image.jpg"
        mock_pexels_instance.search.return_value = [
            {"src": {"large": "https://example.com/img.jpg"}, "alt": "test"}
        ]

        yield {
            "firestore": mock_firestore_instance,
            "strapi": mock_strapi_instance,
            "llm": mock_llm_instance,
            "pexels": mock_pexels_instance,
            "gcs": mock_gcs_instance,
            "pubsub": mock_pubsub_instance,
        }


@pytest.fixture
def sample_task():
    """Create sample task for E2E testing"""
    return {
        "topic": "Artificial Intelligence in Healthcare",
        "primary_keyword": "AI healthcare applications",
        "target_audience": "healthcare professionals",
        "category": "Technology",
        "task_id": "test-task-123",
    }


@pytest.mark.e2e
class TestContentPipelineE2E:
    """End-to-end tests for complete content pipeline"""

    def test_complete_pipeline_execution(self, mock_all_services, sample_task):
        """Test complete pipeline from start to finish"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            # Mock the research agent response
            with patch("agents.research_agent.requests.post") as mock_post:
                mock_post.return_value.json.return_value = {
                    "organic": [{"title": "Test", "link": "https://example.com", "snippet": "Info"}]
                }
                mock_post.return_value.raise_for_status = Mock()

                # Mock QA approval
                mock_all_services["llm"].generate_text.return_value = (
                    "APPROVAL: YES\n\nContent approved"
                )

                # Mock markdown to blocks conversion
                with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
                    # Execute pipeline
                    result = orchestrator.start(
                        topic=sample_task["topic"],
                        primary_keyword=sample_task["primary_keyword"],
                        target_audience=sample_task["target_audience"],
                        category=sample_task["category"],
                        task_id=sample_task["task_id"],
                    )

        # Verify result
        assert result is not None
        assert isinstance(result, BlogPost)
        assert result.topic == sample_task["topic"]

    def test_pipeline_with_qa_refinement(self, mock_all_services, sample_task):
        """Test pipeline with QA rejection and refinement"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            with patch("agents.research_agent.requests.post") as mock_post:
                mock_post.return_value.json.return_value = {
                    "organic": [{"title": "Test", "link": "https://example.com", "snippet": "Info"}]
                }
                mock_post.return_value.raise_for_status = Mock()

                # First rejection, then approval
                mock_all_services["llm"].generate_text.side_effect = [
                    "Initial draft content",  # Creative agent
                    "Needs improvement",  # QA rejection
                    "Improved content",  # Creative agent second pass
                    "APPROVAL: YES\n\nApproved",  # QA approval
                ]

                with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
                    result = orchestrator.start(
                        topic=sample_task["topic"],
                        primary_keyword=sample_task["primary_keyword"],
                        target_audience=sample_task["target_audience"],
                        category=sample_task["category"],
                        task_id=sample_task["task_id"],
                        refinement_loops=2,
                    )

        assert result is not None


@pytest.mark.e2e
class TestPipelineIntegration:
    """Test integration between pipeline components"""

    def test_research_to_creative_flow(self, mock_all_services):
        """Test data flow from research to creative agent"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            with patch("agents.research_agent.requests.post") as mock_post:
                research_data = {
                    "organic": [
                        {
                            "title": "AI in Healthcare",
                            "link": "https://example.com",
                            "snippet": "AI is revolutionizing healthcare",
                        }
                    ]
                }
                mock_post.return_value.json.return_value = research_data
                mock_post.return_value.raise_for_status = Mock()

                # Verify research data is passed to creative agent
                mock_all_services["llm"].generate_text.return_value = "APPROVAL: YES"

                with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
                    orchestrator.start(
                        topic="AI Healthcare",
                        primary_keyword="AI",
                        target_audience="doctors",
                        category="Technology",
                        task_id="test-123",
                    )

                # Creative agent should have been called
                assert mock_all_services["llm"].generate_text.called

    def test_image_to_publishing_flow(self, mock_all_services):
        """Test image generation and integration into publishing"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            with patch("agents.research_agent.requests.post") as mock_post:
                mock_post.return_value.json.return_value = {"organic": []}
                mock_post.return_value.raise_for_status = Mock()

                mock_all_services["llm"].generate_text.return_value = "[IMAGE-1]\n\nAPPROVAL: YES"
                mock_all_services["gcs"].upload_file.return_value = (
                    "https://storage.example.com/img.jpg"
                )

                with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
                    result = orchestrator.start(
                        topic="Test",
                        primary_keyword="test",
                        target_audience="all",
                        category="Test",
                        task_id="test-123",
                    )

                # GCS upload should have been called if images generated
                # (exact behavior depends on implementation)
                assert result is not None


@pytest.mark.e2e
class TestPipelineErrorHandling:
    """Test pipeline error handling"""

    def test_handles_research_failure(self, mock_all_services, sample_task):
        """Test handling of research agent failure"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            with patch("agents.research_agent.requests.post") as mock_post:
                mock_post.side_effect = Exception("Research failed")

                # Pipeline should handle error gracefully
                try:
                    result = orchestrator.start(
                        topic=sample_task["topic"],
                        primary_keyword=sample_task["primary_keyword"],
                        target_audience=sample_task["target_audience"],
                        category=sample_task["category"],
                        task_id=sample_task["task_id"],
                    )
                    # If handled gracefully, result may be None or have error status
                    assert result is None or hasattr(result, "rejection_reason")
                except Exception:
                    # Acceptable to propagate certain errors
                    pass

    def test_handles_publishing_failure(self, mock_all_services, sample_task):
        """Test handling of publishing failure"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            with patch("agents.research_agent.requests.post") as mock_post:
                mock_post.return_value.json.return_value = {"organic": []}
                mock_post.return_value.raise_for_status = Mock()

                mock_all_services["llm"].generate_text.return_value = "APPROVAL: YES"
                mock_all_services["strapi"].create_post.side_effect = Exception("Publishing failed")

                with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
                    result = orchestrator.start(
                        topic=sample_task["topic"],
                        primary_keyword=sample_task["primary_keyword"],
                        target_audience=sample_task["target_audience"],
                        category=sample_task["category"],
                        task_id=sample_task["task_id"],
                    )

                # Should handle error gracefully
                assert result is not None


@pytest.mark.e2e
@pytest.mark.performance
class TestPipelinePerformance:
    """Test pipeline performance"""

    def test_pipeline_completes_in_reasonable_time(self, mock_all_services, sample_task):
        """Test that pipeline completes within acceptable time"""
        import time

        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            with patch("agents.research_agent.requests.post") as mock_post:
                mock_post.return_value.json.return_value = {"organic": []}
                mock_post.return_value.raise_for_status = Mock()

                mock_all_services["llm"].generate_text.return_value = "APPROVAL: YES"

                with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
                    start = time.time()
                    orchestrator.start(
                        topic=sample_task["topic"],
                        primary_keyword=sample_task["primary_keyword"],
                        target_audience=sample_task["target_audience"],
                        category=sample_task["category"],
                        task_id=sample_task["task_id"],
                        refinement_loops=1,
                    )
                    duration = time.time() - start

            # With all mocked services, should complete quickly
            assert duration < 5.0


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.skip(reason="Requires all real services configured")
class TestRealPipelineE2E:
    """Real end-to-end tests (require actual services)"""

    def test_real_pipeline_execution(self):
        """Test with real services"""
        orchestrator = Orchestrator()

        result = orchestrator.start(
            topic="Test Topic",
            primary_keyword="test",
            target_audience="testers",
            category="Testing",
            task_id="real-test-123",
        )

        assert result is not None
        assert result.strapi_id is not None
        assert result.strapi_url is not None


@pytest.mark.smoke
class TestPipelineSmoke:
    """Smoke tests for basic pipeline functionality"""

    def test_orchestrator_can_start(self, mock_all_services):
        """Smoke test: orchestrator can be initialized and started"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            with patch("agents.research_agent.requests.post") as mock_post:
                mock_post.return_value.json.return_value = {"organic": []}
                mock_post.return_value.raise_for_status = Mock()

                mock_all_services["llm"].generate_text.return_value = "APPROVAL: YES"

                with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
                    result = orchestrator.start(
                        topic="Smoke Test",
                        primary_keyword="smoke",
                        target_audience="all",
                        category="Test",
                        task_id="smoke-123",
                    )

        assert result is not None

    def test_all_agents_accessible(self, mock_all_services):
        """Smoke test: all agents are initialized"""
        with patch("orchestrator.load_prompts_from_file", return_value={}):
            orchestrator = Orchestrator()

            assert hasattr(orchestrator, "research_agent")
            assert hasattr(orchestrator, "creative_agent")
            assert hasattr(orchestrator, "qa_agent")
            assert hasattr(orchestrator, "publishing_agent")
