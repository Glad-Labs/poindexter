import logging
import json
from ..config import config
from ..services.llm_client import LLMClient
from ..utils.data_models import BlogPost
from ..utils.helpers import load_prompts_from_file

logger = logging.getLogger(__name__)


class QAAgent:
    def __init__(self, llm_client: LLMClient):
        logger.info("Initializing QAAgent (v2 - Fixed draft key)")
        self.llm_client = llm_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)

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
        logger.debug(f"QAAgent DEBUG: prompts dict keys = {list(self.prompts.keys())}")
        
        # Verify qa_review template exists before using it
        if "qa_review" not in self.prompts:
            available_templates = list(self.prompts.keys())
            error_msg = f"qa_review template not found in prompts. Available templates: {available_templates}"
            logger.error(f"QAAgent ERROR: {error_msg}")
            raise KeyError(error_msg)

        try:
            prompt = self.prompts["qa_review"].format(
                primary_keyword=post.primary_keyword,
                target_audience=post.target_audience,
                draft=previous_content,
            )
        except KeyError as e:
            logger.error(f"QAAgent: Format string error - missing key: {e}")
            logger.error(f"QAAgent: Template = {self.prompts['qa_review'][:200]}...")
            raise

        response_data = await self.llm_client.generate_json(prompt)

        # Parse the JSON response
        approved = response_data.get("approved", False)
        feedback = response_data.get("feedback", "No feedback provided.")

        if approved:
            return True, "Content approved."
        else:
            return False, feedback
