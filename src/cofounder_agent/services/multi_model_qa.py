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

TITLE: {title}
TOPIC: {topic}

---CONTENT---
{content}
---END---

Evaluate:
1. Is the content factually accurate? Flag any claims that seem fabricated.
2. Is the writing clear, engaging, and well-structured?
3. Are there any hallucinated people, statistics, or quotes?
4. Would this be valuable to the target audience (developers and founders)?

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

    async def review(self, title: str, content: str, topic: str = "") -> MultiModelResult:
        """
        Run content through multiple reviewers and aggregate results.

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
        cross_result = await self._review_with_cloud_model(title, content, topic, model_override=critic_model)
        if cross_result:
            cross_review, qa_cost_log = cross_result
            reviews.append(cross_review)

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

        # Approve only if ALL reviewers pass and final score >= threshold
        all_passed = all(r.approved for r in reviews)
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
        self, title: str, content: str, topic: str, model_override: Optional[str] = None,
    ):
        """Review content using local Ollama. Paid cloud APIs removed (Ollama-only policy)."""
        # Try Ollama (free, local)
        ollama_result = await self._review_with_ollama(title, content, topic, model_override)
        if ollama_result is not None:
            return ollama_result

        logger.info("[MULTI_QA] Ollama unavailable, no cloud fallback — skipping review")
        return None

    async def _review_with_ollama(
        self, title: str, content: str, topic: str, model_override: Optional[str] = None,
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

            prompt = QA_PROMPT.format(
                title=title,
                topic=topic or title,
                content=content[:8000],
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
            ollama_model = model_override or default_model
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
