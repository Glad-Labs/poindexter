"""Shared helpers for the SEO atoms (seo.generate_title / .generate_description
/ .extract_keywords). Underscore-prefixed so ``atom_registry`` skips it.

Owns the one LLM-call path (with retry — the TemplateRunner does NOT enforce
``ATOM_META.retry``, so retry lives here), the programmatic fallbacks reused for
graceful degradation, and the degradation logger. Issue Glad-Labs/poindexter#362
(umbrella #355).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from modules.content.atoms._pool import resolve_pool
from services.llm_text import ollama_chat_text
from services.prompt_manager import get_prompt_manager
from utils.text_utils import extract_keywords_from_text
from utils.title_utils import derive_seo_title

logger = logging.getLogger(__name__)

# Cost tier handed to the dispatcher. ``capability_tier`` in ATOM_META is the
# semantic slug ("cheap_critic"); this is the concrete cost tier the dispatcher
# resolves a provider/model from. ``budget`` = cheap instruction-follower.
_SEO_TIER = "budget"
_EXCERPT_CHARS = 1500


def content_excerpt(content: str, limit: int = _EXCERPT_CHARS) -> str:
    return (content or "").strip()[:limit]


def clean_oneline(text: str) -> str:
    """Strip surrounding quotes/backticks and collapse whitespace to one line."""
    t = (text or "").strip()
    for q in ('"', "'", "`"):
        t = t.strip(q)
    return " ".join(t.split())


def clamp_words(text: str, limit: int) -> str:
    """Trim to <= limit chars at a word boundary, dropping trailing punctuation."""
    t = clean_oneline(text)
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0] or t[:limit]
    return cut.rstrip(",.;:- ")


# A word-boundary clip can leave a dangling connective ("... Micro-SaaS &").
# We only strip these when the title was actually truncated — a short title that
# legitimately ends in a preposition ("What to Look For") must be left alone.
_TRAILING_CONNECTIVE = re.compile(
    r"\s+(?:and|or|the|a|an|to|for|with|of|in|on|at|by|vs|&)$", re.IGNORECASE
)
_TRAILING_SYMBOLS = " &/|:;,-–—"


def clean_title(text: str, limit: int) -> str:
    """SEO-title hygiene — the title twin of :func:`clamp_words`.

    ``clean_oneline`` strips only *surrounding* quotes and ``derive_seo_title``
    (unlike ``clamp_words``) keeps trailing punctuation, so an LLM title can
    arrive with an embedded quote or a 60-char clip ending on a dangling ``&``.
    This: (1) removes embedded double-quote artifacts (apostrophes are kept),
    (2) emoji-strips + truncates at a word boundary via ``derive_seo_title``,
    and (3) only when the input had to be truncated, drops a trailing dangling
    connective; trailing stray symbols are dropped either way.
    """
    t = clean_oneline(text)
    for q in ('"', "“", "”"):  # straight + curly DOUBLE quotes only
        t = t.replace(q, "")
    t = " ".join(t.split())
    was_truncated = len(t) > limit
    t = derive_seo_title(t, max_len=limit)  # emoji strip + word-boundary truncate
    if was_truncated:
        prev = None
        while prev != t:
            prev = t
            t = _TRAILING_CONNECTIVE.sub("", t)
            t = t.rstrip(_TRAILING_SYMBOLS)
    else:
        t = t.rstrip(_TRAILING_SYMBOLS)
    return t.strip()


async def run_seo_llm(
    state: dict[str, Any],
    prompt_key: str,
    *,
    max_attempts: int = 2,
    backoff_s: float = 2.0,
    prompt_suffix: str = "",
    **prompt_vars: Any,
) -> str:
    """Render ``prompt_key`` with ``prompt_vars``, call the LLM at the SEO tier,
    and return stripped text. Retries transient failures up to ``max_attempts``;
    raises the last exception on persistent failure (the calling atom catches it
    and degrades to a programmatic fallback).

    ``prompt_suffix`` is appended verbatim after the rendered template — the
    seam corrective-retry callers use to add a "your previous answer did X
    wrong" instruction without a second prompt key.
    """
    prompt = get_prompt_manager().get_prompt(prompt_key, **prompt_vars)
    if prompt_suffix:
        prompt += prompt_suffix
    site_config = state.get("site_config")
    pool = resolve_pool(state, atom="seo")
    # Per-step pin: ``pipeline_seo_model`` when set; EMPTY = follow the writer
    # (``pipeline_writer_model``), the pre-pin behavior. SEO metadata is
    # structured, formulaic copy a budget local model handles — the pin exists
    # so a cloud writer canary doesn't silently bill this step (the 2026-07-07
    # Sonnet canary put every ``phase="seo"`` call on Anthropic).
    model: str | None = None
    if site_config is not None:
        model = (site_config.get("pipeline_seo_model", "") or "").strip() or None
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            text = await ollama_chat_text(
                prompt, model=model, site_config=site_config, pool=pool,
                tier=_SEO_TIER, task_id=state.get("task_id"), phase="seo",
            )
            return (text or "").strip()
        except Exception as exc:  # noqa: BLE001 — retry any transient transport error
            last_exc = exc
            if attempt < max_attempts:
                await asyncio.sleep(backoff_s)
    assert last_exc is not None
    raise last_exc


def degraded(field: str, exc: Exception) -> None:
    """Log the LLM→programmatic degradation. The WARNING (captured by Loki) is
    the floor; a best-effort metric is emitted if the exporter exposes one."""
    logger.warning("[seo.%s] LLM failed, degraded to programmatic: %s", field, exc)
    try:
        from services.metrics_exporter import increment_seo_degraded  # type: ignore

        increment_seo_degraded(field)
    except Exception:  # noqa: BLE001 - silent-ok: the metric is a duplicate
        # signal, not the primary one -- the caller has already logged a
        # WARNING for this same condition, so the floor is preserved and a
        # second line here would only double the noise. — metric is best-effort; the WARNING is the floor
        pass


def fallback_title(state: dict[str, Any]) -> str:
    canonical = (
        state.get("canonical_title")
        or state.get("title")
        or state.get("topic")
        or ""
    )
    return derive_seo_title(canonical, max_len=60)


def resolve_primary_keyword(state: dict[str, Any]) -> str:
    """The keyword the SEO/title prompts are told to lead with.

    ``tags[0]`` when the task carries tags; else the top frequency keyword
    extracted from the draft itself; else the raw topic (legacy last resort).

    The middle rung is the 2026-07-24 fix: with no tags, the raw topic used to
    become the "lead with primary keyword '...'" instruction verbatim — and a
    directive-shaped topic ("Expand coverage of the Insights category — only
    Insights (3)") was then dutifully echoed as the seo_title. A keyword
    extracted from the article grounds the instruction in what the article
    actually says.
    """
    tags = state.get("tags") or []
    if tags and str(tags[0]).strip():
        return str(tags[0]).strip()
    content_keywords = extract_keywords_from_text(state.get("content") or "", count=1)
    if content_keywords:
        return content_keywords[0]
    return str(state.get("topic") or "").strip()


# Normalization for the topic-echo compare: drop everything but letters and
# digits so punctuation/case/whitespace differences never mask an echo.
_ECHO_NORM_RE = re.compile(r"[^a-z0-9]+")

# A truncated echo shorter than this many normalized chars is too little
# signal to call an echo — a short legit title could prefix-match a longer
# topic by coincidence ("Insights" vs "Insights category: ...").
_ECHO_MIN_PREFIX_CHARS = 20


def _echo_norm(text: str) -> str:
    return _ECHO_NORM_RE.sub("", (text or "").casefold())


def is_topic_echo(candidate: str, topic: str) -> bool:
    """True when ``candidate`` is the topic string parroted back (optionally
    clipped by the 60-char SEO truncation) rather than a written title.

    An seo_title that merely *discusses* the topic is NOT an echo — only a
    verbatim (normalized) copy, or a copy truncated at the SEO length cap,
    counts. Pure function — no LLM calls, no I/O.
    """
    norm_c = _echo_norm(candidate)
    norm_t = _echo_norm(topic)
    if not norm_c or not norm_t:
        return False
    if norm_c == norm_t:
        return True
    # Clipped echo: derive_seo_title truncates at 60 chars, so a long topic
    # arrives as its own prefix. Require a meaningful prefix length so a
    # short generic title can't false-positive against a long topic.
    return len(norm_c) >= _ECHO_MIN_PREFIX_CHARS and norm_t.startswith(norm_c)


def fallback_description(state: dict[str, Any]) -> str:
    content = state.get("content") or ""
    topic = state.get("topic") or ""
    paragraphs = content.split("\n\n")
    excerpt = next(
        (p for p in paragraphs if p.strip() and not p.startswith("#")),
        content[:200],
    )
    return (excerpt.strip() or topic)[:160]


def fallback_keywords(state: dict[str, Any], count: int = 5) -> list[str]:
    return extract_keywords_from_text(state.get("content") or "", count=count)


__all__ = [
    "content_excerpt",
    "clean_oneline",
    "clamp_words",
    "clean_title",
    "run_seo_llm",
    "degraded",
    "fallback_title",
    "fallback_description",
    "fallback_keywords",
    "resolve_primary_keyword",
    "is_topic_echo",
]
