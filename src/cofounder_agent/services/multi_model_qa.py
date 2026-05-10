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

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone

from services.content_validator import ValidationResult, validate_content
from services.integrations.operator_notify import notify_operator
from services.llm_providers.dispatcher import resolve_tier_model
from services.logger_config import get_logger
from services.prompt_manager import get_prompt_manager
from services.qa_gates_db import load_qa_gate_chain
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


@dataclass
class ReviewerResult:
    """Result from a single reviewer.

    ``advisory`` (#399): when True, the reviewer's ``approved=False``
    must NOT cause the overall pass/fail decision to flip. The score is
    still factored into the weighted average so trend tracking works,
    but the gate's veto bit is ignored. Set when a ``qa_gates`` row has
    ``required_to_pass=False``.
    """
    reviewer: str  # "ollama_qa", "anthropic_critic", "validator"
    approved: bool
    score: float
    feedback: str
    provider: str  # "ollama", "anthropic", "programmatic"
    advisory: bool = False


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

    def format_feedback_text(self, max_chars: int = 4000) -> str:
        """Human-readable critique text for the approval UI (GH-86).

        Lands in ``pipeline_versions.qa_feedback`` /
        ``content_tasks.qa_feedback`` so approvers can see *why* the post
        scored Q85 vs Q88 instead of just the number.
        """
        header = (
            f"Final score: {self.final_score:.0f}/100 "
            f"({'APPROVED' if self.approved else 'REJECTED'})"
        )
        lines = [header]
        for r in self.reviews:
            status = "pass" if r.approved else "FAIL"
            fb = (r.feedback or "").strip() or "(no feedback)"
            lines.append(
                f"- {r.reviewer} [{r.provider}] {r.score:.0f}/100 {status}: {fb}"
            )
        if self.validation and self.validation.issues:
            for issue in self.validation.issues[:10]:
                lines.append(
                    f"- validator[{issue.severity}] {issue.category}: {issue.description}"
                )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20].rstrip() + "\n...(truncated)"
        return text


