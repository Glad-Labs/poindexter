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

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from services.content_validator import ValidationResult, validate_content
from services.logger_config import get_logger
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
    reviews: list[ReviewerResult] = field(default_factory=list)
    validation: ValidationResult | None = None
    cost_log: dict | None = None

    @property
    def summary(self) -> str:
        lines = [f"Score: {self.final_score:.0f}/100 ({'APPROVED' if self.approved else 'REJECTED'})"]
        for r in self.reviews:
            status = "pass" if r.approved else "FAIL"
            lines.append(f"  {r.reviewer} ({r.provider}): {r.score:.0f} [{status}] — {r.feedback[:80]}")
        if self.validation and self.validation.issues:
            lines.append(f"  Validator: {self.validation.critical_count} critical, {self.validation.warning_count} warnings")
        return "\n".join(lines)


TOPIC_DELIVERY_PROMPT = """You are a strict editor checking whether an article
delivers on its topic. A reader clicking this article expects what the topic
promises. Did the writer deliver?

REQUESTED TOPIC: {topic}

ARTICLE OPENING (first ~1000 words):
{opening}

Check these specific failure modes:

  1. Numeric promises. If the topic says "10 X" or "11 Y" or "5 Z", does the
     body actually list that many? Partial lists (two items then a pivot to
     generalities) FAIL.
  2. Named entities. If the topic names a specific product, person, or
     technology ("Llama 4", "Claude", "indie hackers making $1M+"), does the
     body actually discuss that specific thing? An article titled "Llama 4"
     that only discusses Llama 3.1 FAILS.
  3. Format promise. If the topic implies a guide, tutorial, list, or review,
     does the body deliver that format? A "guide" that's actually an opinion
     piece FAILS.
  4. Angle/thesis. Is the article's thesis actually about the topic, or did
     the writer pivot to a tangential point they preferred?

Respond with ONLY valid JSON:
{{"delivers": true/false, "score": NUMBER 0-100, "reason": "1-2 sentences naming the specific gap if any"}}

Scoring guidance: delivers=true and score 85-100 if the body is a faithful
execution of the topic. delivers=false and score 0-40 if the body is a
bait-and-switch or numeric underdelivery or misnamed version. delivers=true
and score 60-80 if the body is mostly on-topic but weaker than the topic
implies.
"""


