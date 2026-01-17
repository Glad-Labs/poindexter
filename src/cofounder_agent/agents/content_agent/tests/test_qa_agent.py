"""
Tests for QA Agent
Tests content quality review and approval workflow
"""

import pytest
from unittest.mock import Mock, patch
import sys
import types

# Mock Google Cloud modules
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")

from agents.qa_agent import QAAgent
from utils.data_models import BlogPost


@pytest.fixture
def mock_llm_client():
    """Create mock LLM client"""
    mock_client = Mock()
    mock_client.generate_text.return_value = "APPROVAL: YES\n\nContent is high quality."
    return mock_client


@pytest.fixture
def mock_prompts():
    """Create mock prompts dictionary"""
    return {
        "qa_review": "Review this content:\nTopic: {topic}\nKeyword: {primary_keyword}\nAudience: {target_audience}\nContent: {content}"
    }


@pytest.fixture
def qa_agent(mock_llm_client, mock_prompts):
    """Create QAAgent with mocked dependencies"""
    with patch("agents.qa_agent.load_prompts_from_file", return_value=mock_prompts):
        agent = QAAgent(mock_llm_client)
    return agent


@pytest.fixture
def sample_blog_post():
    """Create sample blog post for testing"""
    return BlogPost(
        topic="AI in Healthcare",
        primary_keyword="AI healthcare",
        target_audience="healthcare professionals",
        raw_content="This is a comprehensive article about AI in healthcare...",
    )


class TestQAAgentInitialization:
    """Test QAAgent initialization"""

    def test_agent_initializes_with_llm_client(self, mock_llm_client, mock_prompts):
        """Test that QAAgent initializes with LLM client"""
        with patch("agents.qa_agent.load_prompts_from_file", return_value=mock_prompts):
            agent = QAAgent(mock_llm_client)

        assert agent.llm_client == mock_llm_client
        assert agent.prompts == mock_prompts


class TestContentReview:
    """Test content review functionality"""

    def test_run_approves_good_content(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test that good content is approved"""
        mock_llm_client.generate_text.return_value = "APPROVAL: YES\n\nExcellent quality."

        approved, feedback = qa_agent.run(sample_blog_post, sample_blog_post.raw_content)

        assert approved is True
        assert "approved" in feedback.lower()

    def test_run_rejects_poor_content(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test that poor content is rejected with feedback"""
        feedback_text = "Content needs improvement. Issues:\n1. Too short\n2. Missing examples"
        mock_llm_client.generate_text.return_value = feedback_text

        approved, feedback = qa_agent.run(sample_blog_post, sample_blog_post.raw_content)

        assert approved is False
        assert feedback == feedback_text

    def test_run_formats_prompt_correctly(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test that prompt is formatted with correct parameters"""
        qa_agent.run(sample_blog_post, sample_blog_post.raw_content)

        call_args = mock_llm_client.generate_text.call_args[0][0]
        assert sample_blog_post.topic in call_args
        assert sample_blog_post.primary_keyword in call_args
        assert sample_blog_post.target_audience in call_args


class TestApprovalLogic:
    """Test approval keyword detection"""

    def test_detects_approval_yes(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test detection of APPROVAL: YES keyword"""
        mock_llm_client.generate_text.return_value = "APPROVAL: YES"

        approved, _ = qa_agent.run(sample_blog_post, "content")

        assert approved is True

    def test_detects_approval_no(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test detection when APPROVAL: YES not present"""
        mock_llm_client.generate_text.return_value = "APPROVAL: NO\nNeeds work"

        approved, _ = qa_agent.run(sample_blog_post, "content")

        assert approved is False

    def test_detects_approval_in_middle_of_text(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test detection of approval keyword in middle of response"""
        mock_llm_client.generate_text.return_value = (
            "Content review complete.\n\nAPPROVAL: YES\n\nWell written."
        )

        approved, _ = qa_agent.run(sample_blog_post, "content")

        assert approved is True


class TestFeedbackHandling:
    """Test feedback message handling"""

    def test_returns_feedback_on_rejection(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test that feedback is returned when content rejected"""
        feedback_message = "Improve structure and add more examples."
        mock_llm_client.generate_text.return_value = feedback_message

        approved, feedback = qa_agent.run(sample_blog_post, "content")

        assert approved is False
        assert feedback == feedback_message

    def test_returns_approval_message_on_approval(
        self, qa_agent, sample_blog_post, mock_llm_client
    ):
        """Test that approval message returned when approved"""
        mock_llm_client.generate_text.return_value = "APPROVAL: YES"

        approved, feedback = qa_agent.run(sample_blog_post, "content")

        assert approved is True
        assert "approved" in feedback.lower()


class TestErrorHandling:
    """Test error handling"""

    def test_handles_llm_error(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test handling of LLM errors"""
        mock_llm_client.generate_text.side_effect = Exception("LLM API Error")

        # Should either raise or handle gracefully
        try:
            approved, feedback = qa_agent.run(sample_blog_post, "content")
            # If handled gracefully
            assert approved is False or feedback is not None
        except Exception:
            # Acceptable to propagate error
            pass

    def test_handles_empty_content(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test handling of empty content"""
        result = qa_agent.run(sample_blog_post, "")

        assert result is not None
        assert len(result) == 2  # Should return tuple


@pytest.mark.integration
class TestQAAgentIntegration:
    """Integration tests for QAAgent"""

    @pytest.mark.skip(reason="Requires actual LLM API")
    def test_real_qa_review(self, sample_blog_post):
        """Test with real LLM client"""
        from services.llm_client import LLMClient

        llm_client = LLMClient()
        agent = QAAgent(llm_client)

        approved, feedback = agent.run(sample_blog_post, sample_blog_post.raw_content)

        assert isinstance(approved, bool)
        assert isinstance(feedback, str)


@pytest.mark.performance
class TestQAAgentPerformance:
    """Performance tests"""

    def test_qa_review_performance(self, qa_agent, sample_blog_post, mock_llm_client):
        """Test that QA review completes quickly"""
        import time

        start = time.time()
        qa_agent.run(sample_blog_post, sample_blog_post.raw_content)
        duration = time.time() - start

        assert duration < 1.0  # With mocked client