def format_qa_feedback_from_reviews(
    qa_reviews: list[dict],
    final_score: float | None = None,
    approved: bool | None = None,
    max_chars: int = 4000,
) -> str:
    """Format serialized qa_reviews into reviewer-facing text (GH-86).

    Mirrors :meth:`MultiModelResult.format_feedback_text` for callers
    that only hold the serialized dicts (e.g. when finalize reads the
    ``qa_reviews`` list from context without reconstructing the full
    :class:`MultiModelResult`).
    """
    if not qa_reviews:
        return ""
    lines: list[str] = []
    if final_score is not None:
        status_str = (
            "APPROVED" if approved
            else "REJECTED" if approved is False
            else ""
        )
        suffix = f" ({status_str})" if status_str else ""
        lines.append(f"Final score: {float(final_score):.0f}/100{suffix}")
    for r in qa_reviews:
        if not isinstance(r, dict):
            continue
        reviewer = r.get("reviewer", "unknown")
        provider = r.get("provider", "?")
        try:
            score = float(r.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        status = "pass" if r.get("approved") else "FAIL"
        fb = (r.get("feedback") or "").strip() or "(no feedback)"
        lines.append(
            f"- {reviewer} [{provider}] {score:.0f}/100 {status}: {fb}"
        )
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n...(truncated)"
    return text


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

    async def _load_gate_states(self) -> dict[str, tuple[bool, bool]]:
        """Return ``{gate_name: (enabled, required_to_pass)}`` from ``qa_gates``.

        Returns ``{}`` when the chain is empty (no pool, missing table,
        fresh checkout). Callers fall back to the legacy "every gate
        runs as required" behavior in that case so existing tests + new
        installs keep working without setup. Gate-name lookup is by the
        slug seeded in migration 0094: ``programmatic_validator``,
        ``llm_critic``, ``url_verifier``, ``consistency``,
        ``web_factcheck``, ``vision_gate``.
        """
        chain = await load_qa_gate_chain(self.pool, only_enabled=False)
        return {g.name: (g.enabled, g.required_to_pass) for g in chain}

    @staticmethod
    def _gate_enabled(states: dict[str, tuple[bool, bool]], name: str) -> bool:
        """True when the named gate is enabled — or absent (legacy default)."""
        if name not in states:
            return True
        return states[name][0]

    @staticmethod
    def _mark_advisory_if_configured(
        review: ReviewerResult | None,
        states: dict[str, tuple[bool, bool]],
        name: str,
    ) -> ReviewerResult | None:
        """Flip a ReviewerResult to advisory when ``qa_gates.required_to_pass=False``.

        Advisory mode (#399): the gate still runs and its score still
        feeds the weighted average, but its ``approved=False`` must not
        cause the overall pass/fail decision to flip. We:

        - set ``advisory=True`` so ``_reviewer_vetoes`` ignores the veto bit
        - rewrite ``approved=True`` so downstream consumers that only read
          the bool (``format_feedback_text``, ``MultiModelResult.summary``,
          legacy callers) see the post as passing this gate
        - prepend ``[advisory]`` to the feedback so operators see what
          actually happened in the audit log
        """
        if review is None:
            return None
        if name not in states:
            return review
        _enabled, required = states[name]
        if required:
            return review
        original_passed = review.approved
        review.advisory = True
        review.approved = True
        prefix = "[advisory]"
        if prefix not in review.feedback:
            status = "passed" if original_passed else "failed"
            review.feedback = (
                f"{prefix} ({status}, not required_to_pass) {review.feedback}"
            )
        return review

    async def _resolve_critic_model(
        self,
        *,
        setting_key: str,
        site: str,
    ) -> str:
        """Resolve the critic model via the cost-tier API + fallback chain.

        Lane B sweep migration. Order:
        1. ``resolve_tier_model(pool, "standard")`` — operator-tuned tier mapping.
        2. ``app_settings[setting_key]`` — per-call-site fallback (e.g.
           ``qa_fallback_critic_model``). Only used if the tier mapping
           is missing AND we successfully notify the operator.
        3. Raise — per feedback_no_silent_defaults.md, missing config is a
           configuration bug, not a quiet fallback.
        """
        try:
            return await resolve_tier_model(self.pool, "standard")
        except (RuntimeError, ValueError, AttributeError) as exc:
            fallback: str | None = None
            if self.settings is not None:
                try:
                    fallback = await self.settings.get(setting_key)
                except Exception:
                    fallback = None
            if not fallback:
                await notify_operator(
                    f"qa critic ({site}): cost_tier='standard' has no model "
                    f"AND {setting_key} is empty — review failed: {exc}",
                    critical=True,
                )
                raise
            await notify_operator(
                f"qa critic ({site}): cost_tier='standard' resolution failed "
                f"({exc}); falling back to {setting_key}={fallback!r}",
                critical=False,
            )
            return fallback

    async def review(
        self,
        title: str,
        content: str,
        topic: str = "",
        research_sources: str | None = None,
        preview_url: str | None = None,
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
            preview_url: Optional URL where the post renders as it would
                appear to readers (e.g. /preview/{hash}). When provided and
                qa_preview_screenshot_enabled is true, a new reviewer
                captures a screenshot of the rendered page and feeds it
                to the vision model for a final "yup looks good" layout
                sanity check. Without it, or with the flag off, the
                reviewer is skipped.

        Returns MultiModelResult with approval decision and individual reviews.
        """
        reviews: list[ReviewerResult] = []

        # #399: Load the declarative qa_gates chain. Empty dict means no
        # gate rows exist (fresh checkout / pool=None) → fall back to the
        # legacy "every gate runs as required" behavior. With rows present,
        # ``enabled=False`` skips the gate entirely (no LLM call) and
        # ``required_to_pass=False`` marks the result as advisory so the
        # overall pass/fail decision ignores its veto bit.
        gate_states = await self._load_gate_states()

        # 1. Programmatic validation (always runs, fast, deterministic)
        validation = validate_content(title, content, topic)
        validator_review = ReviewerResult(
            reviewer="programmatic_validator",
            approved=validation.passed,
            score=100.0 - validation.score_penalty,
            feedback="; ".join(i.description[:60] for i in validation.issues[:3]) or "No issues found",
            provider="programmatic",
        )
        # Advisory programmatic_validator (required_to_pass=False) must not
        # short-circuit-reject the post on critical issues either — it just
        # leaves an audit trail. Apply the mark BEFORE the early-return
        # branch below so the rejection path sees approved=True.
        self._mark_advisory_if_configured(
            validator_review, gate_states, "programmatic_validator",
        )
        reviews.append(validator_review)

        # If programmatic validator finds critical issues, check whether they
        # are ONLY known_wrong_fact issues. Those get a second chance via web
        # fact-check — the validator's regex might be stale while the web has
        # the truth. All other critical categories reject immediately.
        _fact_only_rejection = (
            not validation.passed
            and all(
                i.severity != "critical" or i.category == "known_wrong_fact"
                for i in validation.issues
            )
        )
        # #399: when the qa_gates row marks programmatic_validator as
        # advisory (required_to_pass=False), the immediate-rejection
        # short-circuit must NOT fire. The validator score still feeds
        # the weighted average, but the rest of the chain still runs so
        # the operator gets a full audit instead of a single-line reject.
        if not validation.passed and not _fact_only_rejection and not validator_review.advisory:
            logger.warning("[MULTI_QA] Programmatic validator rejected: %d critical issues", validation.critical_count)
            return MultiModelResult(
                approved=False,
                final_score=max(0, 100 - validation.score_penalty),
                reviews=reviews,
                validation=validation,
            )
        if _fact_only_rejection:
            logger.info(
                "[MULTI_QA] Validator flagged known_wrong_fact only — deferring to web fact-check"
            )

        # 1b. Citation verifier — HTTP HEAD every external URL in the content
        # (GH#54 / gitea#271 Phase 6). Dead links + insufficient citation
        # count feed the weighted average as a new reviewer. Behind a
        # DB-configurable flag so it can be silenced per-niche if needed.
        citation_review = await self._check_citations(content)
        if citation_review is not None:
            reviews.append(citation_review)

        # 2. Cross-model review using a DIFFERENT provider than the writer.
        # Model is resolved from app_settings.cost_tier.standard.model
        # (Lane B sweep) inside _review_with_ollama. No model_override
        # threaded here; _review_with_ollama owns the resolution.
        # #399: skip the LLM call entirely when qa_gates row "llm_critic"
        # is enabled=False — log INFO so the operator can see what ran.
        qa_cost_log = None
        critic_skipped = False
        if not self._gate_enabled(gate_states, "llm_critic"):
            logger.info("[MULTI_QA] Skipped gate 'llm_critic' (qa_gates.enabled=False)")
            critic_skipped = True
        else:
            cross_result = await self._review_with_cloud_model(
                title, content, topic,
                research_sources=research_sources,
            )
            if cross_result:
                cross_review, qa_cost_log = cross_result
                self._mark_advisory_if_configured(
                    cross_review, gate_states, "llm_critic",
                )
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
        # #399: gate name "consistency" controls this block.
        if not self._gate_enabled(gate_states, "consistency"):
            logger.info("[MULTI_QA] Skipped gate 'consistency' (qa_gates.enabled=False)")
        else:
            consistency_review = await self._check_internal_consistency(content)
            if consistency_review is not None:
                self._mark_advisory_if_configured(
                    consistency_review, gate_states, "consistency",
                )
                reviews.append(consistency_review)

        # 2d. Image relevance gate — checks whether inline images
        # actually match the content they're next to. Catches the
        # "a close-up image of a busy server room" stock-photo-for-a-
        # FastAPI-post pattern Matt flagged on 2026-04-11. Behind a
        # flag because it requires a vision-capable Ollama model.
        # #399: gate name "vision_gate" controls this block.
        if not self._gate_enabled(gate_states, "vision_gate"):
            logger.info("[MULTI_QA] Skipped gate 'vision_gate' (qa_gates.enabled=False)")
        else:
            image_review = await self._check_image_relevance(title, topic, content)
            if image_review is not None:
                self._mark_advisory_if_configured(
                    image_review, gate_states, "vision_gate",
                )
                reviews.append(image_review)

        # 2e. Web fact-check gate — uses DuckDuckGo to verify claims
        # that the LLM critic or validator flagged. Catches training-
        # cutoff false positives: if the web confirms a claim about a
        # post-cutoff product, the gate overrides the rejection.
        # #399: gate name "web_factcheck" controls this block. Both the
        # qa_gates row AND the legacy app_settings flag must allow it
        # (defense in depth — operators may have toggled either off).
        web_fc_enabled = True
        if self.settings:
            web_fc_enabled = (await self.settings.get("qa_web_factcheck_enabled") or "true").lower() != "false"
        if not self._gate_enabled(gate_states, "web_factcheck"):
            logger.info("[MULTI_QA] Skipped gate 'web_factcheck' (qa_gates.enabled=False)")
        elif web_fc_enabled:
            web_review = await self._web_fact_check(title, topic, content, reviews)
            if web_review is not None:
                self._mark_advisory_if_configured(
                    web_review, gate_states, "web_factcheck",
                )
                reviews.append(web_review)

        # 2f. URL verification gate — checks cited links actually resolve (#214)
        # Not a hard gate — dead links are critical (block), but having good
        # citations is rewarded with a score bonus (carrot, not stick).
        # #399: gate name "url_verifier" controls this block.
        if not self._gate_enabled(gate_states, "url_verifier"):
            logger.info("[MULTI_QA] Skipped gate 'url_verifier' (qa_gates.enabled=False)")
        else:
            try:
                from services.content_validator import verify_content_urls
                url_issues = await verify_content_urls(content)
                dead_links = [i for i in url_issues if i.category == "dead_link"]

                if dead_links:
                    # Dead links block publish — this is a fabricated/hallucinated URL
                    url_review = ReviewerResult(
                        reviewer="url_verifier",
                        approved=False,
                        score=max(0, 100 - len(dead_links) * 20),
                        feedback="; ".join(i.description[:60] for i in dead_links[:3]),
                        provider="programmatic",
                    )
                    self._mark_advisory_if_configured(url_review, gate_states, "url_verifier")
                    reviews.append(url_review)
                    logger.warning("[MULTI_QA] URL verifier: %d dead links found", len(dead_links))
                else:
                    # Count verified external citations — bonus scoring.
                    # "Internal" domains (the operator's own site) are excluded
                    # from the citation count. Read site_domain from site_config
                    # so forked Poindexter installs filter their own domain, not
                    # Glad Labs' — prevents a fork's self-links from being
                    # counted as "external citations" and inflating the score.
                    import re as _re
                    from urllib.parse import urlparse as _urlparse
                    _internal_domains: set[str] = {"localhost"}
                    try:
                        _site_domain = (site_config.get("site_domain", "") or "").lower().strip()
                        if _site_domain:
                            _internal_domains.add(_site_domain)
                            _internal_domains.add(f"www.{_site_domain}")
                    except Exception:
                        pass
                    _ext_urls = [
                        m.group(2) for m in _re.finditer(r'\[([^\]]*)\]\((https?://[^)]+)\)', content)
                        if _urlparse(m.group(2)).netloc.lower() not in _internal_domains
                    ]
                    citation_count = len(_ext_urls)
                    # Reward: +5 per verified citation, max +15
                    citation_bonus = min(15, citation_count * 5)
                    url_score = min(100, 80 + citation_bonus)
                    url_review = ReviewerResult(
                        reviewer="url_verifier",
                        approved=True,
                        score=url_score,
                        feedback=f"{citation_count} verified external citations (+{citation_bonus} bonus)" if citation_count else "No external citations (no penalty, but citations encouraged)",
                        provider="programmatic",
                    )
                    self._mark_advisory_if_configured(url_review, gate_states, "url_verifier")
                    reviews.append(url_review)
                    if citation_count:
                        logger.info("[MULTI_QA] URL verifier: %d verified citations (+%d bonus)", citation_count, citation_bonus)
            except Exception as e:
                logger.debug("[MULTI_QA] URL verification skipped: %s", e)

        # 2g. DeepEval brand-fabrication rail — first production wire-in
        # of DeepEval (sub-issue 1 of glad-labs-stack#329). Runs a
        # DeepEval BaseMetric that wraps content_validator's fabrication
        # pattern sets. Advisory by default — score feeds the weighted
        # average but never vetoes publish. The metric is duplicative
        # with programmatic_validator at this stage; the goal is to
        # exercise the deepeval call-path so the heavier follow-ups
        # (G-Eval, FaithfulnessMetric) reuse the same plumbing.
        # #399: gate name "deepeval_brand_fabrication".
        if not self._gate_enabled(gate_states, "deepeval_brand_fabrication"):
            logger.info(
                "[MULTI_QA] Skipped gate 'deepeval_brand_fabrication' (qa_gates.enabled=False)",
            )
        else:
            deepeval_review = self._check_deepeval_brand(content, topic)
            if deepeval_review is not None:
                self._mark_advisory_if_configured(
                    deepeval_review, gate_states, "deepeval_brand_fabrication",
                )
                reviews.append(deepeval_review)

        # 2g.2. DeepEval G-Eval (LLM-judge) — sub-issue 1 of #329.
        # Uses the configured judge model to grade the post against
        # ``deepeval_g_eval_criterion`` (default: groundedness +
        # internal consistency + no invented facts). Advisory by
        # default — operator can promote it to required via the
        # qa_gates row.
        if not self._gate_enabled(gate_states, "deepeval_g_eval"):
            logger.info(
                "[MULTI_QA] Skipped gate 'deepeval_g_eval' (qa_gates.enabled=False)",
            )
        else:
            ge_review = await self._check_deepeval_g_eval(content, topic)
            if ge_review is not None:
                self._mark_advisory_if_configured(
                    ge_review, gate_states, "deepeval_g_eval",
                )
                reviews.append(ge_review)

        # 2g.3. DeepEval Faithfulness — only fires when research_sources
        # is non-empty. Splits the corpus into paragraph chunks and
        # asks the judge whether every claim in the post is attributable
        # to one of them. Advisory by default.
        if not self._gate_enabled(gate_states, "deepeval_faithfulness"):
            logger.info(
                "[MULTI_QA] Skipped gate 'deepeval_faithfulness' (qa_gates.enabled=False)",
            )
        else:
            ff_review = await self._check_deepeval_faithfulness(
                content, research_sources,
            )
            if ff_review is not None:
                self._mark_advisory_if_configured(
                    ff_review, gate_states, "deepeval_faithfulness",
                )
                reviews.append(ff_review)

        # 2g.4. Guardrails-AI brand-fabrication rail — sub-issue 3 of #329.
        # Same regex patterns as content_validator + the deepeval brand
        # rail, but routed through guardrails-ai's Validator/Guard
        # framework. Cross-framework signal: the two agreeing/disagreeing
        # on a draft is itself a correlation-drift indicator.
        if not self._gate_enabled(gate_states, "guardrails_brand"):
            logger.info(
                "[MULTI_QA] Skipped gate 'guardrails_brand' (qa_gates.enabled=False)",
            )
        else:
            gr_brand_review = await self._check_guardrails_brand(content)
            if gr_brand_review is not None:
                self._mark_advisory_if_configured(
                    gr_brand_review, gate_states, "guardrails_brand",
                )
                reviews.append(gr_brand_review)

        # 2g.5. Guardrails-AI competitor-mention rail — flags when
        # operator-listed competitor brand names appear in the post.
        # Skipped entirely when guardrails_competitor_list is empty
        # (no list = no enforcement). Fills a gap DeepEval doesn't.
        if not self._gate_enabled(gate_states, "guardrails_competitor"):
            logger.info(
                "[MULTI_QA] Skipped gate 'guardrails_competitor' (qa_gates.enabled=False)",
            )
        else:
            gr_comp_review = await self._check_guardrails_competitor(content)
            if gr_comp_review is not None:
                self._mark_advisory_if_configured(
                    gr_comp_review, gate_states, "guardrails_competitor",
                )
                reviews.append(gr_comp_review)

        # 2h. Rendered-preview gate — the final "yup looks good"
        # sanity check. Screenshots the post's /preview/{hash} URL
        # via Playwright-chromium and feeds the PNG to the vision
        # model to catch layout breaks, missing CSS, overflowing
        # tables, broken images, mangled HTML — the stuff that no
        # text-only QA can see. Opt-in via qa_preview_screenshot_enabled.
        # Skipped entirely if preview_url is None.
        if preview_url:
            preview_review = await self._check_rendered_preview(
                title, topic, preview_url
            )
            if preview_review is not None:
                reviews.append(preview_review)

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
                "web_factcheck": gate_weight,
                "url_verifier": gate_weight,
            }
            total_weight = sum(weights.get(r.provider, 0.5) for r in scored_reviews)
            final_score = sum(
                r.score * weights.get(r.provider, 0.5) for r in scored_reviews
            ) / total_weight if total_weight > 0 else 0
        else:
            final_score = validator_review.score

        # GH-91: apply a direct penalty to the final QA score per validator
        # warning. Previously warnings only dragged the validator's own
        # sub-score (weight 0.4), which meant 9 `unlinked_citation`
        # warnings shaved ~11 pts off the weighted average — not enough
        # to cross the Q70 threshold when the critic scored 85. By
        # penalizing the final score directly, 9 warnings now cost
        # ~27 pts at the default 3/warning rate, dropping a Q85 post
        # into Q58 reject territory. Configurable per-site via
        # `content_validator_warning_qa_penalty` (default 3).
        warning_count = validation.warning_count if validation else 0
        if warning_count > 0:
            per_warning_penalty = 3
            if self.settings:
                with suppress(ValueError, TypeError):
                    _raw = await self.settings.get(
                        "content_validator_warning_qa_penalty"
                    )
                    if _raw is not None:
                        per_warning_penalty = int(_raw)
            if per_warning_penalty > 0:
                total_penalty = warning_count * per_warning_penalty
                final_score = max(0.0, final_score - total_penalty)
                logger.info(
                    "[MULTI_QA] Applied warning penalty: %d warnings × %d pts = -%d "
                    "(final_score now %.1f)",
                    warning_count, per_warning_penalty, total_penalty, final_score,
                )

        # Hard-gate pass check — the consistency gate is treated as advisory
        # unless its own score is unambiguously low (< 50). The topic-delivery
        # gate stays binary because title/body mismatch is usually clear-cut.
        consistency_veto_threshold = 50.0
        if self.settings:
            with suppress(ValueError, TypeError):
                consistency_veto_threshold = float(
                    await self.settings.get("qa_consistency_veto_threshold") or 50
                )

        def _reviewer_vetoes(r: ReviewerResult) -> bool:
            if r.approved:
                return False
            # #399: advisory gates (qa_gates.required_to_pass=False) never veto.
            # Their score still feeds the weighted average for trend tracking,
            # but a failing run cannot flip the overall pass/fail decision.
            if r.advisory:
                return False
            if r.reviewer == "internal_consistency":
                return r.score > 0 and r.score < consistency_veto_threshold
            return True

        all_passed = not any(_reviewer_vetoes(r) for r in reviews)
        if critic_skipped:
            # Validator-only: use its raw score, don't pretend cross-model passed
            final_score = validator_review.score

        # If the validator had a fact-only rejection, check whether the web
        # fact-check gate rescued it. If web verified the claims (approved=True),
        # override the validator's rejection. If not, hard reject.
        if _fact_only_rejection:
            web_fc = next((r for r in reviews if r.reviewer == "web_factcheck"), None)
            if web_fc and web_fc.approved:
                logger.info("[MULTI_QA] Web fact-check overrides known_wrong_fact rejection")
                # Restore validator approval since web confirmed the facts
                validator_review.approved = True
                all_passed = not any(_reviewer_vetoes(r) for r in reviews)
            else:
                logger.warning("[MULTI_QA] Web fact-check did NOT verify — upholding rejection")
                all_passed = False

        approved = all_passed and final_score >= approval_threshold

        result = MultiModelResult(
            approved=approved,
            final_score=final_score,
            reviews=reviews,
            validation=validation,
            cost_log=qa_cost_log,
        )

        # Bump qa_gates run counters so the operator dashboard stops
        # showing every gate as last_run_at=NEVER. Best-effort — never
        # fail the chain because telemetry write hiccupped.
        try:
            from services.qa_gates_db_writer import record_chain_run
            await record_chain_run(self.pool, reviews)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MULTI_QA] qa_gates counter update skipped: %s", exc)

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
        # Cost-tier API (Lane B sweep) — operator-tuned. The
        # qa_fallback_critic_model setting remains the per-call-site
        # backstop; if both are missing, _resolve_critic_model raises
        # via notify_operator per feedback_no_silent_defaults.md.
        fallback_model = (
            await self._resolve_critic_model(
                setting_key="qa_fallback_critic_model",
                site="critic_fallback",
            )
        ).removeprefix("ollama/")
        if model_override != fallback_model:
            logger.warning(
                "[MULTI_QA] Primary critic %s failed (empty response or error), falling back to %s",
                model_override, fallback_model,
            )
            # Loud audit event — makes critic fallback visible on the
            # /pipeline dashboard. Without this, a thinking-mode critic
            # that eats its own token budget silently degrades every
            # run and nobody notices.
            try:
                from services.audit_log import audit_log_bg
                audit_log_bg(
                    "critic_fallback", "multi_model_qa",
                    {
                        "configured_critic": model_override or "default",
                        "fallback_critic": fallback_model,
                        "reason": "primary_returned_empty_or_errored",
                        "stage": "cross_model_qa",
                    },
                    severity="warning",
                )
            except Exception as _exc:
                logger.debug("critic_fallback audit failed: %s", _exc)
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
    ) -> tuple[ReviewerResult, dict] | None:
        """Review content using local Ollama (zero cost).

        Model resolved via ``cost_tier="standard"`` (Lane B sweep) —
        operators tune ``app_settings.cost_tier.standard.model`` to
        switch the critic without code edits.

        Returns a ``(review, cost_log)`` tuple on success, or ``None`` if
        the model was unreachable / returned unparseable output. The
        caller (``_review_with_cloud_model``) passes the tuple straight
        through and its outer caller unpacks to ``cross_review,
        qa_cost_log = cross_result``.
        """
        import asyncio
        import json
        import re

        try:
            # v2.3 deliberate non-migration: this path stays on the concrete
            # OllamaClient because it uses Ollama-specific features that the
            # Provider Protocol intentionally does not expose:
            #   - configure_electricity() for local GPU cost attribution
            #   - check_health() short-circuit
            #   - raw result dict fields (cost, duration_seconds, tokens)
            # These are the core of the critic-cost telemetry (see
            # feedback_no_paid_apis + session_58 electricity tracking).
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
            prompt = get_prompt_manager().get_prompt(
                "qa.review",
                title=title,
                topic=topic or title,
                content=content[:8000],
                current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                sources_block=sources_block,
            )

            # Cost-tier API (Lane B sweep). Operators tune the standard
            # tier via app_settings.cost_tier.standard.model — no code edit
            # per niche. The qa_fallback_critic_model setting remains as
            # last-ditch backstop per feedback_no_silent_defaults.md: a
            # missing tier mapping fails loudly via notify_operator before
            # falling back.
            thinking_max = 8000  # Thinking models need budget for reasoning + actual review output
            standard_max = 1500
            temperature = 0.3
            if self.settings:
                thinking_max = int(await self.settings.get("qa_thinking_model_max_tokens") or thinking_max)
                standard_max = int(await self.settings.get("qa_standard_max_tokens") or standard_max)
                temperature = float(await self.settings.get("qa_temperature") or temperature)
            if model_override:
                resolved_model = model_override
            else:
                resolved_model = await self._resolve_critic_model(
                    setting_key="qa_fallback_critic_model",
                    site="critic",
                )
            ollama_model = resolved_model.removeprefix("ollama/")
            from services.llm_providers.thinking_models import (
                is_thinking_model as _is_thinking_model,
                resolve_thinking_substrings,
            )
            _is_thinking = _is_thinking_model(
                ollama_model, substrings=resolve_thinking_substrings(site_config)
            )
            max_tok = thinking_max if _is_thinking else standard_max
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
            # v2.3 deliberate non-migration: same rationale as the main
            # critic call above — electricity tracking + health probe are
            # OllamaClient-specific and essential for cost telemetry.
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

            # Cost-tier API (Lane B sweep). Gates use the standard tier
            # since they're fast/cheap critic prompts. The
            # qa_fallback_critic_model setting remains the operator's
            # explicit per-call-site override; if both the tier mapping
            # AND the fallback are unset, notify_operator + raise per
            # feedback_no_silent_defaults.md.
            temperature = 0.2
            if self.settings:
                temperature = float(
                    await self.settings.get("qa_temperature") or temperature
                )
            resolved_model = await self._resolve_critic_model(
                setting_key="qa_fallback_critic_model",
                site=f"gate:{reviewer_name}",
            )
            ollama_model = resolved_model.removeprefix("ollama/")

            _gate_max = site_config.get_int("qa_gate_max_tokens", 600)
            _gate_timeout = site_config.get_int("qa_gate_timeout_seconds", 60)
            try:
                result = await asyncio.wait_for(
                    client.generate(
                        prompt=prompt,
                        model=ollama_model,
                        temperature=temperature,
                        max_tokens=_gate_max,
                    ),
                    timeout=_gate_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[MULTI_QA] %s timed out after %ds — gate skipped",
                    reviewer_name, _gate_timeout,
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

    def _check_deepeval_brand(
        self, content: str, topic: str,
    ) -> ReviewerResult | None:
        """Run the DeepEval BrandFabricationMetric and return a ReviewerResult.

        Sync because the underlying metric is pure-CPU regex matching —
        no async I/O. Wraps ``deepeval_rails.evaluate_brand_fabrication``
        which is already error-swallowing (returns ``(True, 1.0, reason)``
        on import or runtime failure). Returns ``None`` only if the
        deepeval rail is globally disabled via ``deepeval_enabled=false``.

        Score mapping: deepeval returns 0.0–1.0 where 1.0 is clean.
        We rescale to 0–100 to match the rest of the QA reviewers.
        """
        try:
            from services import deepeval_rails
        except ImportError:
            # Module itself missing — should never happen since
            # services/deepeval_rails.py ships with the worker.
            return None

        # Operator gate. Default false in deepeval_rails.is_enabled, but
        # the migration seeds app_settings.deepeval_enabled='true' so
        # this is on out-of-the-box. If the operator turns it off, skip.
        try:
            if not deepeval_rails.is_enabled(site_config):
                return None
        except Exception:
            # site_config missing is fine — the rail's is_enabled
            # already returns False in that case, but we double-check.
            return None

        passed, score_unit, reason = deepeval_rails.evaluate_brand_fabrication(
            content, topic,
        )
        # Convert 0.0–1.0 to 0–100. Brand metric is binary today (0 or 1),
        # but we rescale generally so future metrics (G-Eval, Faithfulness)
        # that return graded 0.0–1.0 scores fit the same shape.
        score_100 = round(float(score_unit) * 100.0, 1)
        return ReviewerResult(
            reviewer="deepeval_brand_fabrication",
            approved=bool(passed),
            score=score_100,
            feedback=reason or "",
            provider="deepeval",
        )

    async def _check_deepeval_g_eval(
        self, content: str, topic: str,
    ) -> ReviewerResult | None:
        """Run DeepEval's G-Eval (LLM-judge) and return a ReviewerResult.

        Async because the judge model issues an LLM call. The underlying
        ``deepeval_rails.evaluate_g_eval`` is sync (DeepEval's measure()
        is sync) but we wrap it in ``asyncio.to_thread`` so the
        FastAPI event loop isn't blocked while the judge runs.

        Returns ``None`` if the rail is globally disabled. Threshold +
        criterion + judge model are pulled from app_settings so operators
        can tune without a code change.
        """
        try:
            from services import deepeval_rails
        except ImportError:
            return None
        try:
            if not deepeval_rails.is_enabled(site_config):
                return None
        except Exception:
            return None

        threshold = 0.7
        criterion = deepeval_rails._DEFAULT_G_EVAL_CRITERION
        judge_model = deepeval_rails._resolve_judge_model(site_config)
        if self.settings:
            try:
                raw = await self.settings.get("deepeval_threshold_g_eval")
                if raw is not None:
                    threshold = float(raw)
            except (TypeError, ValueError):
                pass
            try:
                raw_c = await self.settings.get("deepeval_g_eval_criterion")
                if raw_c is not None and str(raw_c).strip():
                    criterion = str(raw_c).strip()
            except Exception:
                pass

        try:
            passed, score_unit, reason = await asyncio.to_thread(
                deepeval_rails.evaluate_g_eval,
                content,
                topic,
                criterion=criterion,
                judge_model=judge_model,
                threshold=threshold,
            )
        except Exception as exc:
            logger.warning("[MULTI_QA] deepeval_g_eval reviewer error: %s", exc)
            return None

        score_100 = round(float(score_unit) * 100.0, 1)
        return ReviewerResult(
            reviewer="deepeval_g_eval",
            approved=bool(passed),
            score=score_100,
            feedback=reason or "",
            provider="deepeval",
        )

    async def _check_deepeval_faithfulness(
        self, content: str, research_sources: str | None,
    ) -> ReviewerResult | None:
        """Run DeepEval's FaithfulnessMetric against the research bundle.

        Splits ``research_sources`` (the same corpus the writer was given)
        into paragraph-level snippets and asks the judge whether every
        claim in ``content`` is attributable to one of them. Returns
        ``None`` when the rail is disabled or there is no research
        corpus to ground against — the metric cannot run without context.
        """
        try:
            from services import deepeval_rails
        except ImportError:
            return None
        try:
            if not deepeval_rails.is_enabled(site_config):
                return None
        except Exception:
            return None

        if not research_sources or not research_sources.strip():
            # Nothing to ground against. The brand-fabrication rail
            # still catches the regex-detectable fabrications.
            return None

        threshold = 0.8
        judge_model = deepeval_rails._resolve_judge_model(site_config)
        if self.settings:
            try:
                raw = await self.settings.get("deepeval_threshold_faithfulness")
                if raw is not None:
                    threshold = float(raw)
            except (TypeError, ValueError):
                pass

        # Paragraph-level chunks keep the metric's per-claim attribution
        # tractable (a single 5KB blob makes the judge punt).
        chunks = [
            p.strip() for p in research_sources.split("\n\n") if p.strip()
        ]
        if not chunks:
            return None

        try:
            passed, score_unit, reason = await asyncio.to_thread(
                deepeval_rails.evaluate_faithfulness,
                content,
                chunks,
                judge_model=judge_model,
                threshold=threshold,
            )
        except Exception as exc:
            logger.warning("[MULTI_QA] deepeval_faithfulness reviewer error: %s", exc)
            return None

        score_100 = round(float(score_unit) * 100.0, 1)
        return ReviewerResult(
            reviewer="deepeval_faithfulness",
            approved=bool(passed),
            score=score_100,
            feedback=reason or "",
            provider="deepeval",
        )

    async def _check_guardrails_brand(
        self, content: str,
    ) -> ReviewerResult | None:
        """Run the guardrails-ai brand-fabrication validator.

        Parallel signal to ``_check_deepeval_brand`` — same patterns
        but routed through guardrails-ai instead of DeepEval. The two
        agreeing/disagreeing on a draft is itself a learnable signal
        (correlation drift = one of the framework wrappers has a bug).

        Returns ``None`` when the rail is globally disabled via
        ``app_settings.guardrails_enabled=false``. Pure-CPU regex
        matching, so we wrap in ``asyncio.to_thread`` only as a
        defensive measure — the call typically returns in <1ms.
        """
        try:
            from services import guardrails_rails
        except ImportError:
            return None
        try:
            if not guardrails_rails.is_enabled(site_config):
                return None
        except Exception:
            return None

        try:
            ok, reason = await asyncio.to_thread(
                guardrails_rails.run_brand_guard, content,
            )
        except Exception as exc:
            logger.warning("[MULTI_QA] guardrails_brand reviewer error: %s", exc)
            return None

        return ReviewerResult(
            reviewer="guardrails_brand",
            approved=bool(ok),
            score=100.0 if ok else 0.0,
            feedback=(reason or "")[:300] if not ok else "no fabrication",
            provider="guardrails",
        )

    async def _check_guardrails_competitor(
        self, content: str,
    ) -> ReviewerResult | None:
        """Run the guardrails-ai competitor-mention validator.

        Reads the competitor list from
        ``app_settings.guardrails_competitor_list`` (CSV). Empty list →
        skip entirely (no operator-configured list, no enforcement).

        This is a real brand-protection gap that DeepEval's content
        rails don't cover — accidental mentions of named competitors
        in branded posts. Operator seeds the list once; every post
        thereafter gets the check for free.
        """
        try:
            from services import guardrails_rails
        except ImportError:
            return None
        try:
            if not guardrails_rails.is_enabled(site_config):
                return None
        except Exception:
            return None

        competitors = guardrails_rails._resolve_competitors(site_config)
        if not competitors:
            # No list configured — no enforcement (matches the rail's
            # own behavior, but skip the thread hop entirely).
            return None

        try:
            ok, reason = await asyncio.to_thread(
                guardrails_rails.run_competitor_guard, content, competitors,
            )
        except Exception as exc:
            logger.warning(
                "[MULTI_QA] guardrails_competitor reviewer error: %s", exc,
            )
            return None

        return ReviewerResult(
            reviewer="guardrails_competitor",
            approved=bool(ok),
            score=100.0 if ok else 0.0,
            feedback=(reason or "")[:300] if not ok else "no competitor mentions",
            provider="guardrails",
        )

    async def _check_citations(self, content: str) -> ReviewerResult | None:
        """Verify every external URL cited in ``content`` resolves + enforce
        a minimum-citation floor. Part of Phase 6 / GH#54 source-verified
        pipeline. Returns a ReviewerResult with approval=False if dead-link
        ratio exceeds ``qa_citation_max_dead_ratio`` (default 0.30) or the
        citation count is below ``qa_citation_min_count`` (default 0, i.e.
        disabled unless an operator opts in). Returns None when the feature
        is flagged off entirely via ``qa_citation_verify_enabled=false``.

        All behaviors are DB-configurable per the project's app_settings
        pattern — operators can soften the gate per-niche if a niche has
        fewer natural citation targets.
        """
        # Feature flag — default on.
        enabled = True
        if self.settings:
            val = await self.settings.get("qa_citation_verify_enabled")
            if val is not None:
                enabled = str(val).lower() not in ("false", "0", "no")
        if not enabled:
            return None

        max_dead_ratio = 0.30
        min_count = 0
        timeout_s = 8.0
        concurrency = 5
        site_url = None
        if self.settings:
            try:
                raw = await self.settings.get("qa_citation_max_dead_ratio")
                if raw is not None:
                    max_dead_ratio = float(raw)
            except (TypeError, ValueError):
                pass
            try:
                raw = await self.settings.get("qa_citation_min_count")
                if raw is not None:
                    min_count = int(raw)
            except (TypeError, ValueError):
                pass
            try:
                raw = await self.settings.get("qa_citation_timeout_seconds")
                if raw is not None:
                    timeout_s = float(raw)
            except (TypeError, ValueError):
                pass
            site_url = await self.settings.get("site_url") or None

        try:
            from services.citation_verifier import (
                verdict_from_report,
                verify_citations,
            )
            report = await verify_citations(
                content,
                site_url=site_url,
                timeout_s=timeout_s,
                concurrency=concurrency,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MULTI_QA] citation_verifier raised (non-fatal): %s", exc,
            )
            return None

        passed, reason = await verdict_from_report(
            report, max_dead_ratio=max_dead_ratio, min_citations=min_count,
        )
        # Score: 100 when alive + >=min, linearly scaled down with dead ratio.
        # Skip (None → neutral score=85) when there were no external URLs
        # and min_count=0 — a citation-free post isn't penalized if the
        # operator didn't require citations.
        if report.unique_urls == 0:
            if min_count == 0:
                return None  # No citations to grade; skip the reviewer silently.
            score = 0.0
        else:
            score = max(0.0, 100.0 * (1.0 - report.dead_ratio))
        logger.info(
            "[MULTI_QA] citation_verifier: %s (%d urls, %d dead, %.0f%% alive)",
            "PASS" if passed else "FAIL",
            report.unique_urls, len(report.dead),
            100.0 * (1.0 - report.dead_ratio),
        )
        return ReviewerResult(
            reviewer="citation_verifier",
            approved=passed,
            score=float(score),
            feedback=reason,
            provider="http_head",
        )

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
        prompt = get_prompt_manager().get_prompt(
            "qa.topic_delivery", topic=topic.strip(), opening=opening,
        )
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
        prompt = get_prompt_manager().get_prompt(
            "qa.consistency", content=content[:10000],
        )
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
                        site_config.get("ollama_base_url", "http://host.docker.internal:11434") + "/api/chat",
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

    async def _check_rendered_preview(
        self, title: str, topic: str, preview_url: str
    ) -> ReviewerResult | None:
        """Gate: does the rendered preview page look like a real blog post?

        Captures a screenshot of ``preview_url`` via headless chromium
        and sends it to the vision model for a holistic layout/quality
        check. Catches issues that no text-only QA can see: overflowing
        tables, missing CSS, broken images, empty sections, mangled
        blockquotes, and general "this looks amateur" vibes.

        Opt-in via qa_preview_screenshot_enabled (default false). The
        reviewer returns None — skipped, no veto — when the flag is off,
        when Playwright isn't installed, when the screenshot fails, or
        when the vision model is unreachable.

        Settings:
            qa_preview_screenshot_enabled  — default "false"
            qa_preview_vision_model        — default "qwen3-vl:30b"
            qa_preview_pass_threshold      — default 70 (min score)
            qa_preview_viewport_width      — default 1280
            qa_preview_viewport_height     — default 1024
        """
        import asyncio
        import base64
        import json
        import re

        enabled = False
        model = "qwen3-vl:30b"
        pass_threshold = 70
        viewport_width = 1280
        viewport_height = 1024
        if self.settings:
            try:
                enabled = str(
                    await self.settings.get("qa_preview_screenshot_enabled") or "false"
                ).lower() == "true"
                model = (
                    await self.settings.get("qa_preview_vision_model") or model
                )
                model = model.removeprefix("ollama/")
                pass_threshold = int(
                    await self.settings.get("qa_preview_pass_threshold") or 70
                )
                viewport_width = int(
                    await self.settings.get("qa_preview_viewport_width") or 1280
                )
                viewport_height = int(
                    await self.settings.get("qa_preview_viewport_height") or 1024
                )
            except Exception:
                pass

        if not enabled or not preview_url:
            return None

        try:
            from services.preview_screenshot import capture_preview_screenshot
        except Exception as e:
            logger.debug("[PREVIEW_QA] screenshot service import failed: %s", e)
            return None

        png_bytes = await capture_preview_screenshot(
            preview_url,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            full_page=True,
        )
        if not png_bytes:
            return None

        b64 = base64.b64encode(png_bytes).decode("ascii")

        prompt = (
            "You are the final visual reviewer for a blog post before it goes "
            "live. The attached image is a full-page screenshot of the post "
            "as it will appear to readers.\n\n"
            f"TITLE: {title}\n"
            f"TOPIC: {topic}\n\n"
            "Rate 0-100 how professional and readable the rendered page looks. "
            "Deductions for:\n"
            "  - Broken or missing images (placeholder icons, alt text showing)\n"
            "  - Layout problems (overflowing tables, code blocks spilling outside the container)\n"
            "  - Empty or near-empty sections\n"
            "  - Mangled HTML (raw tags visible, unclosed quotes, escaped entities)\n"
            "  - Visually unbalanced pages (a wall of text with no breaks)\n"
            "  - Anything that would make a reader bounce in 3 seconds\n\n"
            "A clean, professional post scores 80+. A post with any ONE serious "
            "visual defect scores below 60.\n\n"
            "Respond with ONLY valid JSON:\n"
            '{"score": int, "approved": true/false, "issues": ["specific visual problem 1", ...]}'
        )

        try:
            import httpx
        except Exception:
            return None

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(180.0, connect=5.0)
            ) as client:
                payload = {
                    "model": model,
                    "stream": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [b64],
                        }
                    ],
                    "options": {"temperature": 0.2, "num_predict": 500},
                }
                resp = await asyncio.wait_for(
                    client.post(
                        site_config.get("ollama_base_url", "http://host.docker.internal:11434") + "/api/chat",
                        json=payload,
                    ),
                    timeout=200,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[PREVIEW_QA] ollama returned HTTP %d: %s",
                        resp.status_code, resp.text[:200],
                    )
                    return None
                data = resp.json()
                text = data.get("message", {}).get("content", "")
        except Exception as e:
            logger.warning("[PREVIEW_QA] ollama call failed (non-critical): %s", e)
            return None

        if not text:
            return None

        # Parse JSON
        json_text = text
        if "```" in text:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                json_text = m.group(1)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            m = re.search(r"\{[^{}]*\"score\".*?\}", text, re.DOTALL)
            if not m:
                logger.warning("[PREVIEW_QA] unparseable response: %s", text[:200])
                return None
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None

        try:
            score = float(parsed.get("score", 0))
        except Exception:
            return None
        issues = parsed.get("issues") or []
        if not isinstance(issues, list):
            issues = [str(issues)]
        passed = score >= pass_threshold and (
            bool(parsed.get("approved", True)) if "approved" in parsed else True
        )

        feedback_parts = [f"Preview screenshot QA: {int(score)}/100"]
        if issues:
            feedback_parts.append(
                "Issues: " + "; ".join(str(i)[:60] for i in issues[:3])
            )
        feedback = " — ".join(feedback_parts)[:500]

        return ReviewerResult(
            reviewer="rendered_preview",
            approved=passed,
            score=score,
            feedback=feedback,
            provider="vision_gate",
        )

    async def _web_fact_check(
        self,
        title: str,  # noqa: ARG002 — reserved for future title-level grounding
        topic: str,  # noqa: ARG002 — reserved for future topic-level grounding
        content: str,
        existing_reviews: list[ReviewerResult],
    ) -> ReviewerResult | None:
        """Web-grounded fact check for claims the LLM critic can't verify.

        Extracts product names, version numbers, and spec claims from the
        content, runs a quick DuckDuckGo search, and checks whether the
        web corroborates or contradicts them. This is the fix for the
        training-cutoff problem: local models reject "RTX 5090 has 32GB
        VRAM" because they were trained before release, but a web search
        confirms it in seconds.

        Returns a ReviewerResult with provider='web_factcheck'.
        Returns None if no checkable claims found or search fails.
        """
        import re

        try:
            from services.web_research import WebResearcher

            # Extract product/hardware claims worth verifying.
            # Look for patterns like "RTX 5090", "Llama 4", version numbers,
            # and spec claims (e.g., "32GB VRAM", "192GB/s bandwidth").
            product_patterns = [
                # GPU/hardware: "RTX 5090", "RX 9070", "M4 Ultra"
                r"\b(?:RTX|GTX|RX|Arc|M\d)\s*\d{3,5}(?:\s*(?:Ti|XT|Super|Ultra|Max|Pro))?\b",
                # Specific spec claims: "32GB VRAM", "256-bit bus"
                r"\b\d+\s*(?:GB|TB|MHz|GHz)\s+(?:VRAM|RAM|memory|DDR\w*|GDDR\w*|bandwidth|bus)\b",
                # Software versions: "Python 3.14", "Node.js 24", "CUDA 13"
                r"\b(?:Python|Node\.?js?|CUDA|PyTorch|TensorFlow|Docker|Kubernetes)\s+\d+(?:\.\d+)*\b",
                # LLM models: "Llama 4", "Gemma 3", "Qwen 3.5"
                r"\b(?:Llama|Gemma|Qwen|Mistral|Phi|DeepSeek|Command R)\s*\d+(?:\.\d+)*\b",
            ]

            claims = set()
            clean = re.sub(r"<[^>]+>", "", content)
            for pat in product_patterns:
                for m in re.finditer(pat, clean, re.IGNORECASE):
                    claims.add(m.group(0).strip())

            if not claims:
                logger.debug("[WEB_FACTCHECK] No product/spec claims found, skipping")
                return None

            # Also check if any reviewer flagged uncertain/fabrication concerns
            critic_concerned = any(
                not r.approved or r.score < 75
                for r in existing_reviews
                if r.provider == "ollama"
            )

            # Limit to 3 most important claims to keep searches fast
            claims_list = sorted(claims)[:3]
            logger.info(
                "[WEB_FACTCHECK] Verifying %d claims: %s (critic_concerned=%s)",
                len(claims_list), claims_list, critic_concerned,
            )

            researcher = WebResearcher()
            verified = 0
            contradicted = 0
            evidence_lines = []

            for claim in claims_list:
                # Build a focused search query
                query = f"{claim} specs official"
                results = await researcher.search(query, num_results=3)
                if not results:
                    evidence_lines.append(f"  {claim}: no web results found")
                    continue

                # Check if any result's content/snippet mentions the claim
                combined_text = " ".join(
                    (r.get("snippet", "") + " " + r.get("content", ""))[:500]
                    for r in results
                ).lower()
                claim_lower = claim.lower()

                # Extract the key terms from the claim for fuzzy matching
                claim_terms = [t for t in re.split(r"\s+", claim_lower) if len(t) > 2]
                matches = sum(1 for t in claim_terms if t in combined_text)
                match_ratio = matches / len(claim_terms) if claim_terms else 0

                if match_ratio >= 0.6:
                    verified += 1
                    evidence_lines.append(f"  {claim}: VERIFIED (web confirms)")
                else:
                    # Not necessarily contradicted — might just be too niche
                    evidence_lines.append(f"  {claim}: unverified (weak web signal)")

            total = len(claims_list)
            if total == 0:
                return None

            score = 100 * verified / total if total > 0 else 50
            # Boost score if nothing was actively contradicted
            if contradicted == 0 and score < 70:
                score = max(score, 60)  # "inconclusive" floor

            passed = score >= 50
            evidence_str = "\n".join(evidence_lines)
            feedback = (
                f"Web fact-check: {verified}/{total} claims verified, "
                f"{contradicted} contradicted.\n{evidence_str}"
            )[:500]

            logger.info(
                "[WEB_FACTCHECK] Result: %d/%d verified, score=%.0f, passed=%s",
                verified, total, score, passed,
            )

            return ReviewerResult(
                reviewer="web_factcheck",
                approved=passed,
                score=score,
                feedback=feedback,
                provider="web_factcheck",
            )

        except Exception as e:
            logger.warning("[WEB_FACTCHECK] Failed (non-fatal): %s", e)
            return None

    # _review_with_gemini removed — Ollama-only policy
