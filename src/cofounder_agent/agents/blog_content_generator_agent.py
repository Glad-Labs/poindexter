"""
Blog Content Generator Agent - Bridge agent for workflow system

Wraps ai_content_generator service to be callable as a workflow phase.

This agent:
1. Takes workflow inputs (topic, style, tone, tags, target_length)
2. Calls ai_content_generator.generate_blog_post()
3. Returns results compatible with workflow executor
"""

from typing import Any

from services.ai_content_generator import get_content_generator
from services.logger_config import get_logger

logger = get_logger(__name__)


class BlogContentGeneratorAgent:
    """
    Agent that generates blog post content using AI.

    Callable as a workflow phase with inputs:
    - topic: str (required) - Blog topic
    - style: str (optional) - Content style (balanced, technical, narrative, listicle, thought-leadership)
    - tone: str (optional) - Content tone (professional, casual, academic, inspirational)
    - target_length: int (optional) - Target word count
    - tags: list[str] (optional) - Keywords/tags for SEO
    - preferred_model: str (optional) - Preferred LLM model
    - preferred_provider: str (optional) - Preferred LLM provider
    """

    def __init__(self):
        """Initialize blog content generator agent"""
        logger.info("Initializing BlogContentGeneratorAgent")
        self.content_generator = get_content_generator()

    async def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Generate blog content from topic and parameters.

        Args:
            inputs: Dict with keys:
                - topic (required): str, blog topic
                - style (optional): str, content style
                - tone (optional): str, content tone
                - target_length (optional): int, target word count
                - tags (optional): list[str], keywords
                - preferred_model (optional): str
                - preferred_provider (optional): str

        Returns:
            Dict with keys:
                - content: str, generated markdown content
                - model_used: str, which LLM model was used
                - metrics: dict, generation metrics
                - word_count: int, actual word count
                - status: str, "success" or "failed"
                - error: str (if failed)
        """

        try:
            logger.info(
                f"[BlogContentGeneratorAgent] Generating content for topic: {inputs.get('topic')}"
            )

            topic = inputs.get("topic")
            if not topic or len(topic.strip()) < 3:
                raise ValueError("Topic must be at least 3 characters")

            # Extract parameters with defaults
            style = inputs.get("style", "balanced")
            tone = inputs.get("tone", "professional")
            target_length = inputs.get("target_length", 1500)
            tags = inputs.get("tags", [topic])

            # Model selection — UI sends "model" as "provider-modelname" (e.g., "ollama-gemma3:12b")
            # Parse into preferred_provider and preferred_model for the content generator
            preferred_model = inputs.get("preferred_model")
            preferred_provider = inputs.get("preferred_provider")
            ui_model = inputs.get("model")
            if ui_model and not preferred_model:
                if "-" in ui_model:
                    parts = ui_model.split("-", 1)
                    preferred_provider = preferred_provider or parts[0]
                    preferred_model = parts[1]
                else:
                    preferred_model = ui_model
                logger.info(
                    f"[BlogContentGeneratorAgent] UI model '{ui_model}' "
                    f"-> provider={preferred_provider}, model={preferred_model}"
                )

            # Call content generator
            content_text, model_used, metrics = await self.content_generator.generate_blog_post(
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags=tags,
                preferred_model=preferred_model,
                preferred_provider=preferred_provider,
            )

            logger.info(
                f"[BlogContentGeneratorAgent] Generated {len(content_text)} characters "
                f"using {model_used}"
            )

            return {
                "content": content_text,
                "model_used": model_used,
                "metrics": metrics,
                "word_count": len(content_text.split()),
                "status": "success",
            }

        except Exception as e:
            logger.error(f"[BlogContentGeneratorAgent] Error: {e!s}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "content": None,
            }


def get_blog_content_generator_agent() -> BlogContentGeneratorAgent:
    """Factory function for BlogContentGeneratorAgent"""
    return BlogContentGeneratorAgent()