CONSISTENCY_PROMPT = """You are a strict editor checking an article for
internal contradictions. Readers lose trust when section 1 says X and
section 3 says not-X, even if both are defensible on their own.

ARTICLE (full text):
{content}

Read the entire article and look for:

  1. Recommendation contradictions. Does one section recommend tool/approach
     A and another section recommend incompatible tool/approach B without
     acknowledging the switch? ("Don't use React" followed by "use Next.js"
     is a contradiction; Next.js is React.)
  2. Factual contradictions. Does one section state a number, version, or
     claim that another section directly contradicts?
  3. Principle contradictions. Does the article lay out a principle in one
     section ("never build custom auth") and then show code that violates it
     in another section?
  4. Code vs prose contradictions. Does the prose claim the code does X when
     the code actually does Y? (e.g. "the code validates the referrer" when
     the code just inserts without validating.)

Respond with ONLY valid JSON:
{{"consistent": true/false, "score": NUMBER 0-100, "contradictions": ["list","of","specific","pairs"]}}

Scoring guidance: consistent=true and score 85-100 if no contradictions.
consistent=false and score 0-50 if one or more contradictions found.
Be specific in the contradictions list — name the sections and the conflict.
"""


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
        research_sources: str | None = None,
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
        reviews: list[ReviewerResult] = []

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

        # 2b. Topic-delivery gate — catches bait-and-switch titles where the
        # body doesn't deliver what the topic promised. Binary gate: if the
        # body fails to deliver, approved=False regardless of other scores.
        topic_review = await self._check_topic_delivery(topic, content)
        if topic_review is not None:
            reviews.append(topic_review)

        # 2c. Internal-consistency gate — catches cross-section contradictions
        # where one section recommends something another section forbids.
        # NOT a hard binary gate: Ollama critics hallucinate contradictions
        # (confusing section headers for claims, etc.). We feed the gate's
        # score into the weighted average so real contradictions still
        # tank the final score, but a single flaky report won't veto a
        # post that otherwise scored 85+. A hard veto only fires if the
        # gate's own score is unambiguously low (< 50).
        # When the consistency gate fires, the content_router_service
        # rewrite loop intercepts and retries the draft with targeted
        # fixes before finalizing the reject/approve decision.
        consistency_review = await self._check_internal_consistency(content)
        if consistency_review is not None:
            reviews.append(consistency_review)

        # 2d. Image relevance gate — checks whether inline images
        # actually match the content they're next to. Catches the
        # "a close-up image of a busy server room" stock-photo-for-a-
        # FastAPI-post pattern Matt flagged on 2026-04-11. Behind a
        # flag because it requires a vision-capable Ollama model.
        image_review = await self._check_image_relevance(title, topic, content)
        if image_review is not None:
            reviews.append(image_review)

        # 3. Aggregate scores — weights configurable via app_settings
        validator_weight = 0.4
        critic_weight = 0.6
        gate_weight = 0.3  # topic-delivery + internal-consistency gates
        approval_threshold = 70
        if self.settings:
            validator_weight = float(await self.settings.get("qa_validator_weight") or 0.4)
            critic_weight = float(await self.settings.get("qa_critic_weight") or 0.6)
            gate_weight = float(await self.settings.get("qa_gate_weight") or 0.3)
            approval_threshold = float(await self.settings.get("qa_final_score_threshold") or 70)

        scored_reviews = [r for r in reviews if r.score > 0]
        if scored_reviews:
            weights = {
                "programmatic": validator_weight,
                "anthropic": critic_weight,
                "google": critic_weight,
                "ollama": critic_weight,
                "consistency_gate": gate_weight,
                "vision_gate": gate_weight,
            }
            total_weight = sum(weights.get(r.provider, 0.5) for r in scored_reviews)
            final_score = sum(
                r.score * weights.get(r.provider, 0.5) for r in scored_reviews
            ) / total_weight if total_weight > 0 else 0
        else:
            final_score = validator_review.score

        # Hard-gate pass check — the consistency gate is treated as advisory
        # unless its own score is unambiguously low (< 50). The topic-delivery
        # gate stays binary because title/body mismatch is usually clear-cut.
        consistency_veto_threshold = 50.0
        if self.settings:
            try:
                consistency_veto_threshold = float(
                    await self.settings.get("qa_consistency_veto_threshold") or 50
                )
            except Exception:
                pass

        def _reviewer_vetoes(r: ReviewerResult) -> bool:
            if r.approved:
                return False
            if r.reviewer == "internal_consistency":
                return r.score > 0 and r.score < consistency_veto_threshold
            return True

        all_passed = not any(_reviewer_vetoes(r) for r in reviews)
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
        model_override: str | None = None,
        research_sources: str | None = None,
    ):
        """Review content using local Ollama. Paid cloud APIs removed (Ollama-only policy)."""
        # Try Ollama (free, local)
        ollama_result = await self._review_with_ollama(
            title, content, topic, model_override, research_sources=research_sources,
        )
        if ollama_result is not None:
            return ollama_result

        # Try a fallback model if the primary returned empty/failed.
        # DB-configured via qa_fallback_critic_model.
        fallback_model = "gemma3:27b"
        if self.settings:
            try:
                _fb = await self.settings.get("qa_fallback_critic_model")
                if _fb:
                    fallback_model = _fb.removeprefix("ollama/")
            except Exception:
                pass
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
        model_override: str | None = None,
        research_sources: str | None = None,
    ) -> ReviewerResult | None:
        """Review content using local Ollama (zero cost).

        Uses gemma3:27b by default — strong at structured JSON output.
        """
        import asyncio
        import json
        import re

        try:
            from services.ollama_client import OllamaClient

            # Explicit 90s timeout on the main critic — thinking models (glm-4.7,
            # qwen3:30b) can legitimately take ~60s for a 1500-token review, so
            # 90s gives headroom without risking a multi-minute hang.
            client = OllamaClient(timeout=90)
            # Configure electricity rate from app_settings if available
            if self.settings:
                rate = await self.settings.get("electricity_rate_kwh")
                if rate:
                    client.configure_electricity(electricity_rate_kwh=float(rate))
            try:
                healthy = await asyncio.wait_for(client.check_health(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("[MULTI_QA] Ollama check_health timed out after 5s")
                await client.close()
                return None
            if not healthy:
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
            try:
                result = await asyncio.wait_for(
                    client.generate(
                        prompt=prompt,
                        model=ollama_model,
                        temperature=temperature,
                        max_tokens=max_tok,
                    ),
                    timeout=90,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[MULTI_QA] Main critic %s timed out after 90s — skipping",
                    ollama_model,
                )
                await client.close()
                return None
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

    async def _run_gate_prompt(
        self,
        prompt: str,
        reviewer_name: str,
        pass_key: str,
    ) -> ReviewerResult | None:
        """Shared plumbing for the topic-delivery and consistency gates.

        Runs a JSON-returning Ollama prompt, parses the result, and turns it
        into a ReviewerResult with provider='consistency_gate'. Returns None
        if Ollama is unreachable or returns unparseable output — in that
        case the gate is silently skipped and does not block approval.
        """
        import asyncio
        import json
        import re

        try:
            from services.ollama_client import OllamaClient

            # 60s per gate — gates use shorter prompts (max_tokens=600) and
            # should respond well under that even on the thinking critic.
            client = OllamaClient(timeout=60)
            if self.settings:
                rate = await self.settings.get("electricity_rate_kwh")
                if rate:
                    client.configure_electricity(electricity_rate_kwh=float(rate))
            try:
                healthy = await asyncio.wait_for(client.check_health(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning(
                    "[MULTI_QA] %s: Ollama health check timed out after 5s", reviewer_name
                )
                await client.close()
                return None
            if not healthy:
                logger.debug("[MULTI_QA] Ollama not available, skipping %s", reviewer_name)
                await client.close()
                return None

            # DB-configured: qa_fallback_critic_model (used as the primary
            # model for gates since they should be fast/cheap). Falls back
            # to pipeline_critic_model then gemma3:27b.
            default_model = "gemma3:27b"
            temperature = 0.2
            if self.settings:
                default_model = (
                    await self.settings.get("qa_fallback_critic_model")
                    or await self.settings.get("pipeline_critic_model")
                    or default_model
                )
                temperature = float(
                    await self.settings.get("qa_temperature") or temperature
                )
            ollama_model = default_model.removeprefix("ollama/")

            try:
                result = await asyncio.wait_for(
                    client.generate(
                        prompt=prompt,
                        model=ollama_model,
                        temperature=temperature,
                        max_tokens=600,
                    ),
                    timeout=60,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[MULTI_QA] %s timed out after 60s — gate skipped", reviewer_name
                )
                await client.close()
                return None
            await client.close()

            text = result.get("text", "")
            if not text:
                return None

            json_text = text
            if "```" in text:
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
                if m:
                    json_text = m.group(1)

            try:
                data = json.loads(json_text)
            except json.JSONDecodeError:
                m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
                if not m:
                    logger.warning(
                        "[MULTI_QA] %s returned unparseable JSON: %s",
                        reviewer_name, text[:200],
                    )
                    return None
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None

            passed = bool(data.get(pass_key, False))
            score = float(data.get("score", 0))
            if reviewer_name == "topic_delivery":
                feedback = str(data.get("reason", ""))[:300]
            else:  # internal_consistency
                contradictions = data.get("contradictions") or []
                if isinstance(contradictions, list) and contradictions:
                    feedback = "Contradictions: " + "; ".join(
                        str(c)[:80] for c in contradictions[:3]
                    )
                else:
                    feedback = str(data.get("reason", "No contradictions found"))[:300]

            return ReviewerResult(
                reviewer=reviewer_name,
                approved=passed,
                score=score,
                feedback=feedback or ("passed" if passed else "failed"),
                provider="consistency_gate",
            )
        except Exception as e:
            logger.warning("[MULTI_QA] %s gate failed (non-critical): %s", reviewer_name, e)
            return None

    async def _check_topic_delivery(
        self, topic: str, content: str
    ) -> ReviewerResult | None:
        """Gate: does the content deliver what the topic promised?

        Catches bait-and-switch titles (e.g. "11 indie hackers" with no
        hackers named in the body). Returns None if Ollama is unavailable.
        """
        if not topic or not topic.strip():
            return None
        # ~1000 words of opening is enough to see the thesis and main points
        opening = content[:6000]
        prompt = TOPIC_DELIVERY_PROMPT.format(topic=topic.strip(), opening=opening)
        return await self._run_gate_prompt(
            prompt, reviewer_name="topic_delivery", pass_key="delivers"
        )

    async def _check_internal_consistency(
        self, content: str
    ) -> ReviewerResult | None:
        """Gate: does the article contradict itself across sections?

        Catches cases where section 1 says "no React" and section 3 says
        "use Next.js" without acknowledging the switch. Returns None if
        Ollama is unavailable.
        """
        if not content or not content.strip():
            return None
        prompt = CONSISTENCY_PROMPT.format(content=content[:10000])
        return await self._run_gate_prompt(
            prompt, reviewer_name="internal_consistency", pass_key="consistent"
        )

    async def _check_image_relevance(
        self, title: str, topic: str, content: str
    ) -> ReviewerResult | None:
        """Gate: do the inline images actually match what the article is about?

        Extracts up to N inline image URLs from the content, downloads them,
        and asks a vision-capable Ollama model (qwen3-vl:30b by default)
        whether each image is relevant to the content and topic. Returns
        None when disabled (default), when no images are present, or when
        the vision model is unavailable.

        Settings:
            qa_vision_check_enabled    — default "false" (opt-in; vision
                                         inference is ~10s per image)
            qa_vision_model            — default "qwen3-vl:30b"
            qa_vision_max_images       — default 3
            qa_vision_pass_threshold   — default 60 (min per-image score
                                         the gate considers "relevant")
        """
        import asyncio
        import base64
        import json
        import re

        # Feature flag
        enabled = False
        model = "qwen3-vl:30b"
        max_images = 3
        pass_threshold = 60
        if self.settings:
            try:
                enabled = str(
                    await self.settings.get("qa_vision_check_enabled") or "false"
                ).lower() == "true"
                model = (
                    await self.settings.get("qa_vision_model") or "qwen3-vl:30b"
                )
                model = model.removeprefix("ollama/")
                max_images = int(
                    await self.settings.get("qa_vision_max_images") or 3
                )
                pass_threshold = int(
                    await self.settings.get("qa_vision_pass_threshold") or 60
                )
            except Exception:
                pass

        if not enabled:
            return None
        if not content or not content.strip():
            return None

        # Find inline markdown / HTML images. Cap at max_images so a
        # 10-image article doesn't blow up inference budget.
        md_urls = re.findall(r'!\[[^\]]*\]\((https?://[^)\s]+)\)', content)
        html_urls = re.findall(r'<img[^>]+src=[\'"](https?://[^\'"]+)[\'"]', content)
        urls: list[str] = []
        for u in md_urls + html_urls:
            if u not in urls:
                urls.append(u)
            if len(urls) >= max_images:
                break
        if not urls:
            return None

        try:
            import httpx
        except Exception:
            return None

        # Download each image and base64-encode it for the Ollama chat API.
        encoded_images: list[tuple[str, str]] = []
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0)
            ) as client:
                for url in urls:
                    try:
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            continue
                        img_bytes = resp.content
                        if not img_bytes or len(img_bytes) > 8 * 1024 * 1024:
                            continue  # skip empty or oversized (>8MB)
                        encoded_images.append(
                            (url, base64.b64encode(img_bytes).decode("ascii"))
                        )
                    except Exception as e:
                        logger.debug(
                            "[VISION_QA] image download failed for %s: %s",
                            url[:60], e,
                        )
        except Exception as e:
            logger.debug("[VISION_QA] httpx client error: %s", e)
            return None

        if not encoded_images:
            return None

        # Build a context snippet from the content (first ~1500 chars + title).
        content_snippet = content[:1500]
        prompt = (
            "You are reviewing images attached to a blog post for relevance.\n\n"
            f"TITLE: {title}\n"
            f"TOPIC: {topic}\n\n"
            f"ARTICLE SNIPPET:\n{content_snippet}\n\n"
            "For EACH image attached, rate 0-100 how well the image represents "
            "the article's subject and would help a reader understand the content. "
            "A generic stock photo with no connection to the topic scores below 50. "
            "An image that directly illustrates a concept from the article scores 80+.\n\n"
            "Respond with ONLY valid JSON:\n"
            '{"scores": [int,...], "reasons": ["short reason per image", ...], "overall": int}'
        )

        # Ollama /api/chat with images — single message, images array.
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=5.0)
            ) as client:
                payload = {
                    "model": model,
                    "stream": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [b64 for _u, b64 in encoded_images],
                        }
                    ],
                    "options": {"temperature": 0.2, "num_predict": 400},
                }
                resp = await asyncio.wait_for(
                    client.post(
                        "http://host.docker.internal:11434/api/chat",
                        json=payload,
                    ),
                    timeout=150,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[VISION_QA] ollama returned HTTP %d: %s",
                        resp.status_code, resp.text[:200],
                    )
                    return None
                data = resp.json()
                text = data.get("message", {}).get("content", "")
        except Exception as e:
            logger.warning("[VISION_QA] ollama call failed (non-critical): %s", e)
            return None

        if not text:
            return None

        # Parse JSON response
        json_text = text
        if "```" in text:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                json_text = m.group(1)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            m = re.search(r"\{[^{}]*\"scores\".*?\}", text, re.DOTALL)
            if not m:
                logger.warning("[VISION_QA] unparseable response: %s", text[:200])
                return None
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None

        scores_list = parsed.get("scores") or []
        reasons_list = parsed.get("reasons") or []
        overall = parsed.get("overall")
        if not isinstance(scores_list, list) or not scores_list:
            return None
        try:
            score_values = [float(s) for s in scores_list if isinstance(s, (int, float))]
        except Exception:
            return None
        if not score_values:
            return None
        avg_score = sum(score_values) / len(score_values)
        if overall is None or not isinstance(overall, (int, float)):
            overall = avg_score

        # Build feedback with per-image detail (urls get truncated)
        parts: list[str] = []
        for i, (url, _b64) in enumerate(encoded_images):
            s = scores_list[i] if i < len(scores_list) else "?"
            r = reasons_list[i] if i < len(reasons_list) else ""
            parts.append(f"[{s}] {url[-40:]}: {str(r)[:80]}")
        feedback = f"Vision QA avg={avg_score:.0f}, overall={overall}. " + "; ".join(parts[:3])

        # Approval: the average per-image score must clear the threshold.
        passed = avg_score >= pass_threshold

        return ReviewerResult(
            reviewer="image_relevance",
            approved=passed,
            score=float(overall),
            feedback=feedback[:500],
            provider="vision_gate",
        )

    # _review_with_gemini removed — Ollama-only policy
