"""
Multi-Model QA — adversarial review using different LLM providers.

Different models have different training data and blind spots.
By using multiple models to review content, we catch issues that
any single model would miss.

Architecture:
  Writer:       Ollama (local, free) — generates the draft
  Critic:       Anthropic Claude — reviews style, logic, coherence
  Fact-checker: Programmatic validator — catches fabricated claims
  Arbiter:      Score aggregation — weighted average decides publish/reject

Usage:
    from services.multi_model_qa import MultiModelQA
    qa = MultiModelQA(pool)
    result = await qa.review(title, content, topic)
    if result.approved:
        # Safe to publish
"""

from services.logger_config import get_logger
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from services.content_validator import validate_content, ValidationResult
from services.model_router import get_model_router

logger = get_logger(__name__)


@dataclass
class ReviewerResult:
    """Result from a single reviewer."""
    reviewer: str  # "ollama_qa", "anthropic_critic", "validator"
    approved: bool
    score: float
    feedback: str
    provider: str  # "ollama", "anthropic", "programmatic"


@dataclass
class MultiModelResult:
    """Aggregated result from all reviewers."""
    approved: bool
    final_score: float
    reviews: List[ReviewerResult] = field(default_factory=list)
    validation: Optional[ValidationResult] = None
    cost_log: Optional[dict] = None

    @property
    def summary(self) -> str:
        lines = [f"Score: {self.final_score:.0f}/100 ({'APPROVED' if self.approved else 'REJECTED'})"]
        for r in self.reviews:
            status = "pass" if r.approved else "FAIL"
            lines.append(f"  {r.reviewer} ({r.provider}): {r.score:.0f} [{status}] — {r.feedback[:80]}")
        if self.validation and self.validation.issues:
            lines.append(f"  Validator: {self.validation.critical_count} critical, {self.validation.warning_count} warnings")
        return "\n".join(lines)


QA_PROMPT = """Review this blog post for publication readiness. Be critical but fair.

TODAY'S DATE: {current_date}

TITLE: {title}
TOPIC: {topic}

{sources_block}---CONTENT---
{content}
---END---

Evaluate:
1. Is the content factually accurate? Flag any claims that seem fabricated.
2. Is the writing clear, engaging, and well-structured?
3. Are there any hallucinated people, statistics, or quotes?
4. Would this be valuable to the target audience (developers and founders)?

IMPORTANT — handling claims about products or events you do not recognize:
Your training data has a cutoff. The article may discuss hardware, software,
or events that were released or happened after your cutoff but before today's
date above. If you encounter a specific product, version, framework, or event
you do not recognize:

  - Do NOT automatically reject it as fabricated just because you lack knowledge.
    "I have not heard of this" is not the same as "this does not exist."
  - Do NOT automatically accept it either. Unknown-to-you is not a free pass.
  - Distinguish suspicious from merely unknown. Reject when claims are internally
    contradictory, suspiciously specific (fake-looking statistics, invented quotes
    attributed to real people, made-up studies with impossible citations),
    or physically/logically impossible. Flag as "uncertain — cannot verify"
    when the claim is plausible given the date but outside your knowledge,
    and lower the score modestly for unverifiable specifics rather than rejecting.
  - Always reject fabricated people, fake statistics, and invented quotes,
    regardless of date.

HOW TO USE THE SOURCES SECTION (if present above):
The SOURCES block contains the research corpus the writer consulted while
drafting this post — real links, pulled excerpts, and internal reference
material. Treat it as the authoritative ground truth for this specific
article. When you encounter a factual claim in the content:

  - If the claim appears in or is supported by the SOURCES, it is grounded.
    Accept it even if it falls outside your training knowledge.
  - If the claim does NOT appear in the SOURCES and is also outside your
    knowledge, flag it as "unverified — not backed by provided research"
    and lower the score modestly. Do not reject outright unless the claim
    is implausible.
  - If the claim contradicts the SOURCES, that is a hard rejection.
  - A claim that is well-established common knowledge (e.g., "HTTP uses
    status codes", "Postgres supports JSONB") does not need to appear in
    the SOURCES to be accepted.

If the SOURCES block is absent, fall back to your training knowledge with
the cutoff caveats above.

Respond with ONLY valid JSON:
{{"approved": true/false, "quality_score": NUMBER 0-100, "feedback": "2-3 sentences"}}
"""


