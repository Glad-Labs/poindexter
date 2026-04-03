"""
Unified AI Content Generator Service

Handles blog post generation with fallback through multiple providers:
1. Local Ollama (free, RTX 5070 optimized)
2. HuggingFace (free tier)
3. Anthropic Claude (paid cloud)
4. Google Gemini (paid cloud)
5. OpenAI (paid cloud)

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
        issues: Optional[list[str]] = None,
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
            f"   HuggingFace token: {'✓ set' if ProviderChecker.is_huggingface_available() else '✗ not set'}"
        )
        logger.debug(
            f"   Gemini/Google key: {'✓ set' if ProviderChecker.is_gemini_available() else '✗ not set'}"
        )

    async def _check_ollama_async(self):
        """Async check if Ollama is running - call this once before using Ollama"""
        if self.ollama_checked:
            logger.debug(f"ℹ️ Ollama already checked previously: {self.ollama_available}")
            return

        logger.info("🔍 Checking if Ollama server is running...")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                logger.debug("   → Sending request to http://localhost:11434/api/tags")
                response = await client.get("http://localhost:11434/api/tags")
                self.ollama_available = response.status_code == 200
                logger.debug(f"   ← Response status: {response.status_code}")

            if self.ollama_available:
                logger.info("✅ Ollama IS running at http://localhost:11434")
            else:
                logger.warning(f"⚠️ Ollama returned non-200 status: {response.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Ollama health check failed: {type(e).__name__}: {e}", exc_info=True)
            self.ollama_available = False
        finally:
            self.ollama_checked = True
            logger.debug(f"   → Ollama checked. Result: {self.ollama_available}")

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
            feedback = f"✓ Content approved (quality score: {score:.1f}/10)"
        else:
            feedback = f"✗ Content needs improvement (quality score: {score:.1f}/10, threshold: {self.quality_threshold})"

        logger.info(f"Content validation: {feedback}")
        if issues:
            logger.debug(f"Issues found: {issues}")

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
    ) -> Tuple[str, str, Any]:
        """Load system prompt, generation prompt, and refinement prompt getter from prompt manager.

        Returns (system_prompt, generation_prompt, get_refinement_prompt_fn).
        """
        # Get prompt manager for centralized prompt management
        try:
            pm = get_prompt_manager()
            logger.info("✓ Prompt manager loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load prompt manager: {e}", exc_info=True)
            raise

        # Fetch prompts from centralized manager instead of hardcoding
        # This ensures all prompts are versioned, documented, and easy to maintain
        try:
            logger.info("📝 Loading system prompt...")
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
            logger.info(f"✓ System prompt loaded ({len(system_prompt)} chars)")
        except Exception as e:
            logger.error(f"Failed to load system prompt: {e}", exc_info=True)
            raise

        try:
            logger.info("📝 Loading generation prompt...")
            # Format internal_link_titles as a string for template
            internal_link_titles = (
                []
            )  # Initialize empty list for internal links (future: fetch from existing posts)
            internal_links_str = "\n".join(internal_link_titles) if internal_link_titles else ""

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
            logger.info(f"✓ Generation prompt loaded ({len(generation_prompt)} chars)")
        except Exception as e:
            logger.error(
                f"Failed to load generation prompt: {type(e).__name__}: {e}", exc_info=True
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
                logger.error(f"Failed to load refinement prompt: {e}", exc_info=True)
                raise

        return system_prompt, generation_prompt, get_refinement_prompt

    async def _prepare_generation_context(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: list[str],
        preferred_model: Optional[str],
        preferred_provider: Optional[str],
        writing_style_context: Optional[str] = None,
        research_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set up logging, check providers, load prompts, and initialize metrics.

        Returns a context dict containing all shared state needed by provider methods.
        """
        logger.info(f"\n{'='*80}")
        logger.info("🎬 BLOG GENERATION STARTED")
        logger.info(f"{'='*80}")
        logger.info(f"📌 Topic: {topic}")
        logger.info(f"📌 Style: {style} | Tone: {tone}")
        logger.info(f"📌 Target length: {target_length} words | Tags: {tags}")
        logger.info(f"📌 Quality threshold: {self.quality_threshold}")
        logger.info(f"📌 Preferred model: {preferred_model or 'auto'}")
        logger.info(f"📌 Preferred provider: {preferred_provider or 'auto'}")
        logger.info(f"📌 Anthropic key: {'✓' if ProviderChecker.is_anthropic_available() else '✗'}")
        logger.info(f"📌 Gemini key: {'✓' if ProviderChecker.is_gemini_available() else '✗'}")
        logger.info(f"📌 OpenAI key: {'✓' if ProviderChecker.is_openai_available() else '✗'}")
        logger.info(
            f"📌 HuggingFace token: {'✓' if ProviderChecker.is_huggingface_available() else '✗'}"
        )
        logger.info(f"{'='*80}\n")

        # Check if Ollama is available (async check, happens once)
        # IMPORTANT: Default provider priority when none specified:
        # 1. Ollama (if available) - local, free
        # 2. HuggingFace (if token available) - free tier
        # 3. Anthropic Claude (if key available) - paid cloud
        # 4. Google Gemini (if key available) - paid cloud
        # 5. OpenAI (if key available) - paid cloud

        # Determine effective provider preference
        # When no provider specified, let the fallback chain handle it:
        # Ollama → HuggingFace → Anthropic → OpenAI → template
        effective_provider = preferred_provider
        if not effective_provider:
            logger.info("📌 No provider specified, will try fallback chain (Ollama first)")

        skip_ollama = effective_provider and effective_provider.lower() not in ["ollama", "auto"]

        # Use local variable to avoid polluting instance state across requests
        use_ollama = False
        if skip_ollama:
            logger.info(f"📌 Skipping Ollama (user selected: {effective_provider})\n")
            use_ollama = False
        else:
            await self._check_ollama_async()
            use_ollama = self.ollama_available
            logger.info(f"📌 Ollama check result: {use_ollama}\n")

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
                    "gemini_key_available": ProviderChecker.is_gemini_available(),
                    "gemini_attempted": False,
                    "gemini_succeeded": False,
                    "gemini_error": None,
                    "ollama_available": use_ollama,
                    "huggingface_token_available": ProviderChecker.is_huggingface_available(),
                },
            },
        }

        start_time = time.time()

        # Log provider decision tree
        logger.info(f"\n{'='*80}")
        logger.info("🔍 PROVIDER DECISION TREE:")
        logger.info(f"{'='*80}")
        logger.info(f"   User selection: provider={preferred_provider}, model={preferred_model}")
        logger.info(f"   Effective provider: {effective_provider}")
        logger.info("")
        logger.info("   Provider Status:")
        logger.info(
            f"   ├─ Anthropic (cloud):  {'✓ key set' if ProviderChecker.is_anthropic_available() else '✗ no key'}"
        )
        logger.info(
            f"   ├─ Gemini (cloud):     {'✓ key set' if ProviderChecker.is_gemini_available() else '✗ no key'}"
        )
        logger.info(
            f"   ├─ OpenAI (cloud):     {'✓ key set' if ProviderChecker.is_openai_available() else '✗ no key'}"
        )
        logger.info(
            f"   ├─ Ollama (local):     {'✓ available' if use_ollama else '✗ not available/skipped'}"
        )
        logger.info(
            f"   ├─ HuggingFace (cloud): {'✓ token set' if ProviderChecker.is_huggingface_available() else '✗ no token'}"
        )
        logger.info("   └─ Fallback:           Available (generic template)")
        logger.info(f"{'='*80}\n")

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

    async def _try_gemini_user_selected(
        self, ctx: Dict[str, Any]
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """Try user-selected Gemini provider. Returns result tuple or None."""
        effective_provider = ctx["effective_provider"]
        preferred_model = ctx["preferred_model"]
        metrics = ctx["metrics"]
        system_prompt = ctx["system_prompt"]
        generation_prompt = ctx["generation_prompt"]
        target_length = ctx["target_length"]
        topic = ctx["topic"]
        attempts = ctx["attempts"]
        start_time = ctx["start_time"]

        if not (
            effective_provider
            and effective_provider.lower() == "gemini"
            and ProviderChecker.is_gemini_available()
        ):
            return None

        logger.info("🎯 PLAN: Will attempt Gemini (user selection)\n")
        logger.info(
            f"🎯 Attempting Gemini (provider: {effective_provider}, model: {preferred_model or 'auto'})..."
        )
        metrics["model_selection_log"]["decision_tree"]["gemini_attempted"] = True
        try:
            # Import google-genai library (official SDK)
            import google.genai as genai

            genai.api_key = ProviderChecker.get_gemini_api_key()

            # Map generic model names to actual Gemini API models
            model_name = (
                preferred_model
                if preferred_model
                and "gemini" in preferred_model.lower()
                and preferred_model.lower() != "gemini"
                else "gemini-2.5-flash"
            )

            # Handle old/generic names → real Gemini models
            # NOTE: For this environment, use 2.5-flash as default
            model_mapping = {
                "gemini": "gemini-2.5-flash",
                "gemini-pro": "gemini-2.5-pro",
                "gemini-flash": "gemini-2.5-flash",
                "gemini-1.5-pro": "gemini-2.5-pro",
                "gemini-1.5-flash": "gemini-2.5-flash",
                "gemini-2.0-flash": "gemini-2.5-flash",
            }

            # Apply mapping if model is in the mapping dict
            if model_name.lower() in model_mapping:
                mapped_model = model_mapping[model_name.lower()]
                logger.info(
                    f"   Model name mapped: {preferred_model} → {mapped_model} (reason: availability)"
                )
                model_name = mapped_model
            else:
                logger.info(f"   Model name no mapping needed: {model_name}")

            logger.info(f"   Using Gemini model: {model_name}")

            metrics["generation_attempts"] += 1
            metrics["model_selection_log"]["attempted_providers"].append("gemini")

            # Run blocking Gemini call in a thread to avoid blocking the event loop
            def _gemini_generate():
                # Calculate max tokens: For Gemini, use MUCH higher multiplier for large outputs
                # Gemini sometimes throttles long outputs, so we need to give it extra room
                # Using 6x multiplier to ensure Gemini has enough token budget to complete full response
                max_tokens = int(target_length * 6.0)  # Using 6x for large outputs (3000+ words)
                # Cap at Gemini's reasonable maximum to avoid API issues
                max_tokens = min(max_tokens, 32000)  # Gemini-pro-15 supports up to 32k output
                logger.debug(
                    f"   Gemini max_output_tokens: {max_tokens} (target_length: {target_length}, multiplier: 6.0x, capped at 32k)"
                )

                # google.genai SDK: Use client.models.generate_content()
                client = genai.Client(api_key=ProviderChecker.get_gemini_api_key())
                response = client.models.generate_content(
                    model=f"models/{model_name}",
                    contents=f"{system_prompt}\n\n{generation_prompt}",
                    config={
                        "max_output_tokens": max_tokens,
                        "temperature": 0.7,
                    },
                )

                return response

            response = await asyncio.to_thread(_gemini_generate)

            # Extract text from response
            generated_content = ""
            try:
                if hasattr(response, "text"):
                    generated_content = response.text or ""
                elif hasattr(response, "content"):
                    generated_content = response.content or ""
                else:
                    logger.error(
                        f"Gemini response missing text/content attribute. Keys: {dir(response)}"
                    )
                    generated_content = ""
            except AttributeError as e:
                logger.error(f"Failed to extract text from Gemini response: {e}", exc_info=True)
                generated_content = ""

            # Track cost from Gemini usage metadata
            usage_meta = getattr(response, "usage_metadata", None)
            if usage_meta:
                input_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
                cost_usd = input_tokens / 1000 * 0.0001 + output_tokens / 1000 * 0.0004
                metrics["cost_log"] = {
                    "provider": "google", "model": model_name,
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "cost_usd": round(cost_usd, 6), "phase": "content_generation",
                }
                logger.info("💰 Gemini cost: $%.4f (%d in + %d out tokens)", cost_usd, input_tokens, output_tokens)

            if generated_content and len(generated_content) > 100:
                validation = self._validate_content(generated_content, topic, target_length)

                # Check if content is significantly under target (less than 60% of target)
                word_count = len(generated_content.split())
                min_acceptable = int(target_length * 0.6)

                if word_count < min_acceptable:
                    logger.warning(
                        f"⚠️ Gemini returned short content: {word_count} words (target: {target_length}, minimum acceptable: {min_acceptable})"
                    )
                    attempts.append(
                        (
                            "Gemini (undershoot)",
                            f"Content too short: {word_count} words vs {target_length} target",
                        )
                    )
                    # Continue to next provider instead of accepting short content
                    pass  # Fall through to try next provider
                else:
                    # Content is acceptable length
                    metrics["validation_results"].append(
                        {
                            "attempt": metrics["generation_attempts"],
                            "score": validation.quality_score,
                            "issues": validation.issues,
                            "passed": validation.is_valid,
                        }
                    )

                    metrics["model_used"] = f"Google Gemini ({model_name})"
                    metrics["models_used_by_phase"]["draft"] = metrics[
                        "model_used"
                    ]  # NEW: Track phase
                    metrics["final_quality_score"] = validation.quality_score
                    metrics["generation_time_seconds"] = time.time() - start_time
                    metrics["model_selection_log"]["decision_tree"]["gemini_succeeded"] = True
                    logger.info(
                        f"✓ Content generated with Gemini: {validation.feedback} ({word_count} words)"
                    )
                    return generated_content, metrics["model_used"], metrics
            else:
                logger.warning(
                    f"Gemini returned empty or very short content: {len(generated_content) if generated_content else 0} chars"
                )
                attempts.append(("Gemini", "Empty or very short response"))

        except Exception as e:
            import traceback

            logger.warning(
                f"User-selected Gemini failed: {type(e).__name__}: {str(e)}", exc_info=True
            )
            logger.debug(f"Gemini error traceback: {traceback.format_exc()}")
            metrics["model_selection_log"]["decision_tree"]["gemini_error"] = str(e)[
                :200
            ]  # Store error
            attempts.append(("Gemini (user-selected)", str(e)))

        return None

    def _extract_ollama_response(self, response) -> str:
        """Extract text content from an Ollama response (dict or string)."""
        # OllamaClient.generate() returns dict with 'text' key (not 'response')
        logger.info(f"      📦 Raw response type: {type(response)}")
        if isinstance(response, dict):
            logger.info(f"      📦 Response is dict with keys: {list(response.keys())}")

        generated_content = ""
        if isinstance(response, dict):
            # Try multiple possible keys: 'text' (OllamaClient), 'response' (Ollama API), 'content'
            generated_content = (
                response.get("text", "")
                or response.get("response", "")
                or response.get("content", "")
            )
            logger.info(f"      📦 Extracted from dict: {len(generated_content)} chars")
            if generated_content:
                logger.debug(
                    f"      📦 Response type: dict | Extracted text: {len(generated_content)} chars"
                )
            else:
                logger.warning(
                    f"      ⚠️  No text found in response dict keys: {list(response.keys())}"
                )
        elif isinstance(response, str):
            generated_content = response
            logger.info(f"      📦 Got direct string: {len(generated_content)} chars")
            logger.debug(f"      📦 Response type: str | Content: {len(generated_content)} chars")
        else:
            logger.warning(f"      ⚠️  Unexpected response type: {type(response)}")
            generated_content = ""

        return generated_content

    async def _refine_ollama_content(
        self, ollama, model_name: str, generated_content: str, validation, ctx: Dict[str, Any]
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
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
            f"      ⚙️  Content below threshold. Refining ({metrics['refinement_attempts'] + 1}/{self.max_refinement_attempts})..."
        )

        metrics["refinement_attempts"] += 1
        refinement_prompt = get_refinement_prompt(
            feedback=validation.feedback,
            issues=validation.issues,
            content=generated_content,
        )

        # Try to refine with same model
        # Calculate max tokens for refinement pass (4.5x multiplier for comprehensive refinement)
        max_tokens_refinement = int(target_length * 4.5)
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
                f"      📦 Refined response type: dict | Content: {len(refined_content)} chars"
            )
        elif isinstance(response, str):
            refined_content = response
            logger.debug(
                f"      📦 Refined response type: str | Content: {len(refined_content)} chars"
            )

        if refined_content and len(refined_content) > 100:
            logger.info(f"      ✓ Refined content generated: {len(refined_content)} characters")

            # Validate refined content
            logger.info("      🔍 Validating refined content...")
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
                f"      📊 Refined Quality: {refined_validation.quality_score:.1f}/{self.quality_threshold} | Words: {refined_word_count} | Issues: {len(refined_validation.issues)}"
            )

            if refined_validation.is_valid:
                logger.info("      ✅ Refined content APPROVED")
                metrics["model_used"] = f"Ollama - {model_name} (refined)"
                metrics["models_used_by_phase"]["draft"] = metrics["model_used"]  # Track phase
                metrics["final_quality_score"] = refined_validation.quality_score
                metrics["generation_time_seconds"] = time.time() - start_time
                logger.info(f"\n{'='*80}")
                logger.info("✅ GENERATION COMPLETE (with refinement)")
                logger.info(f"   Model: {metrics['model_used']}")
                logger.info(
                    f"   Quality: {refined_validation.quality_score:.1f}/{self.quality_threshold}"
                )
                logger.info(f"   Time: {metrics['generation_time_seconds']:.1f}s")
                logger.info(f"{'='*80}\n")
                return refined_content, metrics["model_used"], metrics

        # Return None but signal the refined content via a special key
        # so the caller can use it for further checks
        if refined_content and len(refined_content) > 100:
            ctx["_refined_content"] = refined_content
        return None

    async def _try_ollama(self, ctx: Dict[str, Any]) -> Optional[Tuple[str, str, Dict[str, Any]]]:
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
                f"⏭️ SKIPPING Ollama (skip_ollama={skip_ollama}, effective_provider={effective_provider})\n"
            )
            return None

        logger.info("🔄 [ATTEMPT 1/3] Trying Ollama (Local, GPU-accelerated)...")
        logger.info("   ├─ Endpoint: http://localhost:11434")
        logger.info("   └─ Status: Connecting...\n")
        try:
            from .ollama_client import OllamaClient

            ollama = OllamaClient()
            logger.info("   ✓ OllamaClient initialized")

            # Model selection priority:
            # 1. UI-selected model (preferred_model from task request)
            # 2. OllamaClient's configured model (from DEFAULT_OLLAMA_MODEL env var)
            # 3. Dynamic discovery from installed models (sorted by size)
            # No hardcoded model names — if nothing works, falls through to cloud providers.
            if preferred_model:
                model_list = [preferred_model]
                logger.info(f"   ├─ Using UI-selected model: {preferred_model}")
            else:
                # Start with the client's configured/resolved model
                resolved = await ollama.resolve_model()
                model_list = [resolved]
                logger.info(f"   ├─ Primary model: {resolved}")

                # Add other installed models as fallbacks (in case primary fails)
                try:
                    available = await ollama.list_models()
                    fallbacks = [
                        m["name"]
                        for m in sorted(
                            available,
                            key=lambda x: x.get("size", 0),
                            reverse=True,
                        )
                        if "embed" not in m.get("name", "").lower() and m["name"] != resolved
                    ]
                    model_list.extend(fallbacks)
                except Exception as e:
                    logger.warning(f"   ⚠️ Could not discover fallback models: {e}")

                logger.info(
                    f"   ├─ Will try {len(model_list)} model(s): {[m.split(':')[0] for m in model_list[:5]]}"
                )
            for model_idx, model_name in enumerate(model_list, 1):
                try:
                    logger.info(f"   └─ Testing model {model_idx}/{len(model_list)}: {model_name}")
                    metrics["generation_attempts"] += 1

                    logger.info("      ⏱️  Generating content (timeout: 120s)...")

                    # Calculate max tokens: markdown content + headers + lists need ~2-2.5 tokens per word
                    # Using 2.5x multiplier to prevent token cutoff during generation
                    max_tokens = int(target_length * 3.0)
                    logger.debug(f"      Max tokens: {max_tokens} (target_length: {target_length})")

                    response = await ollama.generate(
                        prompt=generation_prompt,
                        system=system_prompt,
                        model=model_name,
                        stream=False,
                        max_tokens=max_tokens,  # Set explicit token limit for proper word count control
                    )

                    generated_content = self._extract_ollama_response(response)

                    logger.info(
                        f"      🔍 Final check: bool(content)={bool(generated_content)}, len={len(generated_content)}, threshold=100"
                    )
                    if generated_content and len(generated_content) > 100:
                        logger.info(
                            f"      ✓ Content generated: {len(generated_content)} characters"
                        )

                        # Self-check: Validate content quality
                        logger.info("      🔍 Validating content quality...")
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
                            f"      📊 Quality Score: {validation.quality_score:.1f}/{self.quality_threshold} | Words: {word_count} | Issues: {len(validation.issues)}"
                        )

                        if validation.issues:
                            for issue in validation.issues:
                                logger.debug(f"         ⚠️  {issue}")

                        # If content passes QA, return it
                        if validation.is_valid:
                            logger.info("      ✅ Content APPROVED by QA")
                            metrics["model_used"] = f"Ollama - {model_name}"
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
                            logger.info(f"\n{'='*80}")
                            logger.info("✅ GENERATION COMPLETE")
                            logger.info(f"   Model: {metrics['model_used']}")
                            logger.info(
                                f"   Quality: {validation.quality_score:.1f}/{self.quality_threshold}"
                            )
                            logger.info(f"   Time: {metrics['generation_time_seconds']:.1f}s")
                            logger.info(f"{'='*80}\n")
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
                                "      ⚠️  Content below quality threshold but no more refinements available"
                            )
                            metrics["model_used"] = f"Ollama - {model_name} (below threshold)"
                            metrics["models_used_by_phase"]["draft"] = metrics[
                                "model_used"
                            ]  # Track phase
                            metrics["final_quality_score"] = validation.quality_score
                            metrics["generation_time_seconds"] = time.time() - start_time
                            logger.info(f"\n{'='*80}")
                            logger.warning("⚠️  GENERATION COMPLETE (below quality threshold)")
                            logger.info(f"   Model: {metrics['model_used']}")
                            logger.info(
                                f"   Quality: {validation.quality_score:.1f}/{self.quality_threshold}"
                            )
                            logger.info(f"   Time: {metrics['generation_time_seconds']:.1f}s")
                            logger.info(f"{'='*80}\n")
                            return generated_content, metrics["model_used"], metrics
                    else:
                        logger.warning("      ❌ Generated content too short or empty")

                except asyncio.TimeoutError as e:
                    # Explicitly catch timeout - model too slow or server unresponsive
                    error_msg = f"Timeout (120s exceeded) with {model_name}"
                    logger.warning(
                        f"Ollama model {model_name} timed out: {error_msg}", exc_info=True
                    )
                    attempts.append(("Ollama", error_msg))
                    continue
                except Exception as e:
                    # Catch other errors (500 errors, connection issues, etc.)
                    error_msg = str(e)[:150]  # Truncate long error messages
                    logger.warning(f"Ollama model {model_name} failed: {error_msg}", exc_info=True)
                    attempts.append(("Ollama", f"{model_name}: {error_msg}"))
                    continue

        except Exception as e:
            logger.warning(f"Ollama generation failed: {e}", exc_info=True)
            if not attempts:  # Only append if attempts list is still empty
                attempts.append(("Ollama", str(e)[:150]))

        return None

    async def _try_huggingface(
        self, ctx: Dict[str, Any]
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
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
                    logger.debug(f"Trying HuggingFace model: {model_id}")
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
                            metrics["model_used"] = f"HuggingFace - {model_id.split('/')[-1]}"
                            metrics["models_used_by_phase"]["draft"] = metrics[
                                "model_used"
                            ]  # Track phase
                            metrics["final_quality_score"] = validation.quality_score
                            metrics["generation_time_seconds"] = time.time() - start_time
                            logger.info("✓ Content generated and approved with HuggingFace")
                            return generated_content, metrics["model_used"], metrics

                except asyncio.TimeoutError:
                    logger.debug(f"HuggingFace model {model_id} timed out")
                    continue
                except Exception as e:
                    logger.debug(f"HuggingFace model {model_id} failed: {e}")
                    continue

        except Exception as e:
            logger.warning(f"HuggingFace generation failed: {e}", exc_info=True)
            attempts.append(("HuggingFace", str(e)))

        return None

    async def _try_anthropic(
        self, ctx: Dict[str, Any]
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """Try Anthropic Claude provider. Returns result tuple or None."""
        effective_provider = ctx["effective_provider"]
        preferred_model = ctx["preferred_model"]
        metrics = ctx["metrics"]
        system_prompt = ctx["system_prompt"]
        generation_prompt = ctx["generation_prompt"]
        target_length = ctx["target_length"]
        topic = ctx["topic"]
        attempts = ctx["attempts"]
        start_time = ctx["start_time"]

        # ========================================================================
        # 3. ANTHROPIC CLAUDE: Try if key available (fallback after local providers)
        # ========================================================================
        if not (
            (
                effective_provider
                and effective_provider.lower() in ("anthropic", "claude")
                and ProviderChecker.is_anthropic_available()
            )
            or (
                ProviderChecker.is_anthropic_available()
                and not any(p.startswith("Anthropic") for p, _ in attempts)
            )
        ):
            return None

        logger.info("🔄 Trying Anthropic Claude...")
        metrics["model_selection_log"]["attempted_providers"].append("anthropic")
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=ProviderChecker.get_anthropic_api_key())

            # Map model names
            model_name = preferred_model or "claude-sonnet-4-20250514"
            model_mapping = {
                "claude": "claude-sonnet-4-20250514",
                "anthropic": "claude-sonnet-4-20250514",
                "claude-sonnet": "claude-sonnet-4-20250514",
                "claude-haiku": "claude-haiku-4-5-20251001",
                "claude-opus": "claude-opus-4-20250514",
                "claude-3-sonnet": "claude-sonnet-4-20250514",
                "claude-3-haiku": "claude-haiku-4-5-20251001",
                "claude-3-opus": "claude-opus-4-20250514",
            }
            if model_name.lower() in model_mapping:
                model_name = model_mapping[model_name.lower()]
            logger.info(f"   Using Anthropic model: {model_name}")

            metrics["generation_attempts"] += 1
            max_tokens = min(int(target_length * 5.0), 8192)

            response = await client.messages.create(
                model=model_name,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": generation_prompt}],
                temperature=0.7,
            )

            generated_content = response.content[0].text if response.content else ""

            # Track cost from usage data
            usage = getattr(response, "usage", None)
            if usage:
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                # Anthropic pricing: Haiku ~$0.001/1K, Sonnet ~$0.015/1K
                rate = 0.015 if "sonnet" in model_name.lower() else 0.001
                cost_usd = (input_tokens + output_tokens) / 1000 * rate
                metrics["cost_log"] = {
                    "provider": "anthropic", "model": model_name,
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "cost_usd": round(cost_usd, 6), "phase": "content_generation",
                }
                logger.info("💰 Anthropic cost: $%.4f (%d in + %d out tokens)", cost_usd, input_tokens, output_tokens)

            if generated_content and len(generated_content) > 100:
                validation = self._validate_content(generated_content, topic, target_length)
                word_count = len(generated_content.split())
                min_acceptable = int(target_length * 0.6)

                if word_count < min_acceptable:
                    logger.warning(
                        f"⚠️ Anthropic returned short content: {word_count} words (target: {target_length})"
                    )
                    attempts.append(
                        ("Anthropic (undershoot)", f"{word_count} words vs {target_length} target")
                    )
                else:
                    metrics["validation_results"].append(
                        {
                            "attempt": metrics["generation_attempts"],
                            "score": validation.quality_score,
                            "issues": validation.issues,
                            "passed": validation.is_valid,
                        }
                    )
                    metrics["model_used"] = f"Anthropic Claude ({model_name})"
                    metrics["models_used_by_phase"]["draft"] = metrics["model_used"]
                    metrics["final_quality_score"] = validation.quality_score
                    metrics["generation_time_seconds"] = time.time() - start_time
                    logger.info(
                        f"✓ Content generated with Anthropic: {validation.feedback} ({word_count} words)"
                    )
                    return generated_content, metrics["model_used"], metrics
            else:
                logger.warning("Anthropic returned empty/short content")
                attempts.append(("Anthropic", "Empty or very short response"))

        except ImportError:
            logger.warning("anthropic SDK not installed")
            attempts.append(("Anthropic", "SDK not installed"))
        except Exception as e:
            logger.warning(f"Anthropic generation failed: {type(e).__name__}: {e}", exc_info=True)
            attempts.append(("Anthropic", str(e)[:150]))

        return None

    async def _try_gemini_fallback(
        self, ctx: Dict[str, Any]
    ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """Try Gemini as fallback provider. Returns result tuple or None."""
        metrics = ctx["metrics"]
        system_prompt = ctx["system_prompt"]
        generation_prompt = ctx["generation_prompt"]
        target_length = ctx["target_length"]
        topic = ctx["topic"]
        attempts = ctx["attempts"]
        start_time = ctx["start_time"]

        # 4. Fall back to Google Gemini (paid, but reliable)
        if not ProviderChecker.is_gemini_available():
            return None

        logger.info("🔄 [ATTEMPT 3/3] Trying Google Gemini (Fallback)...")
        logger.info(
            f"   ├─ API Key: {'✓ set' if ProviderChecker.is_gemini_available() else '✗ not set'}"
        )
        logger.info("   ├─ Model: gemini-2.5-flash")
        logger.info("   └─ Status: Initializing...\n")
        try:
            # Import google-genai SDK
            import google.genai as genai

            logger.debug("Configuring Gemini with API key...")
            client = genai.Client(api_key=ProviderChecker.get_gemini_api_key())
            logger.debug("✓ Gemini client initialized")

            metrics["generation_attempts"] += 1
            logger.info("   Generating content...")
            # Calculate max tokens for fallback generation - 3x multiplier for full content
            max_tokens_fallback = int(target_length * 3.0)

            # Use model from environment or default to latest flash
            gemini_model_name = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
            response = client.models.generate_content(
                model=f"models/{gemini_model_name}",
                contents=f"{system_prompt}\n\n{generation_prompt}",
                config={
                    "max_output_tokens": max_tokens_fallback,
                    "temperature": 0.7,
                },
            )

            generated_content = response.text
            # Track cost from Gemini usage metadata
            usage_meta = getattr(response, "usage_metadata", None)
            if usage_meta:
                input_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
                # Gemini Flash: ~$0.0001/1K input, ~$0.0004/1K output
                cost_usd = input_tokens / 1000 * 0.0001 + output_tokens / 1000 * 0.0004
                metrics["cost_log"] = {
                    "provider": "google", "model": gemini_model_name,
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "cost_usd": round(cost_usd, 6), "phase": "content_generation",
                }
                logger.info("💰 Gemini cost: $%.4f (%d in + %d out tokens)", cost_usd, input_tokens, output_tokens)

            logger.info(f"   Generated {len(generated_content)} characters")
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

                metrics["model_used"] = "Google Gemini 2.5 Flash"
                metrics["models_used_by_phase"]["draft"] = metrics["model_used"]  # Track phase
                metrics["final_quality_score"] = validation.quality_score
                metrics["generation_time_seconds"] = time.time() - start_time
                logger.info(f"✓ Content generated with Gemini: {validation.feedback}")
                return generated_content, metrics["model_used"], metrics
            else:
                logger.warning(f"Gemini content too short or empty: {len(generated_content)} chars")
                attempts.append(("Gemini", f"Content too short: {len(generated_content)} chars"))

        except ImportError as e:
            logger.warning(f"google-genai not installed: {e}", exc_info=True)
            attempts.append(("Gemini", "SDK not installed"))
        except Exception as e:
            logger.warning(f"Gemini generation failed: {e}", exc_info=True)
            attempts.append(("Gemini", str(e)[:150]))

        return None

    async def _try_openai(self, ctx: Dict[str, Any]) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        """Try OpenAI provider. Returns result tuple or None."""
        effective_provider = ctx["effective_provider"]
        preferred_model = ctx["preferred_model"]
        metrics = ctx["metrics"]
        system_prompt = ctx["system_prompt"]
        generation_prompt = ctx["generation_prompt"]
        target_length = ctx["target_length"]
        topic = ctx["topic"]
        attempts = ctx["attempts"]
        start_time = ctx["start_time"]

        # ========================================================================
        # 5. OPENAI: Last paid fallback before template
        # ========================================================================
        if not (
            (
                effective_provider
                and effective_provider.lower() == "openai"
                and ProviderChecker.is_openai_available()
            )
            or (
                ProviderChecker.is_openai_available()
                and not any(p.startswith("OpenAI") for p, _ in attempts)
            )
        ):
            return None

        logger.info("🔄 Trying OpenAI...")
        metrics["model_selection_log"]["attempted_providers"].append("openai")
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=ProviderChecker.get_openai_api_key())

            model_name = preferred_model or "gpt-4o-mini"
            model_mapping = {
                "openai": "gpt-4o-mini",
                "gpt-4": "gpt-4o",
                "gpt-4-turbo": "gpt-4o",
                "gpt-3.5-turbo": "gpt-4o-mini",
                "gpt-4o": "gpt-4o",
                "gpt-4o-mini": "gpt-4o-mini",
            }
            if model_name.lower() in model_mapping:
                model_name = model_mapping[model_name.lower()]
            logger.info(f"   Using OpenAI model: {model_name}")

            metrics["generation_attempts"] += 1
            max_tokens = min(int(target_length * 5.0), 16384)

            response = await client.chat.completions.create(
                model=model_name,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": generation_prompt},
                ],
                temperature=0.7,
            )

            generated_content = response.choices[0].message.content or ""

            # Track cost from OpenAI usage
            usage = getattr(response, "usage", None)
            if usage:
                input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", 0) or 0
                # GPT-4o-mini: ~$0.00015/1K input, ~$0.0006/1K output
                rate_in = 0.00015 if "mini" in model_name else 0.005
                rate_out = 0.0006 if "mini" in model_name else 0.015
                cost_usd = input_tokens / 1000 * rate_in + output_tokens / 1000 * rate_out
                metrics["cost_log"] = {
                    "provider": "openai", "model": model_name,
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "cost_usd": round(cost_usd, 6), "phase": "content_generation",
                }
                logger.info("💰 OpenAI cost: $%.4f (%d in + %d out tokens)", cost_usd, input_tokens, output_tokens)

            if generated_content and len(generated_content) > 100:
                validation = self._validate_content(generated_content, topic, target_length)
                word_count = len(generated_content.split())
                min_acceptable = int(target_length * 0.6)

                if word_count < min_acceptable:
                    logger.warning(
                        f"⚠️ OpenAI returned short content: {word_count} words (target: {target_length})"
                    )
                    attempts.append(
                        ("OpenAI (undershoot)", f"{word_count} words vs {target_length} target")
                    )
                else:
                    metrics["validation_results"].append(
                        {
                            "attempt": metrics["generation_attempts"],
                            "score": validation.quality_score,
                            "issues": validation.issues,
                            "passed": validation.is_valid,
                        }
                    )
                    metrics["model_used"] = f"OpenAI ({model_name})"
                    metrics["models_used_by_phase"]["draft"] = metrics["model_used"]
                    metrics["final_quality_score"] = validation.quality_score
                    metrics["generation_time_seconds"] = time.time() - start_time
                    logger.info(
                        f"✓ Content generated with OpenAI: {validation.feedback} ({word_count} words)"
                    )
                    return generated_content, metrics["model_used"], metrics
            else:
                logger.warning("OpenAI returned empty/short content")
                attempts.append(("OpenAI", "Empty or very short response"))

        except ImportError:
            logger.warning("openai SDK not installed")
            attempts.append(("OpenAI", "SDK not installed"))
        except Exception as e:
            logger.warning(f"OpenAI generation failed: {type(e).__name__}: {e}", exc_info=True)
            attempts.append(("OpenAI", str(e)[:150]))

        return None

    def _handle_all_providers_failed(self, ctx: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
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
        logger.error(f"\n{'='*80}")
        logger.error("❌ ALL AI MODELS FAILED - Using fallback template")
        logger.error(f"{'='*80}")
        logger.error(f"Attempts made: {len(attempts)}")
        for provider, error in attempts:
            logger.error(f"   ✗ {provider}: {error}")
        logger.error("Provider summary:")
        logger.error(
            f"   - Anthropic:   {ProviderChecker.is_anthropic_available()} (key available)"
        )
        logger.error(f"   - Gemini:      {ProviderChecker.is_gemini_available()} (key available)")
        logger.error(f"   - OpenAI:      {ProviderChecker.is_openai_available()} (key available)")
        logger.error(f"   - Ollama:      {use_ollama} (tried/available)")
        logger.error(
            f"   - HuggingFace: {ProviderChecker.is_huggingface_available()} (token available)"
        )
        logger.error(f"{'='*80}\n")

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
        preferred_model: Optional[str] = None,
        preferred_provider: Optional[str] = None,
        writing_style_context: Optional[str] = None,
        research_context: Optional[str] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Generate a blog post using best available model with self-checking.

        Features:
        - User-selected model support (respects frontend UI choices)
        - Intelligent provider fallback (Ollama → HuggingFace → Gemini)
        - Self-validation and quality checking
        - Refinement loop for rejected content
        - Full metrics tracking

        Args:
            topic: Blog post topic
            style: Writing style (technical, narrative, etc.)
            tone: Content tone (professional, casual, etc.)
            target_length: Target word count
            tags: Content tags
            preferred_model: User-selected model (e.g., 'gpt-4', 'claude-3-opus', 'gemini-pro')
            preferred_provider: User-selected provider ('openai', 'anthropic', 'gemini', 'ollama', 'huggingface')
            writing_style_context: Optional writing style excerpts to include in the prompt
                for voice/tone matching

        Returns:
            Tuple of (content, model_used, metrics_dict)
        """
        ctx = await self._prepare_generation_context(
            topic, style, tone, target_length, tags, preferred_model, preferred_provider,
            writing_style_context=writing_style_context,
            research_context=research_context,
        )

        # ========================================================================
        # USER SELECTION: Try user-selected provider/model first if specified
        # ========================================================================
        result = await self._try_gemini_user_selected(ctx)
        if result:
            return result

        # 1. Try Ollama (local, free, no internet, RTX 5070 optimized)
        result = await self._try_ollama(ctx)
        if result:
            return result

        # 2. Try HuggingFace (free tier, online)
        result = await self._try_huggingface(ctx)
        if result:
            return result

        # 3. Try Anthropic Claude (paid cloud)
        result = await self._try_anthropic(ctx)
        if result:
            return result

        # 4. Try Google Gemini fallback (paid cloud)
        result = await self._try_gemini_fallback(ctx)
        if result:
            return result

        # 5. Try OpenAI (paid cloud)
        result = await self._try_openai(ctx)
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
_generator: Optional[AIContentGenerator] = None


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
    logger.debug(f"Ollama available: {generator.ollama_available}")
    logger.debug(f"HuggingFace token: {'✓' if generator.hf_token else '✗'}")
    logger.debug(f"Gemini key: {'✓' if generator.gemini_key else '✗'}")

    logger.info("Generating blog post...")
    content, model, metrics = await generator.generate_blog_post(
        topic="AI-Powered Content Creation for Modern Marketing",
        style="technical",
        tone="professional",
        target_length=1500,
        tags=["AI", "Marketing", "Technology"],
    )

    logger.info(f"Model used: {model}")
    logger.info(f"Content length: {len(content)} characters")
    logger.info(f"Quality score: {metrics['final_quality_score']}/10")
    logger.info(f"Generation attempts: {metrics['generation_attempts']}")
    logger.info(f"Time taken: {metrics['generation_time_seconds']:.2f} seconds")
    logger.info(f"First 500 characters:\n{content[:500]}...")


if __name__ == "__main__":
    asyncio.run(test_generation())
