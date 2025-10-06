import logging
import json
import re
from config import config
from services.llm_client import LLMClient
from utils.data_models import BlogPost
from utils.helpers import load_prompts_from_file, extract_json_from_string

logger = logging.getLogger(__name__)

class CreativeAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)

    def run(self, post: BlogPost, is_refinement: bool = False) -> BlogPost:
        """
        Generates or refines the blog post content. The method now directly
        uses the `research_data` and `qa_feedback` stored within the BlogPost object,
        simplifying the method signature.

        Args:
            post (BlogPost): The blog post object, which acts as the single source of truth.
            is_refinement (bool): If True, runs the refinement process using QA feedback
                                  from the post object. Otherwise, generates the initial draft.
        """
        raw_draft = ""
        if is_refinement and post.qa_feedback:
            refinement_prompt = self.prompts['refine_draft'].format(
                topic=post.topic,
                primary_keyword=post.primary_keyword,
                target_audience=post.target_audience,
                research_data=post.research_data,
                previous_draft=post.raw_content,
                qa_feedback=post.qa_feedback[-1]
            )
            logger.info(f"CreativeAgent: Refining content for '{post.topic}' based on QA feedback.")
            raw_draft = self.llm_client.generate_text(refinement_prompt)
        else:
            draft_prompt = self.prompts['create_draft'].format(
                topic=post.topic,
                primary_keyword=post.primary_keyword,
                target_audience=post.target_audience,
                research_data=post.research_data,
                published_posts_map=post.published_posts_map
            )
            logger.info(f"CreativeAgent: Starting initial content generation for '{post.topic}'.")
            raw_draft = self.llm_client.generate_text(draft_prompt)

        # Sanitize the LLM's output and update the post object
        post.raw_content = self._clean_llm_output(raw_draft)
        
        # Generate SEO assets after the main content is finalized
        post = self._generate_seo_assets(post)

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

    def _generate_seo_assets(self, post: BlogPost) -> BlogPost:
        """Generates and assigns SEO assets (title, meta description, slug) for the post."""
        seo_prompt = self.prompts['generate_seo'].format(
            topic=post.topic,
            primary_keyword=post.primary_keyword,
            content=post.raw_content
        )
        logger.info(f"CreativeAgent: Generating SEO assets for '{post.topic}'.")
        seo_assets_text = self.llm_client.generate_text(seo_prompt)

        # Extract the title, meta description, and slug from the response
        post.title = self._extract_asset(seo_assets_text, "Title")
        post.meta_description = self._extract_asset(seo_assets_text, "MetaDescription")
        post.slug = self._extract_asset(seo_assets_text, "Slug")
        
        return post

    def _extract_asset(self, text: str, asset_name: str) -> str:
        """Extracts a specific asset from a text block using regex."""
        try:
            # Pattern to find 'Asset Name: Value' and capture 'Value'
            pattern = re.compile(rf"^\s*{asset_name}:\s*(.*)", re.MULTILINE)
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
            logger.warning(f"CreativeAgent: Could not find '{asset_name}' in the provided text.")
            return ""
        except Exception as e:
            logger.error(f"CreativeAgent: Error extracting asset '{asset_name}': {e}")
            return ""
