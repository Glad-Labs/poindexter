"""
Unified AI Content Generator Service

Handles blog post generation on local Ollama with a fallback template
when the model is unreachable. This is the legacy Ollama-native
orchestrator — the stages-based pipeline in services/stages/* is the
plugin-aware replacement and will eventually delete this file
wholesale.

Features:
- Ollama model discovery + retry across installed models
- Self-checking and validation throughout generation
- Quality assurance with refinement loops
- Content metrics and performance tracking
- Electricity cost tracking from the Ollama result dict

ASYNC-FIRST: All I/O operations use httpx async client (no blocking calls)

v2.5 deliberate non-migration: this file uses OllamaClient-specific
features (resolve_model, list_models, result["cost"]) that don't fit
cleanly behind the LLMProvider Protocol without lossy adaptation.
Same precedent as services/multi_model_qa.py.

HuggingFace fallback path removed in v2.8 per the no-paid-APIs policy —
the HF gate was always returning False via ProviderChecker in
production, so the ~70 LOC of HF retry plumbing was dead weight.
"""

import asyncio
import re
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

import httpx

from services.logger_config import get_logger

from .prompt_manager import get_prompt_manager
from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


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
        # #198: tunable via app_settings so operators can widen/tighten
        # refinement loops without a redeploy.
        _sc = site_config
        self.max_refinement_attempts = _sc.get_int("content_gen_max_refinement_attempts", 3)

        logger.info("AIContentGenerator initialized (Ollama check deferred to first async call)")

    async def _check_ollama_async(self):
        """Async check if Ollama is running - call this once before using Ollama"""
        if self.ollama_checked:
            logger.debug("Ollama already checked previously: %s", self.ollama_available)
            return

        _sc = site_config
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
            _sc = site_config
            site_url = _sc.get("site_url", "")

            import asyncpg
            # brain.bootstrap.resolve_database_url() is the canonical DSN
            # resolver across the project — it already checks
            # LOCAL_DATABASE_URL / DATABASE_URL / bootstrap.toml in order,
            # so services shouldn't reach into os.getenv directly.
            try:
                import sys as _sys
                from pathlib import Path as _Path
                for _p in _Path(__file__).resolve().parents:
                    if (_p / "brain" / "bootstrap.py").is_file():
                        if str(_p) not in _sys.path:
                            _sys.path.insert(0, str(_p))
                        break
                from brain.bootstrap import resolve_database_url
                dsn = resolve_database_url() or ""
            except Exception:
                dsn = ""
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
        target_audience: str | None = None,
        domain: str | None = None,
    ) -> tuple[str, str, Any]:
        """Load system prompt, generation prompt, and refinement prompt getter from prompt manager.

        Returns (system_prompt, generation_prompt, get_refinement_prompt_fn).

        ``target_audience`` and ``domain`` flow in from the
        ``pipeline_tasks`` row (target_audience column + category column,
        respectively) via ``content_router_service`` -> ``stages.
        generate_content`` -> ``generate_blog_post``. Both are required
        placeholders in the YAML default
        ``blog_generation.blog_system_prompt``; missing them used to
        fall through to a DB-override prompt that didn't reference them,
        but Phase 2 of poindexter#47 dropped that DB layer (commit
        5b2cc543), so the call site has to supply them now. See
        Glad-Labs/poindexter#369.

        When a task row has these columns NULL (older tasks created
        before the niche-pivot wiring) the values fall back to visible
        sentinel strings -- ``"a general audience"`` / ``"general"`` --
        chosen so the operator can grep for them in published posts and
        retrofit the missing data. Per CLAUDE.md
        ``feedback_no_silent_defaults``: the fallback is observable, not
        an invisible empty-string substitution.
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
            # Word-count window buffers — tunable via app_settings (#198).
            _sc = site_config
            _min_ratio = _sc.get_float("content_gen_min_word_ratio", 0.9)
            _max_ratio = _sc.get_float("content_gen_max_word_ratio", 1.1)
            min_words = int(target_length * _min_ratio)
            max_words = int(target_length * _max_ratio)
            # Sentinels for missing target_audience / domain are
            # intentionally visible strings so the operator can spot
            # them in rendered prompts + published posts and retrofit
            # the missing pipeline_tasks columns. Per CLAUDE.md
            # feedback_no_silent_defaults.
            _audience = (target_audience or "").strip() or "a general audience"
            _domain = (domain or "").strip() or "general"
            if not (target_audience or "").strip():
                logger.warning(
                    "[blog_system_prompt] target_audience missing -- "
                    "rendering with sentinel %r. Backfill the "
                    "pipeline_tasks.target_audience column for this "
                    "task to silence this warning.",
                    _audience,
                )
            if not (domain or "").strip():
                logger.warning(
                    "[blog_system_prompt] domain missing -- rendering "
                    "with sentinel %r. Backfill the "
                    "pipeline_tasks.category column for this task to "
                    "silence this warning.",
                    _domain,
                )
            system_prompt = pm.get_prompt(
                "blog_generation.blog_system_prompt",
                style=style,
                tone=tone,
                target_length=target_length,
                min_words=min_words,
                max_words=max_words,
                tags=", ".join(tags) if tags else "general",
                target_audience=_audience,
                domain=_domain,
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

            # Kwargs match BOTH the YAML default placeholders
            # ({topic}, {style}, {tone}, {target_length},
            # {research_context}) AND the historical premium-prompt
            # placeholders ({target_audience}, {primary_keyword},
            # {internal_link_titles}). str.format ignores extras, so
            # passing both shapes keeps Langfuse premium overrides
            # working without the call site needing to know which
            # variant is live. See Glad-Labs/poindexter#369 for why
            # the historical-only set started crashing once the
            # prompt_templates DB layer was retired (commit 5b2cc543).
            generation_prompt = pm.get_prompt(
                "blog_generation.initial_draft",
                topic=topic,
                target_audience=_audience,
                primary_keyword=tags[0] if tags else "",
                research_context=research_context,
                internal_link_titles=internal_links_str,
                target_length=target_length,
                word_count=target_length,  # legacy alias for premium override
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
                # Same dual-shape kwarg pattern as the initial_draft
                # call above -- {content}/{feedback} are the YAML
                # default placeholders; {draft}/{critique}/
                # {word_count_constraint}/{target_audience} are the
                # historical premium-prompt placeholders. Pass both
                # so either variant renders cleanly. See #369.
                _critique = (
                    f"FEEDBACK: {feedback}\nISSUES: {chr(10).join(issues)}"
                )
                return pm.get_prompt(
                    "blog_generation.iterative_refinement",
                    content=content,
                    feedback=_critique,
                    draft=content,  # legacy alias for premium override
                    critique=_critique,  # legacy alias for premium override
                    word_count_constraint=f"Target: {target_length} words",
                    target_audience=_audience,
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
        target_audience: str | None = None,
        domain: str | None = None,
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
        logger.info("%s\n", "=" * 80)

        # Provider priority: Ollama → template (HF removed in v2.8).
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
            target_audience=target_audience,
            domain=domain,
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
        # Calculate max tokens for refinement pass — extra headroom for thinking models.
        # Token multipliers are tunable via app_settings (#198).
        _sc = site_config
        _is_thinking_refine = any(t in model_name.lower() for t in ("qwen3", "glm-4", "deepseek-r1"))
        _thinking_mult = _sc.get_float("content_gen_token_mult_thinking", 7.0)
        _standard_mult = _sc.get_float("content_gen_token_mult_standard", 4.5)
        max_tokens_refinement = int(target_length * (_thinking_mult if _is_thinking_refine else _standard_mult))
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
        _sc = site_config
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
            # 2. OllamaClient's configured model (from app_settings.default_ollama_model)
            # 3. Dynamic discovery from installed models (sorted by size)
            # No hardcoded model names — if nothing works, falls through to cloud providers.
            if preferred_model:
                model_list = [preferred_model]
                logger.info("   ├─ Using UI-selected model: %s", preferred_model)
            else:
                # Read pipeline_writer_model from DB first (DB-first config)
                try:
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
                with suppress(Exception):
                    # Teardown path: raising here would mask whatever put
                    # us in finally. Leaked sockets are worse than silenced
                    # close errors only if close() genuinely does work,
                    # and in practice httpx.aclose is already best-effort.
                    await ollama.close()

        return None

    # v2.8 removed _try_huggingface + all paid-provider fallback methods.
    # Only the Ollama path + fallback template remain.

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
        target_audience: str | None = None,
        domain: str | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Generate a blog post using best available model with self-checking.

        Features:
        - User-selected model support (respects frontend UI choices)
        - Ollama-only generation with fallback template if unreachable
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
            preferred_provider: User-selected provider (accepts 'ollama';
                any other value routes straight to the fallback template)
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
            target_audience=target_audience,
            domain=domain,
        )

        # 1. Try Ollama (local, free, no internet, RTX 5070 optimized)
        result = await self._try_ollama(ctx)
        if result:
            return result

        # Ollama failed — return fallback template. (HF path removed v2.8.)
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


# ---------------------------------------------------------------------------
# Writer-RAG-mode helpers (Task 14 of the niche-discovery RAG pivot)
# ---------------------------------------------------------------------------
#
# The four writer modes in services/writer_rag_modes/* call into these two
# functions to render a draft from a topic + angle + retrieved snippets.
# They are deliberately thin wrappers around _ollama_chat_json so unit tests
# can monkeypatch this module to avoid a real Ollama call.
#
# Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
# Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 14)


async def _resolve_rag_writer_model() -> str:
    """Resolve writer model for the RAG ``generate_with_*`` helpers.

    Lane B sweep migration. Order:

    1. ``resolve_tier_model(pool, 'standard')`` — operator-tuned tier
       mapping (``app_settings.cost_tier.standard.model``).
    2. ``app_settings[pipeline_writer_model]`` — legacy per-call-site
       backstop. Pre-existing setting; the literal "glm-4.7-5090:latest"
       default that lived in the inline lookup is removed per
       ``feedback_no_silent_defaults.md``.
    3. ``notify_operator()`` + raise — fail loud.

    Returns the bare model name (``ollama/`` prefix stripped).

    Note: the model-class detection at lines 607 + 776
    (``is_thinking_model = any(t in model.lower() for t in ...)``) is
    NOT a tier-migration target — it branches on model identity to pick
    a token budget, not to pick which model to call. Leave it in place
    pending the future ``is_thinking_model`` registry tracked in the
    Lane B inventory.
    """
    from services.integrations.operator_notify import notify_operator
    from services.llm_providers.dispatcher import resolve_tier_model

    pool = getattr(site_config, "_pool", None)
    if pool is not None:
        try:
            return (await resolve_tier_model(pool, "standard")).removeprefix(
                "ollama/",
            )
        except (RuntimeError, ValueError, AttributeError) as exc:
            tier_exc: Exception | None = exc
        else:
            tier_exc = None
    else:
        tier_exc = RuntimeError("no asyncpg pool available")

    fallback = site_config.get("pipeline_writer_model") or ""
    if fallback:
        await notify_operator(
            f"ai_content_generator (RAG): cost_tier='standard' resolution "
            f"failed ({tier_exc}); falling back to "
            f"pipeline_writer_model={fallback!r}",
            critical=False,
            site_config=site_config,
        )
        return fallback.removeprefix("ollama/")

    await notify_operator(
        f"ai_content_generator (RAG): cost_tier='standard' has no model AND "
        f"pipeline_writer_model is empty — RAG draft generation aborted: {tier_exc}",
        critical=True,
        site_config=site_config,
    )
    raise RuntimeError(
        "ai_content_generator: no writer model resolvable via tier or "
        "pipeline_writer_model setting"
    ) from tier_exc


async def generate_with_context(
    *, topic: str, angle: str, snippets: list[dict],
    extra_instructions: str | None = None,
) -> str:
    """Build a prompt using the snippets as background context, generate the
    draft. Wraps the existing generation path; tests can monkeypatch here.

    Per-snippet length cap is operator-tunable via
    ``writer_rag_context_snippet_max_chars``. Writer model is resolved
    via the cost-tier API (Lane B sweep) — operators tune
    ``app_settings.cost_tier.standard.model`` or the legacy
    ``pipeline_writer_model`` per-call-site backstop.
    """
    from services.topic_ranking import _ollama_chat_json

    snippet_max_chars = site_config.get_int(
        "writer_rag_context_snippet_max_chars", 500,
    )
    model = await _resolve_rag_writer_model()
    snippet_block = "\n".join(
        f"[{s['source']}/{s['ref']}] {s['snippet'][:snippet_max_chars]}"
        for s in snippets if s.get('snippet')
    )
    instructions = extra_instructions or ""
    prompt = f"""Write a blog post on the topic: "{topic}" with this angle: "{angle}".

