"""
Image Decision Agent — LLM-powered reasoning for image selection.

Analyzes finished article content and decides:
- WHERE images should go (which sections)
- WHAT type of image (abstract, photo, diagram, mood)
- HOW to source it (SDXL generation vs Pexels stock)
- WHAT prompt/query to use

This is the first ML decision point in the pipeline. The pattern
(gather context → LLM reasons → structured output → execute → log)
is reusable for other decision points (model selection, topic scoring, etc.)

Usage:
    from services.image_decision_agent import plan_images
    plan = await plan_images(content, topic, category)
    # plan.images = [{section, source, style, prompt, position}, ...]
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


@dataclass
class ImagePlan:
    """A single image decision."""
    section_heading: str
    source: str  # "sdxl" or "pexels"
    style: str  # e.g. "blueprint", "dramatic", "minimal", "photorealistic"
    prompt: str  # SDXL prompt or Pexels search query
    position: str  # "after_heading" — where to insert
    reasoning: str  # why the agent chose this


@dataclass
class ImagePlanResult:
    """Complete image plan for an article."""
    images: List[ImagePlan] = field(default_factory=list)
    featured_image: Optional[ImagePlan] = None
    raw_response: str = ""


async def plan_images(
    content: str,
    topic: str,
    category: str = "technology",
    max_images: int = 4,
) -> ImagePlanResult:
    """Analyze article content and plan image placement + sourcing.

    Uses a local LLM to reason about what images would best serve
    each section of the article, then outputs a structured plan.

    Args:
        content: The finished article text (markdown)
        topic: Article topic/title
        category: Content category (technology, gaming, etc.)
        max_images: Maximum number of inline images (excluding featured)

    Returns:
        ImagePlanResult with per-image decisions
    """
    import httpx

    ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
    model = site_config.get("model_role_image_decision", "qwen3:8b")

    # Extract section headings for context
    headings = re.findall(r'^(#{2,3})\s+(.+)$', content, re.MULTILINE)
    sections = [{"level": len(h[0]), "title": h[1].strip()} for h in headings]

    if not sections:
        logger.info("[IMAGE_AGENT] No sections found — skipping image planning")
        return ImagePlanResult()

    # Build the reasoning prompt
    section_list = "\n".join(f"  {i+1}. {s['title']}" for i, s in enumerate(sections))

    prompt = f"""You are an image director for a tech blog. Analyze this article and decide what images would make it more engaging.

ARTICLE TOPIC: {topic}
CATEGORY: {category}

SECTIONS:
{section_list}

AVAILABLE IMAGE SOURCES:
- "sdxl": AI-generated images. Best for: abstract concepts, mood imagery, artistic visualizations, diagrams, futuristic scenes. Styles: blueprint, dramatic, minimal, isometric, macro, editorial.
- "pexels": Stock photography. Best for: real-world objects, hardware close-ups, workspaces, screens with code, servers, people working (if appropriate).

RULES:
1. Pick {max_images} sections that would benefit most from a visual (skip sections that are mostly code)
2. For each, decide: sdxl or pexels? What style? What specific image?
3. Also decide on 1 featured image (the hero/header image for the article)
4. Be specific in your prompts — describe the exact scene, not vague concepts
5. NEVER include text, words, letters, or faces in SDXL images

Output ONLY valid JSON (no markdown, no explanation):
{{
  "featured": {{
    "source": "sdxl" or "pexels",
    "style": "style_name",
    "prompt": "detailed image prompt or search query",
    "reasoning": "why this image works for the hero"
  }},
  "inline": [
    {{
      "section": "exact section title",
      "source": "sdxl" or "pexels",
      "style": "style_name",
      "prompt": "detailed image prompt or search query",
      "reasoning": "why this visual helps this section"
    }}
  ]
}}"""

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            # Verify Ollama is reachable before generating
            try:
                health = await client.get(f"{ollama_url}/api/tags", timeout=5)
                if health.status_code != 200:
                    raise RuntimeError(f"Ollama not healthy: {health.status_code}")
            except Exception as conn_err:
                raise RuntimeError(f"Ollama unavailable at {ollama_url}: {conn_err}") from conn_err

            resp = await client.post(f"{ollama_url}/api/generate", json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 800, "temperature": 0.7},
            })
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()

        # Parse the JSON response
        # Strip markdown code fences if present
        raw_clean = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw_clean = re.sub(r'```\s*$', '', raw_clean, flags=re.MULTILINE).strip()

        plan_data = json.loads(raw_clean)

        result = ImagePlanResult(raw_response=raw)

        # Parse featured image
        featured = plan_data.get("featured", {})
        if featured:
            result.featured_image = ImagePlan(
                section_heading="featured",
                source=featured.get("source", "sdxl"),
                style=featured.get("style", "editorial"),
                prompt=featured.get("prompt", topic),
                position="hero",
                reasoning=featured.get("reasoning", ""),
            )

        # Parse inline images
        for img in plan_data.get("inline", [])[:max_images]:
            result.images.append(ImagePlan(
                section_heading=img.get("section", ""),
                source=img.get("source", "sdxl"),
                style=img.get("style", "editorial"),
                prompt=img.get("prompt", ""),
                position="after_heading",
                reasoning=img.get("reasoning", ""),
            ))

        logger.info(
            "[IMAGE_AGENT] Planned %d inline images + featured for '%s'",
            len(result.images), topic[:40],
        )
        for img in result.images:
            logger.info(
                "[IMAGE_AGENT]   %s [%s/%s]: %s",
                img.section_heading[:30], img.source, img.style, img.prompt[:50],
            )

        # Log the decision for future learning
        try:
            from services.decision_service import log_decision
            # Need a pool — get it from site_config or pass it in
            # For now, log via the decision service if a pool is available
            import asyncpg
            _dsn = site_config.get("database_url", "")
            if not _dsn:
                import os
                _dsn = os.getenv("DATABASE_URL", "")
            if _dsn:
                _conn = await asyncpg.connect(_dsn)
                try:
                    for img in result.images:
                        await log_decision(
                            pool=_conn,
                            decision_type="image_source",
                            decision_point="image_decision_agent",
                            context={"topic": topic, "category": category, "section": img.section_heading},
                            decision={"source": img.source, "style": img.style, "prompt": img.prompt, "reasoning": img.reasoning},
                            model_used=model,
                        )
                finally:
                    await _conn.close()
        except Exception as _log_err:
            logger.debug("[IMAGE_AGENT] Decision logging failed (non-fatal): %s", _log_err)

        return result

    except json.JSONDecodeError as e:
        logger.warning("[IMAGE_AGENT] Failed to parse LLM response as JSON: %s", e)
        logger.debug("[IMAGE_AGENT] Raw response: %s", raw[:500])
        return ImagePlanResult(raw_response=raw)
    except Exception as e:
        logger.warning("[IMAGE_AGENT] Image planning failed: %s", e)
        return ImagePlanResult()
