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
from typing import Any

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


async def run_seo_llm(
    state: dict[str, Any],
    prompt_key: str,
    *,
    max_attempts: int = 2,
    backoff_s: float = 2.0,
    **prompt_vars: Any,
) -> str:
    """Render ``prompt_key`` with ``prompt_vars``, call the LLM at the SEO tier,
    and return stripped text. Retries transient failures up to ``max_attempts``;
    raises the last exception on persistent failure (the calling atom catches it
    and degrades to a programmatic fallback)."""
    prompt = get_prompt_manager().get_prompt(prompt_key, **prompt_vars)
    site_config = state.get("site_config")
    pool = getattr(state.get("database_service"), "pool", None)
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            text = await ollama_chat_text(
                prompt, site_config=site_config, pool=pool, tier=_SEO_TIER
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
    except Exception:  # noqa: BLE001 — metric is best-effort; the WARNING is the floor
        pass


def fallback_title(state: dict[str, Any]) -> str:
    canonical = (
        state.get("canonical_title")
        or state.get("title")
        or state.get("topic")
        or ""
    )
    return derive_seo_title(canonical, max_len=60)


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
    "run_seo_llm",
    "degraded",
    "fallback_title",
    "fallback_description",
    "fallback_keywords",
]
