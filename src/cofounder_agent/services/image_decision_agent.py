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
    from services.site_config import site_config
    plan = await plan_images(content, topic, category, site_config=site_config)
    # plan.images = [{section, source, style, prompt, position}, ...]
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from services.logger_config import get_logger

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
    images: list[ImagePlan] = field(default_factory=list)
    featured_image: ImagePlan | None = None
    raw_response: str = ""


async def plan_images(
    content: str,
    topic: str,
    category: str = "technology",
    max_images: int = 4,
    site_config: Any = None,
) -> ImagePlanResult:
    """Analyze article content and plan image placement + sourcing.

    Uses a local LLM to reason about what images would best serve
    each section of the article, then outputs a structured plan.

    Args:
        content: The finished article text (markdown)
        topic: Article topic/title
        category: Content category (technology, gaming, etc.)
        max_images: Maximum number of inline images (excluding featured)
        site_config: SiteConfig instance (DI — Phase H, GH#95). Required
            in new code; falls back to the module singleton if omitted
            so legacy callers keep working. Removed in a future cleanup.

    Returns:
        ImagePlanResult with per-image decisions
    """
    import httpx

    if site_config is None:
        # Transitional fallback — removed once all callers thread
        # site_config through. Phase H step 5 (GH#95).
        from services.site_config import site_config as _singleton
        site_config = _singleton

    ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
    model = site_config.get("model_role_image_decision", "qwen3:8b").removeprefix("ollama/")

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
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(90.0, connect=5.0)
        ) as client:
            # Verify Ollama is reachable AND the model exists
            try:
                health = await client.get(f"{ollama_url}/api/tags", timeout=5)
                if health.status_code != 200:
                    raise RuntimeError(f"Ollama not healthy: {health.status_code}")
                available_models = [m["name"] for m in health.json().get("models", [])]
                # Check model exists (match with or without :latest tag)
                model_found = any(
                    model == m or model == m.split(":")[0] or f"{model}:latest" == m
                    for m in available_models
                )
                if not model_found:
                    raise RuntimeError(
                        f"Model '{model}' not found in Ollama. "
                        f"Available: {', '.join(available_models[:10])}. "
                        f"Pull it with: ollama pull {model}"
                    )
            except RuntimeError:
                raise
            except Exception as conn_err:
                raise RuntimeError(f"Ollama unavailable at {ollama_url}: {conn_err}") from conn_err

            logger.info("[IMAGE_AGENT] Calling Ollama model '%s' for image planning", model)

            # Ollama may need to load the model into VRAM on first call.
            # Retry with backoff on 404 (model not loaded yet).
            # Use /nothink prefix for qwen3 thinking models to get direct JSON output.
            is_thinking_model = any(t in model.lower() for t in ("qwen3", "glm-4.7"))
            actual_prompt = f"/nothink\n{prompt}" if is_thinking_model else prompt

            raw = ""
            for _attempt in range(3):
                # Use /api/chat for thinking models (better thinking token handling)
                if is_thinking_model:
                    resp = await client.post(f"{ollama_url}/api/chat", json={
                        "model": model,
                        "messages": [{"role": "user", "content": actual_prompt}],
                        "stream": False,
                        "options": {"num_predict": 800, "temperature": 0.7},
                    })
                else:
                    resp = await client.post(f"{ollama_url}/api/generate", json={
                        "model": model,
                        "prompt": actual_prompt,
                        "stream": False,
                        "options": {"num_predict": 800, "temperature": 0.7},
                    })
                if resp.status_code == 404 and _attempt < 2:
                    import asyncio
                    wait_s = 3 * (_attempt + 1)
                    logger.info("[IMAGE_AGENT] Model not loaded yet (404), retrying in %ds...", wait_s)
                    await asyncio.sleep(wait_s)
                    continue
                resp.raise_for_status()
                resp_data = resp.json()
                # /api/chat returns content in message.content; /api/generate in response
                if is_thinking_model:
                    msg = resp_data.get("message", {})
                    raw = msg.get("content", "").strip()
                    if not raw:
                        # Thinking models: check thinking field in message
                        raw = resp_data.get("thinking", "").strip()
                else:
                    raw = resp_data.get("response", "").strip()
                if not raw:
                    logger.warning("[IMAGE_AGENT] Model returned empty — response=%d chars, thinking=%d chars",
                                   len(resp_data.get("response", "") or resp_data.get("message", {}).get("content", "")),
                                   len(resp_data.get("thinking", "")))
                break

        # Parse the JSON response
        # Strip thinking tags (qwen3 models wrap output in <think>...</think>)
        raw_clean = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        # Strip markdown code fences if present
        raw_clean = re.sub(r'^```(?:json)?\s*', '', raw_clean, flags=re.MULTILINE)
        raw_clean = re.sub(r'```\s*$', '', raw_clean, flags=re.MULTILINE).strip()

        # Try direct parse first, then search for JSON object in the text
        try:
            plan_data = json.loads(raw_clean)
        except (json.JSONDecodeError, ValueError):
            # Thinking models may embed JSON within reasoning text — extract it
            json_match = re.search(r'\{[^{}]*"featured"[^}]*\{.*\}.*\}', raw_clean, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{.*\}', raw_clean, re.DOTALL)
            if json_match:
                plan_data = json.loads(json_match.group(0))
                logger.info("[IMAGE_AGENT] Extracted JSON from reasoning text")
            else:
                raise

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
            # Need a pool — get it from site_config or pass it in.
            # site_config.get already falls back to the DATABASE_URL env
            # var (the only preserved bootstrap credential per DB-first
            # config policy), so no manual os.getenv needed here.
            import asyncpg

            from services.decision_service import log_decision
            _dsn = site_config.get("database_url", "")
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