{instructions}

Background context (cite where relevant):
{snippet_block}

Return the full post body in Markdown.
"""
    return await _ollama_chat_json(prompt, model=model)


async def generate_with_outline(
    *, topic: str, outline: dict, snippets: list[dict],
) -> str:
    """Expand a structured outline into a full blog post draft.

    Used by the STORY_SPINE writer mode after it preprocesses the top
    snippets into a {hook, what_happened, why_it_matters, ...} skeleton.

    Per-snippet length cap is operator-tunable via
    ``writer_rag_context_snippet_max_chars``. Writer model is resolved
    via the cost-tier API (Lane B sweep) — operators tune
    ``app_settings.cost_tier.standard.model`` or the legacy
    ``pipeline_writer_model`` per-call-site backstop.
    """
    from services.topic_ranking import _ollama_chat_json

    snippet_max_chars = site_config.get_int(
        "writer_rag_context_snippet_max_chars", 500,
    )
    model = await _resolve_rag_writer_model()
    snippet_block = "\n".join(
        f"[{s['source']}/{s['ref']}] {s['snippet'][:snippet_max_chars]}"
        for s in snippets if s.get('snippet')
    )
    outline_block = "\n".join(f"{k.replace('_',' ').title()}: {v}" for k, v in outline.items())
    prompt = f"""Expand the following outline into a full blog post.

Topic: {topic}
Outline:
{outline_block}

Background snippets to draw on:
{snippet_block}

Return the full post body in Markdown.
"""
    return await _ollama_chat_json(prompt, model=model)


if __name__ == "__main__":
    asyncio.run(test_generation())
