import json
import re
from typing import Optional

from services.logger_config import get_logger
from services.prompt_manager import get_prompt_manager

from ..services.llm_client import LLMClient
from ..utils.data_models import BlogPost
from ..utils.helpers import extract_json_from_string, slugify
from ..utils.tools import CrewAIToolsFactory

logger = get_logger(__name__)


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
        self.pm = get_prompt_manager()
        self.prompts = {}  # Defensive stub — use self.pm for all prompt access
        try:
            self.tools = CrewAIToolsFactory.get_content_agent_tools()
            logger.info("CreativeAgent: Initialized with all content agent tools")
        except Exception as e:
            logger.warning(
                f"[init_tools] CreativeAgent: Failed to initialize tools: {e}", exc_info=True
            )
            logger.warning("CreativeAgent will continue without some tools", exc_info=True)
            # Initialize with empty tools list - LLMClient can still generate content
            self.tools = []

    async def run(
        self,
        post: BlogPost,
        is_refinement: bool = False,
        word_count_target: Optional[int] = None,
        constraints=None,
    ) -> BlogPost:
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
            # NEW: BUILD ACCUMULATED FEEDBACK from ALL QA rounds (not just the last one)
            accumulated_feedback = "QA FEEDBACK HISTORY:\n" + "\n".join(
                [f"Round {i+1}: {feedback}" for i, feedback in enumerate(post.qa_feedback)]
            )

            logger.info(
                f"CreativeAgent: Using accumulated QA feedback ({len(post.qa_feedback)} rounds)"
            )
            logger.debug(f"Accumulated feedback:\n{accumulated_feedback}")

            # Enforce maximum refinement iterations (issue #188)
            max_refinements = getattr(post, "refinement_loops", 3)
            current_iteration = len(post.qa_feedback)
            if current_iteration > max_refinements:
                logger.warning(
                    f"CreativeAgent: Max refinements reached ({current_iteration}/{max_refinements}) "
                    f"— returning best draft"
                )
                return post

            # Score improvement check (if quality scores tracked)
            if hasattr(post, "quality_scores") and len(post.quality_scores) > 1:
                latest_score = post.quality_scores[-1]
                previous_score = post.quality_scores[-2]
                score_improvement = latest_score - previous_score

                logger.info(
                    f"Quality score improvement: {previous_score:.1f} → {latest_score:.1f} "
                    f"(+{score_improvement:.1f} points)"
                )

                # Exit if content is good enough OR improvement has truly stalled (issue #187).
                # Previous logic exited when delta < 5 regardless of absolute score,
                # allowing 68/100 content to publish below the 70-point threshold.
                if latest_score >= 75.0:
                    logger.info(
                        f"CreativeAgent: Content meets quality bar ({latest_score:.1f}/100 >= 75). "
                        f"Stopping refinement."
                    )
                    return post
                if score_improvement < 2.0 and len(post.qa_feedback) > 1:
                    logger.info(
                        f"CreativeAgent: Stopping refinement - improvement stalled "
                        f"({score_improvement:.1f} points). "
                        f"Returning current content (score: {latest_score:.1f}/100)"
                    )
                    return post  # Return without further refinement

            if word_count_target and constraints:
                tolerance = constraints.word_count_tolerance
                min_words = int(word_count_target * (1 - tolerance / 100))
                max_words = int(word_count_target * (1 + tolerance / 100))
                word_count_constraint = (
                    f"{min_words}–{max_words} words (target: {word_count_target} ±{tolerance}%)"
                )
            else:
                word_count_constraint = "No strict word count constraint"
            refinement_prompt = self.pm.get_prompt(
                "blog_generation.iterative_refinement",
                draft=post.raw_content,
                critique=accumulated_feedback,
                target_audience=post.target_audience or "General",
                word_count_constraint=word_count_constraint,
            )

            # Include writing sample guidance in refinement too
            if post.metadata and post.metadata.get("writing_sample_guidance"):
                refinement_prompt += f"\n\n{post.metadata['writing_sample_guidance']}"

            logger.info(f"CreativeAgent: Refining content for '{post.topic}' based on QA feedback.")
            raw_draft = await self.llm_client.generate_text(refinement_prompt)
        else:
            draft_prompt = self.pm.get_prompt(
                "blog_generation.initial_draft",
                topic=post.topic,
                target_audience=post.target_audience or "General",
                primary_keyword=post.primary_keyword or "topic",
                word_count=word_count_target or 1500,
                research_context=post.research_data or "No research data provided",
                internal_link_titles=(
                    list(post.published_posts_map.keys()) if post.published_posts_map else []
                ),
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
                logger.info("CreativeAgent: Using user's writing sample for style matching")
            # Otherwise, inject basic style guidance
            elif post.writing_style:
                style_guidance = {
                    "technical": "Use technical language, include code examples and implementation details where appropriate.",
                    "narrative": "Use storytelling techniques with real-world examples and anecdotes. Build a narrative arc.",
                    "listicle": "Structure as a clear numbered or bulleted list with distinct, self-contained items.",
                    "educational": "Focus on teaching. Use simple language and progressively build complexity with practical examples.",
                    "thought-leadership": "Position as expert analysis. Include insights, research citations, and forward-thinking perspectives.",
                }
                style_text = style_guidance.get(
                    post.writing_style, "Use a professional writing style."
                )
                draft_prompt += (
                    f"\n\n⭐ WRITING STYLE: {post.writing_style.upper()}\nApproach: {style_text}"
                )

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
            if (
                line_stripped
                and len(line_stripped) < 100
                and not line_stripped.startswith(("-", "*", " "))
            ):
                # Use this line as the heading
                heading = f"# {line_stripped}\n\n"
                remaining_text = "\n".join(lines[i + 1 :]) if i + 1 < len(lines) else ""
                return heading + remaining_text

        # Fallback: add generic heading
        return f"# Content\n\n{text}"

    async def _generate_seo_assets(self, post: BlogPost) -> BlogPost:
        """Generates and assigns SEO assets (title, meta description, slug) for the post."""
        seo_prompt = self.pm.get_prompt(
            "blog_generation.seo_and_social",
            draft=post.raw_content or "",
        )
        logger.info(f"CreativeAgent: Generating SEO assets for '{post.topic}'.")
        seo_assets_text = await self.llm_client.generate_text(seo_prompt)

        # Extract the JSON object from the LLM's output
        seo_assets_json = extract_json_from_string(seo_assets_text)
        if seo_assets_json:
            try:
                seo_assets = json.loads(seo_assets_json)
            except json.JSONDecodeError:
                # Some model outputs escape markdown underscores in JSON keys
                # (e.g., meta\_description). Normalize and retry once.
                try:
                    normalized_json = seo_assets_json.replace("\\_", "_")
                    seo_assets = json.loads(normalized_json)
                except json.JSONDecodeError:
                    logger.error(
                        f"CreativeAgent: Failed to decode JSON from SEO assets response: {seo_assets_json}",
                        exc_info=True,
                    )
                    seo_assets = None

            if seo_assets:
                post.title = seo_assets.get("title", "")
                post.meta_description = seo_assets.get("meta_description", "")
                post.slug = slugify(post.title or "")  # Generate slug from title
        else:
            logger.error(
                f"CreativeAgent: Could not extract JSON from SEO assets response: {seo_assets_text}"
            )

        # Fallback: ensure SEO fields are never empty (issue #195).
        # Posts with empty title/meta_description cause SEO damage and broken URLs.
        if not post.title:
            post.title = (post.topic[:60] if post.topic else "Untitled Post").strip()
            logger.warning(f"CreativeAgent: Using fallback title from topic: '{post.title}'")
        if not post.meta_description:
            post.meta_description = (
                f"An in-depth guide covering {post.topic}."[:160]
                if post.topic
                else "Read more about this topic."
            )
            logger.warning("CreativeAgent: Using fallback meta_description")
        if not post.slug:
            post.slug = slugify(post.title)
            logger.warning(f"CreativeAgent: Using fallback slug: '{post.slug}'")

        return post


def get_creative_agent():
    """Factory used by workflow_executor dynamic loading.

    Uses the workflow-compatible blog content generator agent implementation.
    """
    from agents.blog_content_generator_agent import get_blog_content_generator_agent

    return get_blog_content_generator_agent()
