import logging
import json
from config import config
from services.llm_client import LLMClient
from utils.data_models import BlogPost
from utils.helpers import load_prompts_from_file

logger = logging.getLogger(__name__)

class QAAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)

    def run(self, post: BlogPost, previous_content: str) -> tuple[bool, str]:
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
        
        prompt = self.prompts['qa_review'].format(
            topic=post.topic,
            primary_keyword=post.primary_keyword,
            target_audience=post.target_audience,
            content=previous_content
        )
        
        response_text = self.llm_client.generate_text(prompt)
        
        # Simple check for approval keyword
        if "APPROVAL: YES" in response_text:
            return True, "Content approved."
        else:
            return False, response_text
