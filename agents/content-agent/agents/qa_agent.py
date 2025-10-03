import logging
import json
from config import config
from services.llm_client import LLMClient
from utils.data_models import BlogPost
from utils.helpers import load_prompts_from_file, extract_json_from_string

logger = logging.getLogger(__name__)

class QAAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)

    def review_content(self, post: BlogPost) -> tuple[bool, str]:
        """
        Reviews the content against the quality rubric.

        Args:
            post (BlogPost): The blog post to review.

        Returns:
            tuple[bool, str]: A tuple containing the approval status (boolean)
                              and the feedback (string).
        """
        logger.info(f"QAAgent: Reviewing content for '{post.topic}'.")
        
        prompt = self.prompts['qa_review'].format(
            draft=post.raw_content,
            target_audience=post.target_audience,
            primary_keyword=post.primary_keyword
        )
        
        response_text = self.llm_client.generate_text_content(prompt)
        
        json_string = extract_json_from_string(response_text)
        if not json_string:
            logger.error("QAAgent: Failed to extract JSON review from LLM response.")
            return False, "Failed to extract the QA review from the model's response."

        try:
            review = json.loads(json_string)
            approved = review.get('approved', False)
            feedback = review.get('feedback', "No feedback provided.")
            
            if not approved:
                post.qa_feedback.append(feedback)

            return approved, feedback
        except json.JSONDecodeError:
            logger.error("QAAgent: Failed to parse QA review from LLM response.")
            return False, "Failed to parse the QA review. Please check the content."
