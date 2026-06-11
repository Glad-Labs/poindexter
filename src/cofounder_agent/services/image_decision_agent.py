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

from services.langfuse_shim import observe
from services.llm_providers.dispatcher import dispatch_complete, resolve_tier_model
from services.logger_config import get_logger
from services.prompt_manager import get_prompt_manager
from services.site_config import SiteConfig

# Phase-2c (#272): the module-global ``site_config`` + ``set_site_config``
# shim were removed. ``plan_images`` now requires an explicit
# ``site_config=`` kwarg; callers thread the run-bound instance (the
# ``replace_inline_images`` stage → ``context.get("site_config")``). The
# module is no longer in ``di_wiring.WIRED_MODULES``.
#
# Dispatcher migration (poindexter#706): the module-level ``http_client``
# lifespan-bound pool + ``set_http_client`` setter were removed.
# ``plan_images`` now routes through ``dispatch_complete`` when a pool is
# available (production), so cost tracking, Langfuse traces, and
# provider-swappability all apply automatically.  The hand-rolled httpx
# health check + per-model availability probe were Ollama-specific
# implementation details that the provider abstraction handles internally.


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


@observe(as_type="generation", name="image_decision_agent.plan_images")
async def plan_images(
    content: str,
    topic: str,
    category: str = "technology",
    max_images: int = 4,
    *,
    site_config: SiteConfig,
) -> ImagePlanResult:
    """Analyze article content and plan image placement + sourcing.

    Uses a local LLM to reason about what images would best serve
    each section of the article, then outputs a structured plan.

    Args:
        content: The finished article text (markdown)
        topic: Article topic/title
        category: Content category (technology, gaming, etc.)
        max_images: Maximum number of inline images (excluding featured)
        site_config: REQUIRED (#272 Phase-2c) — the run-bound SiteConfig,
            threaded by the ``replace_inline_images`` stage via
            ``context.get("site_config")``.

    Returns:
        ImagePlanResult with per-image decisions
    """
    _sc = site_config

    # Cost-tier API (Lane B sweep). Image decision is a small JSON-shaped
    # task — budget tier (qwen3:8b-class) is the right home, not standard.
    # Operators tune via app_settings.cost_tier.budget.model. Per-call-site
    # key (model_role_image_decision) preserved as last-ditch fallback;
    # if both miss we page the operator and return an empty plan.
    pool = getattr(_sc, "_pool", None)
    model: str | None = None
    if pool is not None:
        try:
            model = (
                await resolve_tier_model(pool, "budget")
            ).removeprefix("ollama/")
        except (RuntimeError, ValueError) as tier_err:
            logger.debug(
                "[IMAGE_AGENT] cost_tier.budget.model unresolved (%s); "
                "trying model_role_image_decision fallback",
                tier_err,
            )
    if not model:
        fallback = _sc.get("model_role_image_decision") or ""
        if not fallback:
            from services.integrations.operator_notify import notify_operator
            await notify_operator(
                "image_decision_agent: cost_tier='budget' has no model AND "
                "model_role_image_decision is empty — image planning skipped",
                critical=False,
                site_config=_sc,
            )
            return ImagePlanResult()
        model = fallback.removeprefix("ollama/")

    # Extract section headings for context.
    #
    # 2026-05-27 fix: writers sometimes emit ``**Section Title**``
    # bold-text fake headings instead of real ``## Section Title``
    # markdown. The 12 most-recent canonical_blog posts had 0–4 H2s
    # — most had a single H2 OR none at all, with bold-text pseudo-
    # headings carrying the structural load instead. The image
    # decision agent's regex matched only real markdown headings, so
    # ``plan_images`` returned an empty plan for every post that used
    # bold-text section dividers — visible in prod as 0 inline images
    # across 12 consecutive published posts.
    #
    # Two passes: real markdown first (preserves any explicit H2/H3
    # structure the writer gave us), then bold-text pseudo-headings
    # as L2-equivalent when nothing real survives. The bold-text
    # fallback only counts a line that is ENTIRELY a bold-wrapped
    # phrase ``**…**`` and short (<=80 chars). A `**foo**` mid-
    # paragraph isn't a heading, so we anchor to start- and end-of-
    # line with the multiline flag.
    headings = re.findall(r'^(#{2,3})\s+(.+)$', content, re.MULTILINE)
    sections = [{"level": len(h[0]), "title": h[1].strip()} for h in headings]

    if not sections:
        bold_headings = re.findall(
            r'^\*\*(.{1,80}?)\*\*\s*$', content, re.MULTILINE,
        )
        sections = [
            {"level": 2, "title": title.strip()}
            for title in bold_headings
            if title.strip()
        ]
        if sections:
            logger.info(
                "[IMAGE_AGENT] No real H2/H3 — fell back to %d "
                "bold-text pseudo-headings as L2",
                len(sections),
            )

    if not sections:
        logger.info("[IMAGE_AGENT] No sections found — skipping image planning")
        return ImagePlanResult()

    section_list = "\n".join(f"  {i+1}. {s['title']}" for i, s in enumerate(sections))

    prompt = get_prompt_manager().get_prompt(
        "image.decision",
        topic=topic,
        category=category,
        section_list=section_list,
        max_images=max_images,
    )

    if pool is None:
        # No pool means no DB access — we can't route through dispatch_complete
        # (which needs DB for provider/config lookup). Production always has a
        # pool via site_config._pool; this guard is for tests / bootstrap paths
        # that run without a live DB.
        logger.warning(
            "[IMAGE_AGENT] No DB pool available — skipping image planning "
            "(pool is required for dispatch_complete)"
        )
        return ImagePlanResult()

    try:
        logger.info("[IMAGE_AGENT] Calling model '%s' for image planning", model)

        # Route through dispatch_complete — picks up cost tracking,
        # Langfuse traces, and provider-swappability from a single call.
        # The hand-rolled Ollama health check + retry loop were
        # implementation details specific to the raw httpx path; the
        # provider layer handles connectivity and retries internally.
        messages = [{"role": "user", "content": prompt}]
        completion = await dispatch_complete(
            pool=pool,
            messages=messages,
            model=model,
            tier="budget",
            phase="image_decision_agent",
            options={"num_predict": 800, "temperature": 0.7},
        )
        raw = (getattr(completion, "text", "") or "").strip()

        if not raw:
            logger.warning("[IMAGE_AGENT] Model returned empty response")

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
            _dsn = _sc.get("database_url", "")
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
