"""Writer self-review pass — catch cross-section contradictions before QA.

Lifted from content_router_service.py during Phase E2 (issue #170's
prevention layer). A second Ollama call asks the model to review its own
draft for internal contradictions; if any are found, a follow-up call
asks the model to fix only those specific issues.

Gated by app_settings ``enable_writer_self_review`` (default ``false``).
When disabled, returns ``(draft, {"enabled": False, ...})`` unchanged.

The WriterSelfReviewStage + the inline self-review inside
GenerateContentStage both call this helper.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


async def self_review_and_revise(
    draft: str, title: str, topic: str,
) -> tuple[str, dict]:
    """Ask the writer model to catch + fix cross-section contradictions.

    Returns ``(possibly_revised_draft, stats_dict)`` where stats includes:

    - ``enabled`` (bool) — False when the feature flag is off; True otherwise.
    - ``contradictions_found`` (int) — count the detector returned.
    - ``revised`` (bool) — True only when we accepted the revision.
    """
    from plugins.registry import get_all_llm_providers
    import services.site_config as _scm
    site_config = _scm.site_config

    stats: dict = {"enabled": False, "contradictions_found": 0, "revised": False}

    enabled = (
        str(site_config.get("enable_writer_self_review", "false")).lower() == "true"
    )
    if not enabled:
        return draft, stats

    stats["enabled"] = True
    if not draft or len(draft) < 500:
        return draft, stats  # too short for meaningful cross-section review

    # Non-thinking default. Thinking models burn tokens on <think> wrappers
    # and frequently return short/empty outputs on long review prompts.
    review_model = str(
        site_config.get("writer_self_review_model") or "gemma3:27b"
    ).removeprefix("ollama/")

    review_prompt = (
        "You are reviewing your own draft for internal contradictions.\n\n"
        f"TITLE: {title}\n"
        f"TOPIC: {topic}\n\n"
        f"DRAFT:\n{draft}\n\n"
        "Read every section. Identify any claim in one section that contradicts "
        "a claim, code example, or recommendation in another section. "
        "Ignore stylistic variation; focus on factual or logical conflicts.\n\n"
        "If you find contradictions, output a numbered list of specific corrections "
        "needed (one per line, format: 'SECTION X conflicts with SECTION Y: <details>'). "
        "If you find none, reply with exactly: PASS"
    )

    # v2.3: Provider Protocol instead of concrete OllamaClient. The
    # timeout knob survives via the new ``timeout_s`` kwarg (v2.1).
    timeout_s = site_config.get_int(
        "content_router_contradiction_timeout_seconds", 120,
    )
    providers = {p.name: p for p in get_all_llm_providers()}
    provider = providers.get("ollama_native")
    if provider is None:
        logger.warning(
            "[SELF_REVIEW] ollama_native provider not registered; skipping",
        )
        return draft, stats

    try:
        result = await provider.complete(
            messages=[{"role": "user", "content": review_prompt}],
            model=review_model,
            temperature=0.2,
            max_tokens=site_config.get_int(
                "content_router_contradiction_review_max_tokens", 1500,
            ),
            timeout_s=timeout_s,
        )
        review_text = (result.text or "").strip()

        if not review_text or review_text.upper().startswith("PASS"):
            return draft, stats

        contradictions = [
            ln for ln in review_text.splitlines()
            if re.match(r"^\s*\d+[\.\)]\s+", ln)
        ]
        stats["contradictions_found"] = len(contradictions)
        if not contradictions:
            return draft, stats

        revise_prompt = (
            "Here is your draft. Fix these specific contradictions and nothing else:\n\n"
            f"CONTRADICTIONS TO FIX:\n{review_text}\n\n"
            f"ORIGINAL DRAFT:\n{draft}\n\n"
            "Output only the revised draft. Keep the structure, length, and tone "
            "identical. Only change what's needed to resolve the contradictions."
        )
        revised = await provider.complete(
            messages=[{"role": "user", "content": revise_prompt}],
            model=review_model,
            temperature=0.3,
            max_tokens=site_config.get_int(
                "content_router_contradiction_revise_max_tokens", 8000,
            ),
            timeout_s=timeout_s,
        )
        revised_text = (revised.text or "").strip()

        # Guard: revision must be a reasonable length — thinking-model fallthroughs
        # can return near-empty strings even on long input.
        if len(revised_text) >= int(0.7 * len(draft)):
            stats["revised"] = True
            logger.info(
                "[SELF_REVIEW] Revised draft: %d contradictions found, %d chars in/%d out",
                len(contradictions), len(draft), len(revised_text),
            )
            return revised_text, stats

        logger.warning(
            "[SELF_REVIEW] Revision too short (%d chars), keeping original (%d chars)",
            len(revised_text), len(draft),
        )
    except Exception as e:
        logger.warning("[SELF_REVIEW] Self-review failed (non-fatal): %s", e)

    return draft, stats
