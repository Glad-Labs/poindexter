"""
Unit tests for agents/content_agent/agents/qa_agent.py — QAAgent

Tests focus on:
- QAAgent.run(): JSON parsing, approval logic, quality score tracking
- Score threshold enforcement (>= 75 required)
- LLM error fallback
- Feedback normalization
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_blog_post(**kwargs):
    from agents.content_agent.utils.data_models import BlogPost

    defaults = {
        "topic": "Cloud Computing",
        "primary_keyword": "cloud",
        "target_audience": "IT managers",
        "category": "Technology",
    }
    defaults.update(kwargs)
    return BlogPost(**defaults)  # type: ignore[arg-type]


def make_qa_agent():
    """Build a QAAgent with mocked LLM and tools."""
    with (
        patch("agents.content_agent.agents.qa_agent.CrewAIToolsFactory") as mock_tools,
        patch("agents.content_agent.agents.qa_agent.get_prompt_manager") as mock_pm,
    ):
        mock_tools.get_content_agent_tools.return_value = []
        pm = MagicMock()
        mock_pm.return_value = pm
        pm.get_prompt.return_value = "QA review prompt"

        llm_client = AsyncMock()

        from agents.content_agent.agents.qa_agent import QAAgent

        agent = QAAgent(llm_client=llm_client)
        agent.llm_client = llm_client
        agent.pm = pm
    return agent, llm_client, pm


# ---------------------------------------------------------------------------
# run() — approval logic
# ---------------------------------------------------------------------------


class TestQAAgentRun:
    @pytest.mark.asyncio
    async def test_returns_approved_true_when_score_above_threshold(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": True,
                "feedback": "Excellent content.",
                "quality_score": 85.0,
            }
        )
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Some draft content")

        assert approved is True
        assert "85.0" in feedback or "approved" in feedback.lower()

    @pytest.mark.asyncio
    async def test_returns_rejected_when_score_below_75_even_if_llm_approved(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": True,
                "feedback": "OK content.",
                "quality_score": 72.0,
            }
        )
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        # Score 72 < 75 overrides the LLM's approval
        assert approved is False
        assert "72" in feedback or "threshold" in feedback.lower()

    @pytest.mark.asyncio
    async def test_returns_rejected_when_llm_explicitly_rejects(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": False,
                "feedback": "Content lacks depth and citations.",
                "quality_score": 55.0,
            }
        )
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        assert approved is False
        assert "depth" in feedback or "55" in feedback

    @pytest.mark.asyncio
    async def test_tracks_quality_score_in_post(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": True,
                "feedback": "Great.",
                "quality_score": 78.5,
            }
        )
        post = make_blog_post(quality_scores=[])

        await agent.run(post, "Draft content")

        assert 78.5 in post.quality_scores

    @pytest.mark.asyncio
    async def test_appends_to_existing_quality_scores(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": True,
                "feedback": "Better.",
                "quality_score": 82.0,
            }
        )
        post = make_blog_post(quality_scores=[70.0])

        await agent.run(post, "Revised draft")

        assert len(post.quality_scores) == 2
        assert post.quality_scores[1] == 82.0

    @pytest.mark.asyncio
    async def test_handles_llm_exception_gracefully(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        assert approved is False
        assert "error" in feedback.lower() or "manual review" in feedback.lower()

    @pytest.mark.asyncio
    async def test_handles_non_dict_response(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(return_value="this is a string, not a dict")
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        assert approved is False
        assert "malformed" in feedback.lower() or "manual review" in feedback.lower()

    @pytest.mark.asyncio
    async def test_handles_missing_quality_score_field(self):
        agent, llm_client, pm = make_qa_agent()
        # No quality_score key — defaults to 0.0
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": True,
                "feedback": "Looks good.",
            }
        )
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        # quality_score defaults to 0.0 which is < 70 — overridden to rejected
        # (unless quality_score == 0.0 exactly, which the code treats as "no score")
        # Per implementation: `if approved and quality_score < 70.0 and quality_score > 0`
        # So quality_score=0.0 does NOT trigger override — approved stays True
        assert approved is True

    @pytest.mark.asyncio
    async def test_string_approved_field_coerced_to_bool(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": "true",
                "feedback": "Good.",
                "quality_score": 75.0,
            }
        )
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        assert approved is True

    @pytest.mark.asyncio
    async def test_handles_null_feedback(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": True,
                "feedback": None,
                "quality_score": 80.0,
            }
        )
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        assert isinstance(feedback, str)
        assert len(feedback) > 0

    @pytest.mark.asyncio
    async def test_handles_empty_feedback_string(self):
        agent, llm_client, pm = make_qa_agent()
        llm_client.generate_json = AsyncMock(
            return_value={
                "approved": True,
                "feedback": "",
                "quality_score": 80.0,
            }
        )
        post = make_blog_post(quality_scores=[])

        approved, feedback = await agent.run(post, "Draft content")

        assert feedback  # should not be empty