class MultiModelQA:
    """Multi-model quality assurance for content pipeline.

    Model assignments are configurable via app_settings:
      pipeline_critic_model = "anthropic/claude-haiku-4-5"
      pipeline_factcheck_model = "programmatic"
    Change at runtime via OpenClaw or the settings API.
    """

    def __init__(self, pool=None, settings_service=None):
        self.pool = pool
        self.settings = settings_service
        self.router = get_model_router()

    async def review(
        self,
        title: str,
        content: str,
        topic: str = "",
        research_sources: Optional[str] = None,
    ) -> MultiModelResult:
        """
        Run content through multiple reviewers and aggregate results.

        Args:
            title: Post title
            content: Post body
            topic: Original topic (used for context)
            research_sources: Optional research corpus the writer consulted.
                When provided, the critic grounds factual claims against this
                corpus instead of relying solely on its own (possibly stale)
                training data. Pass the same research_context string produced
                by ResearchService.build_context() during generation.

        Returns MultiModelResult with approval decision and individual reviews.
        """
        reviews: List[ReviewerResult] = []

        # 1. Programmatic validation (always runs, fast, deterministic)
        validation = validate_content(title, content, topic)
        validator_review = ReviewerResult(
            reviewer="programmatic_validator",
            approved=validation.passed,
            score=100.0 - validation.score_penalty,
            feedback="; ".join(i.description[:60] for i in validation.issues[:3]) or "No issues found",
            provider="programmatic",
        )
        reviews.append(validator_review)

        # If programmatic validator finds critical issues, reject immediately
        if not validation.passed:
            logger.warning("[MULTI_QA] Programmatic validator rejected: %d critical issues", validation.critical_count)
            return MultiModelResult(
                approved=False,
                final_score=max(0, 100 - validation.score_penalty),
                reviews=reviews,
                validation=validation,
            )

        # 2. Cross-model review using a DIFFERENT provider than the writer
        # Model is configurable via app_settings (pipeline_critic_model)
        critic_model = None
        if self.settings:
            critic_model = await self.settings.get("pipeline_critic_model")
        qa_cost_log = None
        cross_result = await self._review_with_cloud_model(
            title, content, topic,
            model_override=critic_model,
            research_sources=research_sources,
        )
        critic_skipped = False
        if cross_result:
            cross_review, qa_cost_log = cross_result
            reviews.append(cross_review)
        else:
            critic_skipped = True
            logger.warning("[MULTI_QA] Cross-model review skipped — score will reflect validator only")

        # 3. Aggregate scores — weights configurable via app_settings
        validator_weight = 0.4
        critic_weight = 0.6
        approval_threshold = 70
        if self.settings:
            validator_weight = float(await self.settings.get("qa_validator_weight") or 0.4)
            critic_weight = float(await self.settings.get("qa_critic_weight") or 0.6)
            approval_threshold = float(await self.settings.get("qa_final_score_threshold") or 70)

        scored_reviews = [r for r in reviews if r.score > 0]
        if scored_reviews:
            weights = {"programmatic": validator_weight, "anthropic": critic_weight, "google": critic_weight, "ollama": critic_weight}
            total_weight = sum(weights.get(r.provider, 0.5) for r in scored_reviews)
            final_score = sum(
                r.score * weights.get(r.provider, 0.5) for r in scored_reviews
            ) / total_weight if total_weight > 0 else 0
        else:
            final_score = validator_review.score

        # If critic was skipped, don't auto-approve — require the validator score
        # to meet threshold on its own. This prevents silently passing weak content.
        all_passed = all(r.approved for r in reviews)
        if critic_skipped:
            # Validator-only: use its raw score, don't pretend cross-model passed
            final_score = validator_review.score
        approved = all_passed and final_score >= approval_threshold

        result = MultiModelResult(
            approved=approved,
            final_score=final_score,
            reviews=reviews,
            validation=validation,
            cost_log=qa_cost_log,
        )

        logger.info("[MULTI_QA] %s — %s", title[:50], result.summary.split("\n")[0])
        return result

    async def _review_with_cloud_model(
        self,
        title: str,
        content: str,
        topic: str,
        model_override: Optional[str] = None,
        research_sources: Optional[str] = None,
    ):
        """Review content using local Ollama. Paid cloud APIs removed (Ollama-only policy)."""
        # Try Ollama (free, local)
        ollama_result = await self._review_with_ollama(
            title, content, topic, model_override, research_sources=research_sources,
        )
        if ollama_result is not None:
            return ollama_result

        # Try a fallback model if the primary returned empty/failed
        fallback_model = "gemma3:27b"
        if model_override != fallback_model:
            logger.info("[MULTI_QA] Primary critic failed, trying fallback model: %s", fallback_model)
            fallback_result = await self._review_with_ollama(
                title, content, topic,
                model_override=fallback_model,
                research_sources=research_sources,
            )
            if fallback_result is not None:
                return fallback_result

        logger.info("[MULTI_QA] All QA models unavailable — skipping review")
        return None

    async def _review_with_ollama(
        self,
        title: str,
        content: str,
        topic: str,
        model_override: Optional[str] = None,
        research_sources: Optional[str] = None,
    ) -> Optional[ReviewerResult]:
        """Review content using local Ollama (zero cost).

        Uses gemma3:27b by default — strong at structured JSON output.
        """
        import json
        import re

        try:
            from services.ollama_client import OllamaClient

            client = OllamaClient()
            # Configure electricity rate from app_settings if available
            if self.settings:
                rate = await self.settings.get("electricity_rate_kwh")
                if rate:
                    client.configure_electricity(electricity_rate_kwh=float(rate))
            if not await client.check_health():
                logger.debug("[MULTI_QA] Ollama not available, skipping local review")
                await client.close()
                return None

            # Build the sources block if the caller passed a research corpus.
            # Cap it at 4000 chars so it doesn't dwarf the content in the prompt.
            sources_block = ""
            if research_sources:
                trimmed = research_sources.strip()
                if len(trimmed) > 4000:
                    trimmed = trimmed[:4000] + "\n\n[...truncated for prompt length...]"
                sources_block = (
                    f"---SOURCES (research corpus the writer consulted)---\n"
                    f"{trimmed}\n"
                    f"---END SOURCES---\n\n"
                )
            prompt = QA_PROMPT.format(
                title=title,
                topic=topic or title,
                content=content[:8000],
                current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                sources_block=sources_block,
            )

            # Model and token limits configurable via app_settings
            default_model = "glm-4.7-5090:latest"
            thinking_max = 1500
            standard_max = 300
            temperature = 0.3
            if self.settings:
                default_model = await self.settings.get("pipeline_critic_model") or default_model
                thinking_max = int(await self.settings.get("qa_thinking_model_max_tokens") or thinking_max)
                standard_max = int(await self.settings.get("qa_standard_max_tokens") or standard_max)
                temperature = float(await self.settings.get("qa_temperature") or temperature)
            ollama_model = (model_override or default_model).removeprefix("ollama/")
            is_thinking_model = any(t in ollama_model.lower() for t in ("qwen3.5", "glm-4.7", "qwen3:30b"))
            max_tok = thinking_max if is_thinking_model else standard_max
            result = await client.generate(
                prompt=prompt,
                model=ollama_model,
                temperature=temperature,
                max_tokens=max_tok,
            )
            await client.close()

            text = result.get("text", "")
            if not text:
                logger.warning("[MULTI_QA] Ollama returned empty response")
                return None

            # Parse JSON response
            json_match = text
            if "```" in text:
                match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
                if match:
                    json_match = match.group(1)

            try:
                data = json.loads(json_match)
            except json.JSONDecodeError:
                match = re.search(r"\{[^{}]*\"approved\"[^{}]*\}", text)
                if match:
                    data = json.loads(match.group(0))
                else:
                    logger.warning("[MULTI_QA] Ollama response was not valid JSON: %s", text[:200])
                    return None

            # Cost is calculated from GPU power draw * duration by the Ollama client
            electricity_cost = result.get("cost", 0.0)
            duration_s = result.get("duration_seconds", 0.0)
            cost_log = {
                "provider": "ollama", "model": ollama_model,
                "input_tokens": result.get("prompt_tokens", 0),
                "output_tokens": result.get("tokens", 0),
                "cost_usd": round(electricity_cost, 6), "phase": "qa_review",
                "duration_seconds": round(duration_s, 2),
            }
            logger.info(
                "[MULTI_QA] Ollama QA: model=%s, tokens=%d, electricity=$%.6f",
                ollama_model, result.get("tokens", 0), electricity_cost,
            )

            score = float(data.get("quality_score", 0))
            # Trust the score over the LLM's boolean — models often say
            # approved=false even with high scores due to minor nitpicks
            approved = score >= 70 or data.get("approved", False)
            review = ReviewerResult(
                reviewer="ollama_critic",
                approved=approved,
                score=score,
                feedback=data.get("feedback", "No feedback"),
                provider="ollama",
            )
            return review, cost_log

        except Exception as e:
            logger.warning("[MULTI_QA] Ollama review failed (non-critical): %s", e)
            return None

    # _review_with_gemini removed — Ollama-only policy
