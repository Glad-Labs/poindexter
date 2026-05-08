"""Canonical title generation + web-originality check.

Lifted from content_router_service.py during Phase E2. Two functions the
generate_content stage uses back-to-back:

- :func:`generate_canonical_title` — asks the writer LLM for an SEO-optimized
  title that avoids a list of recent/existing titles. Handles the
  thinking-model failure mode (``<think>…</think>`` wrappers, list markers,
  deliberation markers) via :func:`sanitize_generated_title`.

- :func:`check_title_originality` — web-searches the proposed title to see
  if it collides with existing published content. If it does, the stage
  regenerates with a stronger avoidance prompt. Threshold + enable flag
  come from ``app_settings`` (``qa_title_similarity_threshold`` +
  ``qa_title_originality_enabled``).
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# Phrases that indicate the LLM deliberated instead of just giving the title.
# If these appear anywhere in the output, we treat the response as unclean
# and return None so the caller falls back to a safer source (topic / seo_title).
TITLE_DELIBERATION_MARKERS: tuple[str, ...] = (
    "let's go with",
    "let me choose",
    "i'll pick",
    "i'd pick",
    "the most unique",
    "the best option",
    "here are",
    "here's a",
    "option 1",
    "option 2",
    "option a",
    "option b",
    "title 1:",
    "title 2:",
)


def sanitize_generated_title(raw: str) -> str | None:
    """Clean an LLM title response, or return None if it's unsalvageable.

    Real-world failure mode (#198 follow-up): thinking-models sometimes
    return their reasoning trace instead of a clean title, e.g.:

        "*   Let's go with the **Question**. It is the most unique structure..."

    Steps:

    1. Strip ``<think>…</think>`` blocks (some models emit them literally).
    2. Walk from the bottom of the output for the first line that looks
       like an actual title (not bullet / deliberation / empty).
    3. Strip list markers (``*``, ``-``, ``+``, ``1.``), bold wrappers,
       leading ``#`` headers, and surrounding quotes.
    4. Reject anything that still contains deliberation markers.
    5. Reject empty, too-short (<5 chars), or too-long (>120) results.
    6. Final length trim — SEO caps around 60, hard cap at 100.
    """
    if not raw:
        return None

    text = raw.strip()
    text = re.sub(
        r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidate: str | None = None
    for line in reversed(lines):
        stripped = re.sub(r"^[\s\*\-\+\u2022]+|^\d+[\.\)]\s*", "", line).strip()
        stripped = re.sub(r"^#+\s+", "", stripped)
        stripped = stripped.strip('"').strip("'").strip()
        stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        if not stripped or len(stripped) < 5 or len(stripped) > 200:
            continue
        lower = stripped.lower()
        if any(marker in lower for marker in TITLE_DELIBERATION_MARKERS):
            continue
        candidate = stripped
        break

    if not candidate:
        return None

    if len(candidate) > 100:
        candidate = candidate[:97].rstrip() + "..."
    return candidate


async def generate_canonical_title(
    topic: str,
    primary_keyword: str,
    content_excerpt: str,
    existing_titles: str = "",
) -> str | None:
    """Generate an SEO-optimized title via LLM, avoiding similarity to existing titles."""
    try:
        from plugins.registry import get_all_llm_providers
        from services.prompt_manager import get_prompt_manager
        import services.site_config as _scm
        site_config = _scm.site_config

        pm = get_prompt_manager()
        providers = {p.name: p for p in get_all_llm_providers()}
        provider = providers.get("ollama_native")
        if provider is None:
            logger.warning("[TITLE_GEN] ollama_native provider not registered; skipping")
            return None

        prompt = pm.get_prompt(
            "seo.generate_title",
            content=content_excerpt,
            primary_keyword=primary_keyword or topic,
        )
        if existing_titles:
            prompt += (
                f"\n\n⚠️ AVOID SIMILARITY to these recent titles:\n{existing_titles}\n\n"
                "Your title must be DISTINCTLY DIFFERENT in structure and wording."
            )

        model = (
            site_config.get("pipeline_writer_model") or "gemma3:27b"
        ).removeprefix("ollama/")
        result = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.7,
            max_tokens=site_config.get_int(
                "content_router_seo_title_max_tokens", 4000,
            ),
        )

        if result and result.text:
            title = sanitize_generated_title(result.text)
            if title:
                logger.debug("Generated title: %s", title)
                return title
            logger.warning(
                "[TITLE_GEN] Sanitizer rejected LLM output as unclean: %r",
                result.text[:100],
            )
        return None

    except Exception as e:
        logger.warning("Error generating canonical title: %s", e, exc_info=True)
        return None


async def check_title_originality(title: str) -> dict:
    """Web-search the title; return similarity summary.

    Return shape::

        {
            "is_original": bool,
            "similar_titles": list[str],
            "max_similarity": float,  # 0.0..1.0
            # GH-87 additions:
            "external_verbatim_match": bool,
            "external_near_match": bool,
            "external_penalty": int,     # points to subtract from QA score
            "external_matches": list[dict],  # [{"title": str, "url": str}, ...]
            "external_fail_open": bool,  # true if the external check couldn't run
        }

    Threshold defaults to 0.6 and comes from
    ``qa_title_similarity_threshold``. Set
    ``qa_title_originality_enabled=false`` to bypass the check (returns
    "is_original": True with empty similar_titles).

    GH-87: also runs :func:`services.title_originality_external.check_external_title_duplicates`
    which hits the DuckDuckGo HTML endpoint directly for the exact quoted
    title. Verbatim matches surface as ``external_verbatim_match=True``
    with a non-zero ``external_penalty`` the QA stage can subtract from
    the final score; near-matches set ``external_near_match=True`` so
    the approver sees a warning without the post being rejected. The
    external check fails OPEN — if DDG is rate-limiting us or the
    network is down, ``external_fail_open=True`` and the pipeline
    continues as if nothing matched.
    """
    result: dict = {
        "is_original": True,
        "similar_titles": [],
        "max_similarity": 0.0,
        "external_verbatim_match": False,
        "external_near_match": False,
        "external_penalty": 0,
        "external_matches": [],
        "external_fail_open": False,
    }

    try:
        import services.site_config as _scm
        site_config = _scm.site_config
        threshold = site_config.get_float("qa_title_similarity_threshold", 0.6)
        enabled = site_config.get_bool("qa_title_originality_enabled", True)
        if not enabled:
            return result
    except Exception:
        threshold = 0.6

    try:
        from services.web_research import WebResearcher
        researcher = WebResearcher()
        search_results = await researcher.search_simple(
            f'"{title}"', num_results=8,
        )
        if not search_results:
            # Broader search without quotes.
            search_results = await researcher.search_simple(title, num_results=8)

        title_lower = title.lower().strip()
        for r in search_results:
            ext_title = (r.get("title") or "").lower().strip()
            if not ext_title:
                continue
            sim = SequenceMatcher(None, title_lower, ext_title).ratio()
            if sim > result["max_similarity"]:
                result["max_similarity"] = sim
            if sim >= threshold:
                result["similar_titles"].append(r.get("title", ""))

        result["is_original"] = len(result["similar_titles"]) == 0
        if not result["is_original"]:
            logger.warning(
                "[TITLE] Originality check FAILED (%.0f%% similar): '%s' vs '%s'",
                result["max_similarity"] * 100,
                title,
                result["similar_titles"][0] if result["similar_titles"] else "?",
            )
        else:
            logger.info(
                "[TITLE] Originality check passed (max %.0f%% similarity)",
                result["max_similarity"] * 100,
            )

    except Exception as e:
        logger.warning("[TITLE] Originality check skipped (non-fatal): %s", e)

    # GH-87: external-article duplicate check. Isolated from the block
    # above so a WebResearcher failure doesn't short-circuit the DDG HTML
    # path (and vice versa).
    try:
        from services.title_originality_external import (
            check_external_title_duplicates,
        )
        ext = await check_external_title_duplicates(title)
        result["external_verbatim_match"] = ext.verbatim_match
        result["external_near_match"] = ext.near_match
        result["external_penalty"] = ext.penalty
        result["external_matches"] = ext.matches
        result["external_fail_open"] = ext.fail_open
        # Verbatim external match should flip ``is_original`` so the
        # regenerate-title path in the generate_content stage kicks in
        # the same way it does for our-own-corpus duplicates.
        if ext.verbatim_match:
            result["is_original"] = False
            if ext.matches:
                # Surface the external title so the avoid-list in the
                # regeneration prompt includes it.
                result["similar_titles"].extend(
                    m.get("title", "") for m in ext.matches if m.get("title")
                )
    except Exception as e:
        logger.warning("[TITLE] External originality check skipped (non-fatal): %s", e)

    return result
