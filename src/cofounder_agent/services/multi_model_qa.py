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

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from services.content_validator import validate_content, ValidationResult
from services.model_router import get_model_router

logger = logging.getLogger(__name__)


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
        cross_review = await self._review_with_cloud_model(title, content, topic, model_override=critic_model)
        if cross_review:
            reviews.append(cross_review)

        # 3. Aggregate scores
        scored_reviews = [r for r in reviews if r.score > 0]
        if scored_reviews:
            # Weighted: programmatic validator gets 40%, cloud reviewer gets 60%
            weights = {"programmatic": 0.4, "anthropic": 0.6, "google": 0.6, "ollama": 0.3}
            total_weight = sum(weights.get(r.provider, 0.5) for r in scored_reviews)
            final_score = sum(
                r.score * weights.get(r.provider, 0.5) for r in scored_reviews
            ) / total_weight if total_weight > 0 else 0
        else:
            final_score = validator_review.score

        # Approve only if ALL reviewers pass and final score >= 70
        all_passed = all(r.approved for r in reviews)
        approved = all_passed and final_score >= 70

        result = MultiModelResult(
            approved=approved,
            final_score=final_score,
            reviews=reviews,
            validation=validation,
        )

        logger.info("[MULTI_QA] %s — %s", title[:50], result.summary.split("\n")[0])
        return result

    async def _review_with_cloud_model(
        self, title: str, content: str, topic: str, model_override: Optional[str] = None,
    ) -> Optional[ReviewerResult]:
        """Review content using Gemini Flash (cheap, $1K credit, GCP metrics).

        Strategy: Use Google Gemini for API work, keep Anthropic for development.
        Gemini Flash is 10x cheaper than Haiku and gives full GCP billing visibility.
        """
        import json
        import os
        import re

        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.debug("[MULTI_QA] No Google API key, skipping cloud review")
            return None

        try:
            import google.genai as genai

            client = genai.Client(api_key=api_key)
            prompt = QA_PROMPT.format(
                title=title,
                topic=topic or title,
                content=content[:8000],
            )

            model_name = model_override or "gemini-2.5-flash"
            response = client.models.generate_content(
                model=f"models/{model_name}",
                contents=prompt,
                config={"max_output_tokens": 300, "temperature": 0.3},
            )

            text = response.text
            if not text:
                return None

            # Parse JSON response
            json_match = text
            if "```" in text:
                match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
                if match:
                    json_match = match.group(1)

            try:
                data = json.loads(json_match)
            except json.JSONDecodeError:
                match = re.search(r"\{[^{}]*\"approved\"[^{}]*\}", text)
                if match:
                    data = json.loads(match.group(0))
                else:
                    return None

            # Log cost for tracking
            usage = getattr(response, "usage_metadata", None)
            if usage:
                in_tok = getattr(usage, "prompt_token_count", 0) or 0
                out_tok = getattr(usage, "candidates_token_count", 0) or 0
                cost = in_tok / 1000 * 0.0001 + out_tok / 1000 * 0.0004
                logger.info("[MULTI_QA] Gemini cost: $%.4f (%d in + %d out)", cost, in_tok, out_tok)

            return ReviewerResult(
                reviewer="gemini_critic",
                approved=data.get("approved", False),
                score=float(data.get("quality_score", 0)),
                feedback=data.get("feedback", "No feedback"),
                provider="google",
            )

        except ImportError:
            logger.debug("[MULTI_QA] google-genai not installed")
            return None
        except Exception as e:
            logger.warning("[MULTI_QA] Gemini review failed (non-critical): %s", e)
            return None
