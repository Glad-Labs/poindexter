import logging
import json
from config import config
from services.llm_client import LLMClient
from utils.data_models import BlogPost
from utils.helpers import load_prompts_from_file, extract_json_from_string

logger = logging.getLogger(__name__)

class CreativeAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)

    def run(self, post: BlogPost, research_context: str = "", is_refinement: bool = False) -> BlogPost:
        """
        Generates or refines the blog post content, now with research context.

        Args:
            post (BlogPost): The blog post to process.
            research_context (str): Context from the ResearchAgent's web search.
            is_refinement (bool): If True, runs the refinement process using QA feedback.
                                  Otherwise, generates the initial draft.
        """
        raw_draft = ""
        if is_refinement:
            logger.info(f"CreativeAgent: Refining content for '{post.topic}' based on QA feedback.")
            feedback = "\n".join(post.qa_feedback)
            refinement_prompt = self.prompts['iterative_refinement'].format(draft=post.raw_content, critique=feedback)
            raw_draft = self.llm_client.generate_text_content(refinement_prompt)
        else:
            logger.info(f"CreativeAgent: Starting initial content generation for '{post.topic}'.")
            draft_prompt = self.prompts['initial_draft_generation'].format(
                topic=post.topic,
                primary_keyword=post.primary_keyword,
                target_audience=post.target_audience,
                internal_link_titles=", ".join(post.published_posts_map.keys()),
                research_context=research_context # Add research context to the prompt
            )
            raw_draft = self.llm_client.generate_text_content(draft_prompt)

        # Sanitize the LLM's output to remove conversational preamble before QA.
        post.raw_content = self._clean_llm_output(raw_draft)

        # Always generate SEO assets after creating or refining content
        self._generate_seo_assets(post)

        logger.info(f"CreativeAgent: Finished processing for '{post.topic}'.")
        return post

    def _clean_llm_output(self, text: str) -> str:
        """
        Removes conversational preamble from the LLM's output by finding the
        first Markdown heading and trimming everything before it.
        """
        if not text:
            return ""
        lines = text.split('\n')
        for i, line in enumerate(lines):
            # Find the first line that starts with '#' which indicates a Markdown header.
            if line.strip().startswith('#'):
                return '\n'.join(lines[i:])
        
        logger.warning("CreativeAgent: Could not find a starting Markdown heading ('#') in the LLM output. The content might contain unwanted preamble.")
        return text # Return original text if no heading is found, with a warning.

    def _generate_seo_assets(self, post: BlogPost):
        """Generates and attaches SEO assets to the post."""
        seo_prompt = self.prompts['seo_and_social_media'].format(draft=post.raw_content)
        seo_assets_text = self.llm_client.generate_text_content(seo_prompt)
        
        json_string = extract_json_from_string(seo_assets_text)
        if not json_string:
            logger.error("Failed to extract JSON for SEO assets from LLM response. Using fallback values.")
            post.generated_title = post.topic
            post.meta_description = "A detailed look at " + post.topic
            return

        try:
            seo_assets = json.loads(json_string)
            post.generated_title = seo_assets.get("title", post.topic)
            post.meta_description = seo_assets.get("meta_description")
            post.related_keywords = seo_assets.get("keywords", [])
        except json.JSONDecodeError:
            logger.error("Failed to parse SEO assets JSON from LLM response. Using fallback values.")
            post.generated_title = post.topic
            post.meta_description = "A detailed look at " + post.topic
