import logging

from ..config import config
from ..services.llm_client import LLMClient
from ....services.prompt_manager import get_prompt_manager
from ..utils.data_models import BlogPost
from ..utils.tools import CrewAIToolsFactory

logger = logging.getLogger(__name__)


class QAAgent:
    def __init__(self, llm_client: LLMClient):
        logger.info("Initializing QAAgent (v2 - Using centralized prompt manager)")
        self.llm_client = llm_client
        self.pm = get_prompt_manager()
        self.tools = CrewAIToolsFactory.get_content_agent_tools()

    async def run(self, post: BlogPost, previous_content: str) -> tuple[bool, str]:
        """
        Reviews the content against the quality rubric. This method is designed
        to be called within the iterative refinement loop of the orchestrator.

        Args:
            post (BlogPost): The blog post object containing all context.
            previous_content (str): The specific version of the content to be reviewed.
                                    In the future, this could be used to compare versions,
                                    but for now, it's the current draft.

        Returns:
            tuple[bool, str]: A tuple containing the approval status (boolean)
                              and the feedback (string).
        """
        logger.info(f"QAAgent: Reviewing content for '{post.topic}'.")

        # Debug logging for troubleshooting
        logger.debug(f"QAAgent DEBUG: post.topic = {post.topic}")
        logger.debug(f"QAAgent DEBUG: post.primary_keyword = {post.primary_keyword}")
        logger.debug(f"QAAgent DEBUG: post.target_audience = {post.target_audience}")
        prompt = self.pm.get_prompt(
            "qa.content_review",
            primary_keyword=post.primary_keyword or "topic",
            target_audience=post.target_audience or "General",
            draft=previous_content,
        )

        try:
            response_data = await self.llm_client.generate_json(prompt)
        except Exception as e:
            logger.error(f"QAAgent: Failed to get JSON from LLM: {e}")
            # Fallback: return generic rejection with error message
            return (
                False,
                f"QA system encountered an error: {str(e)[:100]}. Manual review recommended.",
            )

        # Validate response_data structure
        if not isinstance(response_data, dict):
            logger.error(f"QAAgent: Expected dict, got {type(response_data).__name__}")
            return False, "QA response was malformed. Manual review recommended."

        # Parse the JSON response with validation
        try:
            approved = response_data.get("approved", False)
            feedback = response_data.get("feedback", "No feedback provided.")

            # Validate feedback is a string
            if not isinstance(feedback, str):
                feedback = str(feedback) if feedback else "No feedback provided."

            # Ensure feedback is not empty
            feedback = feedback.strip() if feedback else "No feedback provided."
            if not feedback or feedback == "null" or feedback == "None":
                feedback = "QA review completed. Content ready for approval decision."

            # Validate approved is boolean
            if not isinstance(approved, bool):
                approved = str(approved).lower() in ["true", "yes", "1"]
        except Exception as e:
            logger.error(f"QAAgent: Error parsing response data: {e}")
            return False, f"QA feedback parsing error. Content requires manual review."

        logger.info(f"QAAgent: Review complete - Approved={approved}, Feedback={feedback[:100]}...")

        if approved:
            return True, "Content approved by QA."
        return False, feedback
