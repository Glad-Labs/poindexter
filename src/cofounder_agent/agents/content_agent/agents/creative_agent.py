import logging
import json
import re
from ..config import config
from ..services.llm_client import LLMClient
from ..utils.data_models import BlogPost
from ..utils.helpers import load_prompts_from_file, extract_json_from_string, slugify
from ..utils.tools import CrewAIToolsFactory

logger = logging.getLogger(__name__)


class CreativeAgent:
    def _extract_asset(self, text: str, asset_name: str) -> str:
        """
        Extracts the value of a named asset from a text block, e.g., 'Title: ...'.
        Returns the value as a string, or an empty string if not found.
        """
        pattern = rf"^{asset_name}\s*:\s*(.*)$"
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return ""
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.prompts = load_prompts_from_file(config.PROMPTS_PATH)
        try:
            self.tools = CrewAIToolsFactory.get_content_agent_tools()
            logger.info("CreativeAgent: Initialized with all content agent tools")
        except Exception as e:
            logger.warning(f"CreativeAgent: Failed to initialize tools: {e}")
            logger.warning("CreativeAgent will continue without some tools")
            # Initialize with empty tools list - LLMClient can still generate content
            self.tools = []

    async def run(self, post: BlogPost, is_refinement: bool = False, word_count_target: int = None, constraints=None) -> BlogPost:
        """
        Generates or refines the blog post content. The method now directly
        uses the `research_data` and `qa_feedback` stored within the BlogPost object,
        simplifying the method signature.

        Args:
            post (BlogPost): The blog post object, which acts as the single source of truth.
            is_refinement (bool): If True, runs the refinement process using QA feedback
                                  from the post object. Otherwise, generates the initial draft.
            word_count_target (int): Target word count for this phase (e.g., 300 words for creative phase)
            constraints: ContentConstraints object with word count and tolerance settings
        """
        raw_draft = ""
        if is_refinement and post.qa_feedback:
            refinement_prompt = self.prompts["iterative_refinement"].format(
                draft=post.raw_content,
                critique=post.qa_feedback[-1],
            )
            
            # Include writing sample guidance in refinement too
            if post.metadata and post.metadata.get("writing_sample_guidance"):
                refinement_prompt += f"\n\n{post.metadata['writing_sample_guidance']}"
            
            # Inject word count constraint for refinement
            if word_count_target and constraints:
                tolerance = constraints.word_count_tolerance
                min_words = int(word_count_target * (1 - tolerance / 100))
                max_words = int(word_count_target * (1 + tolerance / 100))
                refinement_prompt = f"[WORD COUNT CONSTRAINT: {min_words}-{max_words} words (target: {word_count_target})]\n\n" + refinement_prompt
            
            logger.info(
                f"CreativeAgent: Refining content for '{post.topic}' based on QA feedback."
            )
            raw_draft = await self.llm_client.generate_text(refinement_prompt)
        else:
            draft_prompt = self.prompts["initial_draft_generation"].format(
                topic=post.topic,
                target_audience=post.target_audience,
                primary_keyword=post.primary_keyword,
                research_context=post.research_data,
                internal_link_titles=list(post.published_posts_map.keys()),
            )
            
            # Inject word count constraint at the beginning of the prompt
            if word_count_target and constraints:
                tolerance = constraints.word_count_tolerance
                min_words = int(word_count_target * (1 - tolerance / 100))
                max_words = int(word_count_target * (1 + tolerance / 100))
                constraint_instruction = f"[CRITICAL: Generate content between {min_words}-{max_words} words (target: {word_count_target} ±{tolerance}%)]\n"
                draft_prompt = constraint_instruction + draft_prompt
            
            # Inject writing sample guidance (RAG style matching) if provided
            if post.metadata and post.metadata.get("writing_sample_guidance"):
                draft_prompt += f"\n\n{post.metadata['writing_sample_guidance']}"
                logger.info(f"CreativeAgent: Using user's writing sample for style matching")
            # Otherwise, inject basic style guidance
            elif post.writing_style:
                style_guidance = {
                    "technical": "Use technical language, include code examples and implementation details where appropriate.",
                    "narrative": "Use storytelling techniques with real-world examples and anecdotes. Build a narrative arc.",
                    "listicle": "Structure as a clear numbered or bulleted list with distinct, self-contained items.",
                    "educational": "Focus on teaching. Use simple language and progressively build complexity with practical examples.",
                    "thought-leadership": "Position as expert analysis. Include insights, research citations, and forward-thinking perspectives."
                }
                style_text = style_guidance.get(post.writing_style, "Use a professional writing style.")
                draft_prompt += f"\n\n⭐ WRITING STYLE: {post.writing_style.upper()}\nApproach: {style_text}"
            
            logger.info(
                f"CreativeAgent: Starting initial content generation for '{post.topic}' with style: {post.writing_style or 'default'} (target: {word_count_target} words)."
            )
            raw_draft = await self.llm_client.generate_text(draft_prompt)

        # Sanitize the LLM's output and update the post object
        post.raw_content = self._clean_llm_output(raw_draft)

        # Generate SEO assets after the main content is finalized
        post = await self._generate_seo_assets(post)

        logger.info(f"CreativeAgent: Finished processing for '{post.topic}'.")
        return post

    def _clean_llm_output(self, text: str) -> str:
        """
        Removes conversational preamble from the LLM's output by finding the
        first Markdown heading and trimming everything before it.
        """
        if not text:
            return ""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            # Find the first line that starts with '#' which indicates a Markdown header.
            if line.strip().startswith("#"):
                return "\n".join(lines[i:])

        logger.warning(
            "CreativeAgent: No Markdown heading found in LLM output. Adding heading to ensure proper content structure."
        )
        # No heading found - extract first line as title if it looks reasonable
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if line_stripped and len(line_stripped) < 100 and not line_stripped.startswith(('-', '*', ' ')):
                # Use this line as the heading
                heading = f"# {line_stripped}\n\n"
                remaining_text = '\n'.join(lines[i+1:]) if i+1 < len(lines) else ""
                return heading + remaining_text
        
        # Fallback: add generic heading
        return f"# Content\n\n{text}"

    async def _generate_seo_assets(self, post: BlogPost) -> BlogPost:
        """Generates and assigns SEO assets (title, meta description, slug) for the post."""
        seo_prompt = self.prompts["seo_and_social_media"].format(
            draft=post.raw_content,
        )
        logger.info(f"CreativeAgent: Generating SEO assets for '{post.topic}'.")
        seo_assets_text = await self.llm_client.generate_text(seo_prompt)

        # Extract the JSON object from the LLM's output
        seo_assets_json = extract_json_from_string(seo_assets_text)
        if seo_assets_json:
            try:
                seo_assets = json.loads(seo_assets_json)
                post.title = seo_assets.get("title", "")
                post.meta_description = seo_assets.get("meta_description", "")
                post.slug = slugify(post.title) # Generate slug from title
            except json.JSONDecodeError:
                logger.error(
                    f"CreativeAgent: Failed to decode JSON from SEO assets response: {seo_assets_json}"
                )
        else:
            logger.error(
                f"CreativeAgent: Could not extract JSON from SEO assets response: {seo_assets_text}"
            )

        return post