"""
Unified AI Content Generator Service

Handles blog post generation with fallback through multiple providers:
1. Local Ollama (free, RTX 5070 optimized)
2. HuggingFace (free tier)

Features:
- Intelligent provider fallback strategy
- Self-checking and validation throughout generation
- Quality assurance with refinement loops
- Content metrics and performance tracking

ASYNC-FIRST: All I/O operations use httpx async client (no blocking calls)
"""

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from services.logger_config import get_logger

from .prompt_manager import get_prompt_manager
from .provider_checker import ProviderChecker

logger = get_logger(__name__)


class ContentValidationResult:
    """Result of content validation check"""

    def __init__(
        self,
        is_valid: bool,
        quality_score: float,
        issues: list[str] | None = None,
        feedback: str = "",
    ):
        self.is_valid = is_valid
        self.quality_score = quality_score  # 0-10 scale
        self.issues = issues if issues is not None else []
        self.feedback = feedback


class AIContentGenerator:
    """Unified content generation with provider fallback and self-checking"""

    def __init__(self, quality_threshold: float = 5.0):
        """Initialize content generator

        Args:
            quality_threshold: Minimum quality score (0-10) for content acceptance
        """
        self.quality_threshold = quality_threshold
        self.ollama_available = False
        self.ollama_checked = False  # Track if we've checked Ollama async
        self.generation_attempts = 0
        self.max_refinement_attempts = 3

        logger.info("AIContentGenerator initialized (Ollama check deferred to first async call)")
        logger.debug(
            "   HuggingFace token: %s",
            "set" if ProviderChecker.is_huggingface_available() else "not set",
        )

    async def _check_ollama_async(self):
        """Async check if Ollama is running - call this once before using Ollama"""
        if self.ollama_checked:
            logger.debug("Ollama already checked previously: %s", self.ollama_available)
            return

        from services.site_config import site_config as _sc
        ollama_url = _sc.get("ollama_base_url") or _sc.get("ollama_host", "http://host.docker.internal:11434")
        logger.info("Checking if Ollama server is running at %s...", ollama_url)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0)
            ) as client:
                response = await client.get(f"{ollama_url}/api/tags", timeout=5)
                self.ollama_available = response.status_code == 200

            if self.ollama_available:
                logger.info("[OK] Ollama IS running at %s", ollama_url)
            else:
                logger.warning("Ollama returned non-200 status: %s", response.status_code)
        except Exception as e:
            logger.warning("Ollama health check failed: %s: %s", type(e).__name__, e)
            self.ollama_available = False
        finally:
            self.ollama_checked = True

    async def _populate_internal_links_cache(self):
        """Fetch published post titles + slugs so the LLM can include real internal links."""
        try:
            from services.site_config import site_config as _sc
            site_url = _sc.get("site_url", "")

            import asyncpg
            dsn = os.getenv("DATABASE_URL", "")
            if not dsn:
                self._internal_links_cache = []
                return

            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch(
                    "SELECT title, slug FROM posts WHERE status = 'published' ORDER BY published_at DESC LIMIT 20"
                )
                self._internal_links_cache = [
                    f"- \"{row['title']}\" -> {site_url}/posts/{row['slug']}"
                    for row in rows
                ]
                logger.info("[INTERNAL_LINKS] Loaded %d published posts for linking", len(rows))
            finally:
                await conn.close()
        except Exception as e:
            logger.debug("[INTERNAL_LINKS] Failed to load internal links: %s", e)
            self._internal_links_cache = []

    def _validate_content(
        self, content: str, topic: str, target_length: int
    ) -> ContentValidationResult:
        """
        Self-check: Validate generated content against quality rubric.

        Checks:
        1. Content length (target ±30%)
        2. Structure (has headings, sections)
        3. Content quality (readability, completeness)
        4. Markdown formatting
        5. Presence of practical examples

        Returns:
            ContentValidationResult with quality score and issues
        """
        issues = []
        score = 10.0

        # 1. Check length
        word_count = len(content.split())
        critical_min = int(target_length * 0.7)
        soft_min = int(target_length * 0.9)
        soft_max = int(target_length * 1.1)

        if word_count < critical_min:
            issues.append(
                f"CRITICAL: Content too short: {word_count} words (target: {target_length})"
            )
            score -= 3.0
        elif word_count < soft_min:
            issues.append(f"Content too short: {word_count} words (target: {target_length})")
            score -= 2.0
        elif word_count > soft_max:
            issues.append(f"Content too long: {word_count} words (target: {target_length})")
            score -= 1.0

        # 2. Check structure (headings)
        heading_count = len(re.findall(r"^##+ ", content, re.MULTILINE))
        if heading_count < 3:
            issues.append(f"Insufficient structure: {heading_count} sections (recommend 3-5)")
            score -= 1.5

        # 3. Check for introduction
        if not re.search(r"^# ", content, re.MULTILINE):
            issues.append("Missing title (# heading)")
            score -= 1.0

        # 4. Check for conclusion
        conclusion_keywords = ["conclusion", "summary", "next steps", "takeaway"]
        has_conclusion = any(keyword in content.lower() for keyword in conclusion_keywords)
        if not has_conclusion:
            issues.append("Missing conclusion section")
            score -= 1.5

        # 5. Check for practical examples/lists
        has_examples = "- " in content or "* " in content or "1. " in content
        if not has_examples:
            issues.append("Missing practical examples or bullet points")
            score -= 1.0

        # 6. Check for call-to-action
        cta_keywords = ["ready", "start", "begin", "try", "implement", "action", "next"]
        has_cta = any(keyword in content.lower() for keyword in cta_keywords)
        if not has_cta:
            issues.append("Missing call-to-action")
            score -= 0.5

        # 7. Check for topic mentions (relevance)
        topic_words = topic.lower().split()[:3]  # First 3 words
        topic_mentions = sum(1 for word in topic_words if word in content.lower())
        if topic_mentions < 2:
            issues.append(f"Topic '{topic}' mentioned too few times")
            score -= 1.0

        # Ensure score stays in valid range
        score = max(0, min(10, score))

        is_valid = score >= self.quality_threshold

        feedback = ""
        if is_valid:
            feedback = f"[OK] Content approved (quality score: {score:.1f}/10)"
        else:
            feedback = f"[FAIL] Content needs improvement (quality score: {score:.1f}/10, threshold: {self.quality_threshold})"

        logger.info("Content validation: %s", feedback)
        if issues:
            logger.debug("Issues found: %s", issues)

        return ContentValidationResult(
            is_valid=is_valid, quality_score=score, issues=issues, feedback=feedback
        )

    def _load_generation_prompts(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: list[str],
        research_context: str = "",
    ) -> tuple[str, str, Any]:
        """Load system prompt, generation prompt, and refinement prompt getter from prompt manager.

        Returns (system_prompt, generation_prompt, get_refinement_prompt_fn).
        """
        # Get prompt manager for centralized prompt management
        try:
            pm = get_prompt_manager()
            logger.info("[OK] Prompt manager loaded successfully")
        except Exception as e:
            logger.error("Failed to load prompt manager: %s", e, exc_info=True)
            raise

        # Fetch prompts from centralized manager instead of hardcoding
        # This ensures all prompts are versioned, documented, and easy to maintain
        try:
            logger.info("Loading system prompt...")
            min_words = int(target_length * 0.9)
            max_words = int(target_length * 1.1)
            system_prompt = pm.get_prompt(
                "blog_generation.blog_system_prompt",
                style=style,
                tone=tone,
                target_length=target_length,
                min_words=min_words,
                max_words=max_words,
                tags=", ".join(tags) if tags else "general",
            )
            logger.info("[OK] System prompt loaded (%d chars)", len(system_prompt))
        except Exception as e:
            logger.error("Failed to load system prompt: %s", e, exc_info=True)
            raise

        try:
            logger.info("Loading generation prompt...")
            # Internal links populated by caller (generate_blog_post) via self._internal_links_cache
            internal_link_titles = getattr(self, "_internal_links_cache", [])
            internal_links_str = "\n".join(internal_link_titles) if internal_link_titles else "No existing articles to link to."

            generation_prompt = pm.get_prompt(
                "blog_generation.initial_draft",
                topic=topic,
                target_audience=style,
                primary_keyword=tags[0] if tags else "",
                research_context=research_context,
                internal_link_titles=internal_links_str,
                word_count=target_length,
                style=style,
                tone=tone,
            )
            logger.info("[OK] Generation prompt loaded (%d chars)", len(generation_prompt))
        except Exception as e:
            logger.error(
                "Failed to load generation prompt: %s: %s", type(e).__name__, e, exc_info=True
            )
            raise

        # Create a callable refinement prompt getter
        def get_refinement_prompt(feedback: str, issues: list, content: str) -> str:
            try:
                return pm.get_prompt(
                    "blog_generation.iterative_refinement",
                    draft=content,
                    critique=f"FEEDBACK: {feedback}\nISSUES: {chr(10).join(issues)}",
                    word_count_constraint=f"Target: {target_length} words",
                    target_audience=style,
                )
            except Exception as e:
                logger.error("Failed to load refinement prompt: %s", e, exc_info=True)
                raise

        return system_prompt, generation_prompt, get_refinement_prompt

    async def _prepare_generation_context(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: list[str],
        preferred_model: str | None,
        preferred_provider: str | None,
        writing_style_context: str | None = None,
        research_context: str | None = None,
    ) -> dict[str, Any]:
        """Set up logging, check providers, load prompts, and initialize metrics.

        Returns a context dict containing all shared state needed by provider methods.
        """
        logger.info("\n%s", "=" * 80)
        logger.info("BLOG GENERATION STARTED")
        logger.info("%s", "=" * 80)
        logger.info("Topic: %s", topic)
        logger.info("Style: %s | Tone: %s", style, tone)
        logger.info("Target length: %d words | Tags: %s", target_length, tags)
        logger.info("Quality threshold: %s", self.quality_threshold)
        logger.info("Preferred model: %s", preferred_model or "auto")
        logger.info("Preferred provider: %s", preferred_provider or "auto")
        logger.info(
            "HuggingFace token: %s",
            "yes" if ProviderChecker.is_huggingface_available() else "no",
        )
        logger.info("%s\n", "=" * 80)

        # Provider priority: Ollama → HuggingFace → template
        effective_provider = preferred_provider
        if not effective_provider:
            logger.info("No provider specified, will try fallback chain (Ollama first)")

        skip_ollama = effective_provider and effective_provider.lower() not in ["ollama", "auto"]

        # Use local variable to avoid polluting instance state across requests
        use_ollama = False
        if skip_ollama:
            logger.info("Skipping Ollama (user selected: %s)", effective_provider)
            use_ollama = False
        else:
            await self._check_ollama_async()
            use_ollama = self.ollama_available
            logger.info("Ollama check result: %s", use_ollama)

        # Load all prompts (research context injected into generation prompt)
        system_prompt, generation_prompt, get_refinement_prompt = self._load_generation_prompts(
            topic,
            style,
            tone,
            target_length,
            tags,
            research_context=research_context or "",
        )

        # Inject writing style context into system prompt if provided
        if writing_style_context:
            system_prompt = (
                system_prompt
                + "\n\n## Writing Style Reference\n"
                "Match the tone, voice, and stylistic patterns demonstrated in these "
                "writing samples from the operator. Do not copy them verbatim — use them "
                "as a style guide.\n\n"
                + writing_style_context
            )
            logger.info("Writing style context injected into system prompt (%d chars)", len(writing_style_context))

        # Track metrics
        metrics = {
            "topic": topic,
            "generation_attempts": 0,
            "refinement_attempts": 0,
            "validation_results": [],
            "model_used": None,
            "final_quality_score": 0.0,
            "generation_time_seconds": 0,
            "preferred_model": preferred_model,
            "preferred_provider": preferred_provider,
            "models_used_by_phase": {},  # NEW: Track models at each phase
            "model_selection_log": {  # NEW: Track decision tree
                "requested_provider": preferred_provider,
                "requested_model": preferred_model,
                "attempted_providers": [],
                "skipped_ollama": skip_ollama,
                "decision_tree": {
                    "ollama_available": use_ollama,
                    "huggingface_token_available": ProviderChecker.is_huggingface_available(),
                },
            },
        }

        start_time = time.time()

        # Log provider decision tree
        logger.info("\n%s", "=" * 80)
        logger.info("PROVIDER DECISION TREE:")
        logger.info("%s", "=" * 80)
        logger.info("   User selection: provider=%s, model=%s", preferred_provider, preferred_model)
        logger.info("   Effective provider: %s", effective_provider)
        logger.info("")
        logger.info("   Provider Status:")
        logger.info(
            "   ├─ Ollama (local):     %s",
            "available" if use_ollama else "not available/skipped",
        )
        logger.info(
            "   ├─ HuggingFace (cloud): %s",
            "token set" if ProviderChecker.is_huggingface_available() else "no token",
        )
        logger.info("   └─ Fallback:           Available (generic template)")
        logger.info("%s\n", "=" * 80)

        return {
            "effective_provider": effective_provider,
            "skip_ollama": skip_ollama,
            "use_ollama": use_ollama,
            "system_prompt": system_prompt,
            "generation_prompt": generation_prompt,
            "get_refinement_prompt": get_refinement_prompt,
            "metrics": metrics,
            "start_time": start_time,
            "attempts": [],
            "topic": topic,
            "style": style,
            "tone": tone,
            "target_length": target_length,
            "tags": tags,
            "preferred_model": preferred_model,
        }

    def _extract_ollama_response(self, response) -> str:
        """Extract text content from an Ollama response (dict or string)."""
        # OllamaClient.generate() returns dict with 'text' key (not 'response')
        logger.info("      Raw response type: %s", type(response))
        if isinstance(response, dict):
            logger.info("      Response is dict with keys: %s", list(response.keys()))

        generated_content = ""
        if isinstance(response, dict):
            # Try multiple possible keys: 'text' (OllamaClient), 'response' (Ollama API), 'content'
            generated_content = (
                response.get("text", "")
                or response.get("response", "")
                or response.get("content", "")
            )
            logger.info("      Extracted from dict: %d chars", len(generated_content))
            if generated_content:
                logger.debug(
                    "      Response type: dict | Extracted text: %d chars", len(generated_content)
                )
            else:
                logger.warning(
                    "      No text found in response dict keys: %s", list(response.keys())
                )
        elif isinstance(response, str):
            generated_content = response
            logger.info("      Got direct string: %d chars", len(generated_content))
            logger.debug("      Response type: str | Content: %d chars", len(generated_content))
        else:
            logger.warning("      Unexpected response type: %s", type(response))
            generated_content = ""

        return generated_content

    async def _refine_ollama_content(
        self, ollama, model_name: str, generated_content: str, validation, ctx: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Attempt to refine Ollama-generated content that failed QA.

        Returns result tuple if refinement produces acceptable content, or None.
        Also updates generated_content in the caller via the return value.
        """
        metrics = ctx["metrics"]
        system_prompt = ctx["system_prompt"]
        get_refinement_prompt = ctx["get_refinement_prompt"]
        target_length = ctx["target_length"]
        topic = ctx["topic"]
        start_time = ctx["start_time"]

        if metrics["refinement_attempts"] >= self.max_refinement_attempts:
            return None

        logger.info(
            "      Content below threshold. Refining (%d/%d)...",
            metrics["refinement_attempts"] + 1, self.max_refinement_attempts,
        )

        metrics["refinement_attempts"] += 1
        refinement_prompt = get_refinement_prompt(
            feedback=validation.feedback,
            issues=validation.issues,
            content=generated_content,
        )

        # Try to refine with same model
        # Calculate max tokens for refinement pass — extra headroom for thinking models
        _is_thinking_refine = any(t in model_name.lower() for t in ("qwen3", "glm-4", "deepseek-r1"))
        max_tokens_refinement = int(target_length * (7.0 if _is_thinking_refine else 4.5))
        response = await ollama.generate(
            prompt=refinement_prompt,
            system=system_prompt,
            model=model_name,
            stream=False,
            max_tokens=max_tokens_refinement,  # 4.5x multiplier for complete refinement with better word count
        )

        # Extract text from response dict
        refined_content = ""
        if isinstance(response, dict):
            refined_content = response.get("response", "")
            logger.debug(
                "      Refined response type: dict | Content: %d chars", len(refined_content)
            )
        elif isinstance(response, str):
            refined_content = response
            logger.debug(
                "      Refined response type: str | Content: %d chars", len(refined_content)
            )

        if refined_content and len(refined_content) > 100:
            logger.info("      [OK] Refined content generated: %d characters", len(refined_content))

            # Validate refined content
            logger.info("      Validating refined content...")
            refined_validation = self._validate_content(refined_content, topic, target_length)
            metrics["validation_results"].append(
                {
                    "attempt": metrics["generation_attempts"],
                    "refinement": metrics["refinement_attempts"],
                    "score": refined_validation.quality_score,
                    "issues": refined_validation.issues,
                    "passed": refined_validation.is_valid,
                }
            )

            refined_word_count = len(refined_content.split())
            logger.info(
                "      Refined Quality: %.1f/%s | Words: %d | Issues: %d",
                refined_validation.quality_score, self.quality_threshold, refined_word_count, len(refined_validation.issues),
            )

            if refined_validation.is_valid:
                logger.info("      [OK] Refined content APPROVED")
                metrics["model_used"] = model_name
                metrics["models_used_by_phase"]["draft"] = metrics["model_used"]  # Track phase
                metrics["final_quality_score"] = refined_validation.quality_score
                metrics["generation_time_seconds"] = time.time() - start_time
                logger.info("\n%s", "=" * 80)
                logger.info("[OK] GENERATION COMPLETE (with refinement)")
                logger.info("   Model: %s", metrics["model_used"])
                logger.info(
                    "   Quality: %.1f/%s", refined_validation.quality_score, self.quality_threshold
                )
                logger.info("   Time: %.1fs", metrics["generation_time_seconds"])
                logger.info("%s\n", "=" * 80)
                return refined_content, metrics["model_used"], metrics

        # Return None but signal the refined content via a special key
        # so the caller can use it for further checks
        if refined_content and len(refined_content) > 100:
            ctx["_refined_content"] = refined_content
        return None

    async def _try_ollama(self, ctx: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
        """Try Ollama local provider with refinement loop. Returns result tuple or None."""
        use_ollama = ctx["use_ollama"]
        skip_ollama = ctx["skip_ollama"]
        effective_provider = ctx["effective_provider"]
        preferred_model = ctx["preferred_model"]
        metrics = ctx["metrics"]
        system_prompt = ctx["system_prompt"]
        generation_prompt = ctx["generation_prompt"]
        target_length = ctx["target_length"]
        topic = ctx["topic"]
        attempts = ctx["attempts"]
        start_time = ctx["start_time"]

        # 1. Try Ollama (local, free, no internet, RTX 5070 optimized)
        if not use_ollama:
            logger.info(
                "SKIPPING Ollama (skip_ollama=%s, effective_provider=%s)",
                skip_ollama, effective_provider,
            )
            return None

        logger.info("[ATTEMPT 1/3] Trying Ollama (Local, GPU-accelerated)...")
        from services.site_config import site_config as _sc
        ollama_endpoint = _sc.get("ollama_base_url") or _sc.get("ollama_host", "http://host.docker.internal:11434")
        logger.info("   ├─ Endpoint: %s", ollama_endpoint)
        logger.info("   └─ Status: Connecting...\n")
        ollama = None
        try:
            from .ollama_client import OllamaClient

            ollama = OllamaClient()
            logger.info("   [OK] OllamaClient initialized")

            # Model selection priority:
            # 1. UI-selected model (preferred_model from task request)
            # 2. OllamaClient's configured model (from DEFAULT_OLLAMA_MODEL env var)
            # 3. Dynamic discovery from installed models (sorted by size)
            # No hardcoded model names — if nothing works, falls through to cloud providers.
            if preferred_model:
                model_list = [preferred_model]
                logger.info("   ├─ Using UI-selected model: %s", preferred_model)
            else:
                # Read pipeline_writer_model from DB first (DB-first config)
                try:
                    from services.site_config import site_config
                    db_model = site_config.get("pipeline_writer_model", "")
                    if db_model:
                        # Strip "ollama/" prefix if present
                        db_model = db_model.removeprefix("ollama/")
                except Exception:
                    db_model = ""

                if db_model:
                    resolved = db_model
                    logger.info("   ├─ Primary model (from DB): %s", resolved)
                else:
                    resolved = await ollama.resolve_model()
                    logger.info("   ├─ Primary model (auto): %s", resolved)
                model_list = [resolved]

                # Read fallback model from DB
                try:
                    db_fallback = site_config.get("pipeline_fallback_model", "")
                    if db_fallback:
                        db_fallback = db_fallback.removeprefix("ollama/")
                        if db_fallback != resolved:
                            model_list.append(db_fallback)
                except Exception:
                    pass

                # Add other installed models as fallbacks (smaller first for speed)
                try:
                    available = await ollama.list_models()
                    fallbacks = [
                        m["name"]
                        for m in sorted(
                            available,
                            key=lambda x: x.get("size", 0),
                        )
                        if "embed" not in m.get("name", "").lower() and m["name"] not in model_list
                    ]
                    model_list.extend(fallbacks)
                except Exception as e:
                    logger.warning("   Could not discover fallback models: %s", e)

                logger.info(
                    "   ├─ Will try %d model(s): %s",
                    len(model_list), [m.split(":")[0] for m in model_list[:5]],
                )
            for model_idx, model_name in enumerate(model_list, 1):
                try:
                    logger.info("   └─ Testing model %d/%d: %s", model_idx, len(model_list), model_name)
                    metrics["generation_attempts"] += 1

                    logger.info("      Generating content...")

                    # Calculate max tokens: markdown content + headers + lists need ~2-2.5 tokens per word
                    # Using 4x multiplier to prevent truncation mid-sentence.
                    # Thinking models (qwen3, qwen3.5, glm-4.7) use part of the budget for
                    # internal reasoning, so we give them extra headroom.
                    _is_thinking = any(t in model_name.lower() for t in ("qwen3", "glm-4", "deepseek-r1"))
                    _multiplier = 6.0 if _is_thinking else 4.0
                    max_tokens = int(target_length * _multiplier)
                    logger.debug("      Max tokens: %d (target_length: %d, thinking=%s)", max_tokens, target_length, _is_thinking)

                    response = await ollama.generate(
                        prompt=generation_prompt,
                        system=system_prompt,
                        model=model_name,
                        stream=False,
                        max_tokens=max_tokens,  # Set explicit token limit for proper word count control
                    )

                    generated_content = self._extract_ollama_response(response)

                    logger.info(
                        "      Final check: bool(content)=%s, len=%d, threshold=100",
                        bool(generated_content), len(generated_content),
                    )
                    if generated_content and len(generated_content) > 100:
                        logger.info(
                            "      [OK] Content generated: %d characters", len(generated_content)
                        )

                        # Self-check: Validate content quality
                        logger.info("      Validating content quality...")
                        validation = self._validate_content(generated_content, topic, target_length)
                        metrics["validation_results"].append(
                            {
                                "attempt": metrics["generation_attempts"],
                                "score": validation.quality_score,
                                "issues": validation.issues,
                                "passed": validation.is_valid,
                            }
                        )

                        word_count = len(generated_content.split())
                        logger.info(
                            "      Quality Score: %.1f/%s | Words: %d | Issues: %d",
                            validation.quality_score, self.quality_threshold, word_count, len(validation.issues),
                        )

                        if validation.issues:
                            for issue in validation.issues:
                                logger.debug("         %s", issue)

                        # If content passes QA, return it
                        if validation.is_valid:
                            logger.info("      [OK] Content APPROVED by QA")
                            metrics["model_used"] = model_name
                            metrics["models_used_by_phase"]["draft"] = metrics["model_used"]
                            metrics["final_quality_score"] = validation.quality_score
                            metrics["generation_time_seconds"] = time.time() - start_time
                            # Track Ollama usage — NOT free, electricity costs money
                            # Cost is calculated from GPU power draw * duration by the Ollama client
                            electricity_cost = response.get("cost", 0.0)
                            duration_s = response.get("duration_seconds", 0.0)
                            input_tokens = response.get("prompt_tokens", 0)
                            est_tokens = response.get("tokens", 0) or int(len(generated_content.split()) * 1.3)
                            metrics["cost_log"] = {
                                "provider": "ollama", "model": model_name,
                                "input_tokens": input_tokens,
                                "output_tokens": est_tokens,
                                "cost_usd": round(electricity_cost, 6), "phase": "content_generation",
                                "duration_seconds": round(duration_s, 2),
                            }
                            logger.info("\n%s", "=" * 80)
                            logger.info("[OK] GENERATION COMPLETE")
                            logger.info("   Model: %s", metrics["model_used"])
                            logger.info(
                                "   Quality: %.1f/%s", validation.quality_score, self.quality_threshold
                            )
                            logger.info("   Time: %.1fs", metrics["generation_time_seconds"])
                            logger.info("%s\n", "=" * 80)
                            return generated_content, metrics["model_used"], metrics

                        # If content fails QA but we have refinement attempts left, try to improve
                        refinement_result = await self._refine_ollama_content(
                            ollama, model_name, generated_content, validation, ctx
                        )
                        if refinement_result:
                            return refinement_result

                        # Check if refinement produced updated content
                        if "_refined_content" in ctx:
                            generated_content = ctx.pop("_refined_content")

                        # If still not passing after refinement, return best attempt
                        if metrics["generation_attempts"] == len(model_list):
                            logger.warning(
                                "      Content below quality threshold but no more refinements available"
                            )
                            metrics["model_used"] = model_name
                            metrics["models_used_by_phase"]["draft"] = metrics[
                                "model_used"
                            ]  # Track phase
                            metrics["final_quality_score"] = validation.quality_score
                            metrics["generation_time_seconds"] = time.time() - start_time
                            logger.info("\n%s", "=" * 80)
                            logger.warning("GENERATION COMPLETE (below quality threshold)")
                            logger.info("   Model: %s", metrics["model_used"])
                            logger.info(
                                "   Quality: %.1f/%s", validation.quality_score, self.quality_threshold
                            )
                            logger.info("   Time: %.1fs", metrics["generation_time_seconds"])
                            logger.info("%s\n", "=" * 80)
                            return generated_content, metrics["model_used"], metrics
                    else:
                        logger.warning("      [FAIL] Generated content too short or empty")

                except asyncio.TimeoutError:
                    # Explicitly catch timeout - model too slow or server unresponsive
                    error_msg = f"Timeout exceeded with {model_name}"
                    logger.warning(
                        "Ollama model %s timed out: %s", model_name, error_msg, exc_info=True
                    )
                    attempts.append(("Ollama", error_msg))
                    continue
                except Exception as e:
                    # Catch other errors (500 errors, connection issues, etc.)
                    error_msg = str(e)[:150]  # Truncate long error messages
                    logger.warning("Ollama model %s failed: %s", model_name, error_msg, exc_info=True)
                    attempts.append(("Ollama", f"{model_name}: {error_msg}"))
                    continue

        except Exception as e:
            logger.warning("Ollama generation failed: %s", e, exc_info=True)
            if not attempts:  # Only append if attempts list is still empty
                attempts.append(("Ollama", str(e)[:150]))
        finally:
            # Close the httpx connection pool held by OllamaClient. Without
            # this, every content-generation attempt leaked an AsyncClient
            # (the client was only closed inside the success-return path).
            if ollama is not None:
                try:
                    await ollama.close()
                except Exception:
                    pass

        return None

    async def _try_huggingface(
        self, ctx: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Try HuggingFace provider. Returns result tuple or None."""
        metrics = ctx["metrics"]
        generation_prompt = ctx["generation_prompt"]
        target_length = ctx["target_length"]
        topic = ctx["topic"]
        attempts = ctx["attempts"]
        start_time = ctx["start_time"]

        # 2. Try HuggingFace (free tier, online)
        if not ProviderChecker.is_huggingface_available():
            return None

        logger.info("Attempting content generation with HuggingFace...")
        try:
            from .huggingface_client import HuggingFaceClient

            hf = HuggingFaceClient(api_token=ProviderChecker.get_huggingface_api_key())

            # Try recommended models
            for model_id in [
                "mistralai/Mistral-7B-Instruct-v0.1",
                "meta-llama/Llama-2-7b-chat",
                "tiiuae/falcon-7b-instruct",
            ]:
                try:
                    logger.debug("Trying HuggingFace model: %s", model_id)
                    metrics["generation_attempts"] += 1

                    # Calculate max tokens for HuggingFace generation (2.5x multiplier)
                    max_tokens_hf = int(target_length * 3.0)
                    generated_content = await asyncio.wait_for(
                        hf.generate(
                            model=model_id,
                            prompt=generation_prompt,
                            max_tokens=max_tokens_hf,  # 2.0x multiplier ensures full content generation
                            temperature=0.7,
                        ),
                        timeout=60,
                    )

                    if generated_content and len(generated_content) > 100:
                        # Self-check: Validate content quality
                        validation = self._validate_content(generated_content, topic, target_length)
                        metrics["validation_results"].append(
                            {
                                "attempt": metrics["generation_attempts"],
                                "score": validation.quality_score,
                                "issues": validation.issues,
                                "passed": validation.is_valid,
                            }
                        )

                        if validation.is_valid:
                            metrics["model_used"] = model_id.split("/")[-1]
                            metrics["models_used_by_phase"]["draft"] = metrics[
                                "model_used"
                            ]  # Track phase
                            metrics["final_quality_score"] = validation.quality_score
                            metrics["generation_time_seconds"] = time.time() - start_time
                            logger.info("[OK] Content generated and approved with HuggingFace")
                            return generated_content, metrics["model_used"], metrics

                except asyncio.TimeoutError:
                    logger.debug("HuggingFace model %s timed out", model_id)
                    continue
                except Exception as e:
                    logger.debug("HuggingFace model %s failed: %s", model_id, e)
                    continue

        except Exception as e:
            logger.warning("HuggingFace generation failed: %s", e, exc_info=True)
            attempts.append(("HuggingFace", str(e)))

        return None

    # Paid provider methods (_try_anthropic, _try_gemini_fallback, _try_openai) removed.
    # Policy: Ollama-only. See session 55 notes.

    def _handle_all_providers_failed(self, ctx: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        """Handle the case where all AI providers failed. Returns fallback content."""
        metrics = ctx["metrics"]
        attempts = ctx["attempts"]
        start_time = ctx["start_time"]
        use_ollama = ctx["use_ollama"]
        topic = ctx["topic"]
        style = ctx["style"]
        tone = ctx["tone"]
        tags = ctx["tags"]

        # If all models fail, use fallback
        logger.warning("\n%s", "=" * 80)
        logger.warning("[FAIL] ALL AI MODELS FAILED - Using fallback template")
        logger.warning("%s", "=" * 80)
        logger.warning("Attempts made: %d", len(attempts))
        for provider, error in attempts:
            logger.warning("   [FAIL] %s: %s", provider, error)
        logger.warning("Provider summary:")
        logger.warning("   - Ollama:      %s (tried/available)", use_ollama)
        logger.warning(
            "   - HuggingFace: %s (token available)",
            ProviderChecker.is_huggingface_available(),
        )
        logger.warning("%s\n", "=" * 80)

        # Capture in Sentry as a distinct message so alert rules can target it.
        # Generated content will be a stub template, not AI output (issue #556).
        try:
            import sentry_sdk  # pylint: disable=import-outside-toplevel

            if sentry_sdk.is_initialized():  # type: ignore[attr-defined]
                sentry_sdk.capture_message(  # type: ignore[attr-defined]
                    "ALL AI MODELS FAILED — serving fallback template content",
                    level="error",
                    extras={"attempts": [{"provider": p, "error": e} for p, e in attempts]},
                )
        except Exception:  # pylint: disable=broad-except
            # Never let Sentry integration block content delivery, but do log the failure
            logger.error(
                "[ai_content_generator] Failed to capture all-models-failed event in Sentry",
                exc_info=True,
            )

        fallback_content = self._generate_fallback_content(topic, style, tone, tags)
        metrics["model_used"] = "Fallback (no AI models available)"
        metrics["models_used_by_phase"]["draft"] = metrics["model_used"]  # Track phase
        metrics["final_quality_score"] = 0.0
        metrics["generation_time_seconds"] = time.time() - start_time
        return fallback_content, metrics["model_used"], metrics

    async def generate_blog_post(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: list[str],
        preferred_model: str | None = None,
        preferred_provider: str | None = None,
        writing_style_context: str | None = None,
        research_context: str | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Generate a blog post using best available model with self-checking.

        Features:
        - User-selected model support (respects frontend UI choices)
        - Intelligent provider fallback (Ollama → HuggingFace)
        - Self-validation and quality checking
        - Refinement loop for rejected content
        - Full metrics tracking

        Args:
            topic: Blog post topic
            style: Writing style (technical, narrative, etc.)
            tone: Content tone (professional, casual, etc.)
            target_length: Target word count
            tags: Content tags
            preferred_model: User-selected model (e.g., 'qwen3.5:35b')
            preferred_provider: User-selected provider ('ollama', 'huggingface')
            writing_style_context: Optional writing style excerpts to include in the prompt
                for voice/tone matching

        Returns:
            Tuple of (content, model_used, metrics_dict)
        """
        # Populate internal links cache so the prompt includes real links to our posts
        await self._populate_internal_links_cache()

        ctx = await self._prepare_generation_context(
            topic, style, tone, target_length, tags, preferred_model, preferred_provider,
            writing_style_context=writing_style_context,
            research_context=research_context,
        )

        # 1. Try Ollama (local, free, no internet, RTX 5070 optimized)
        result = await self._try_ollama(ctx)
        if result:
            return result

        # 2. Try HuggingFace (free tier, online)
        result = await self._try_huggingface(ctx)
        if result:
            return result

        # All providers failed — return fallback template
        return self._handle_all_providers_failed(ctx)

    def _generate_fallback_content(
        self,
        topic: str,
        style: str,
        tone: str,
        tags: list[str],
    ) -> str:
        """Generate fallback content when AI models are unavailable"""

        tag_str = ", ".join(tags) if tags else "general"

        return f"""# {topic}

## Introduction

{topic} is an important area that deserves careful attention and exploration. This article provides insights and practical considerations for understanding this topic better.

## Key Aspects

### Understanding the Basics

When exploring {topic}, it's essential to understand the foundational concepts. The {style} approach ensures clarity and depth, delivered in a {tone.lower()} manner that resonates with our audience.

### Practical Applications

{topic} has several real-world applications:

- **Strategic Implementation**: How to apply these concepts in practice
- **Common Challenges**: Obstacles to watch for and how to overcome them
- **Success Metrics**: How to measure progress and impact

### Emerging Trends

Looking ahead, {topic} continues to evolve:

- New opportunities and possibilities
- Challenges that may emerge
- Best practices for staying current

## Why This Matters

Understanding {topic} provides significant advantages:

1. Better decision-making capabilities
2. Improved efficiency and effectiveness
3. Competitive advantage in your field

## Practical Next Steps

Ready to dive deeper into {topic}?

1. **Learn More**: Research additional resources and case studies
2. **Experiment**: Try implementing one strategy this week
3. **Share**: Discuss what you've learned with colleagues
4. **Iterate**: Refine your approach based on results

## Conclusion

{topic} represents a valuable area for professional and personal development. By understanding the concepts and practices outlined in this article, you can make better decisions and achieve improved outcomes.

Take action today—the insights you gain will compound over time.

---

*Tags: {tag_str}*
*Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}*
"""


# Global instance
_generator: AIContentGenerator | None = None


def get_content_generator() -> AIContentGenerator:
    """Get or create global content generator"""
    global _generator
    if _generator is None:
        _generator = AIContentGenerator()
    return _generator


async def test_generation():
    """Test content generation"""
    logger = get_logger(__name__)
    generator = get_content_generator()

    logger.info("Testing content generation...")
    logger.debug("Ollama available: %s", generator.ollama_available)
    logger.debug("HuggingFace token: %s", "yes" if generator.hf_token else "no")

    logger.info("Generating blog post...")
    content, model, metrics = await generator.generate_blog_post(
        topic="AI-Powered Content Creation for Modern Marketing",
        style="technical",
        tone="professional",
        target_length=1500,
        tags=["AI", "Marketing", "Technology"],
    )

    logger.info("Model used: %s", model)
    logger.info("Content length: %d characters", len(content))
    logger.info("Quality score: %s/10", metrics["final_quality_score"])
    logger.info("Generation attempts: %s", metrics["generation_attempts"])
    logger.info("Time taken: %.2f seconds", metrics["generation_time_seconds"])
    logger.info("First 500 characters:\n%s...", content[:500])


if __name__ == "__main__":
    asyncio.run(test_generation())
