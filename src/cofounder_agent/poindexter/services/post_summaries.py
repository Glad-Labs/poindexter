"""An answer-first "In brief" summary for each published post.

AI answer engines (ChatGPT, Perplexity, Google's AI answers) quote passages
that state a finding plainly; the posts open with a narrative hook ("If you
want the most advanced lithography equipment on the planet, you don't just
sign a check..."), and the meta description is written to sell a click
("Learn how..."). So each published main-feed post gets two or three
sentences that say what it found, stored in ``posts.metadata->>'summary'`` and
shown at the top of the post, in its JSON-LD ``abstract``, at the head of its
Markdown version and as its line in /llms.txt (2026-10-18).

It runs after publishing, as a job (``GeneratePostSummariesJob``), rather than
as a graph node: a new output on a pipeline atom would change its contract and
force a graph_def reseed for both live graphs, and a job also backfills the
posts already published.

Code checks every summary before it is kept: within
``post_summary_max_chars``, not about itself ("This article..."), and every
number in it appears in the post. A summary that fails gets one retry with the
reason; a second failure leaves the post without one and records when, so it
is not retried for ``post_summary_retry_days``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_SELF_REFERENCE_RE = re.compile(r"^\s*(?:this|the)\s+(?:article|post|piece|guide)\b|^\s*in this (?:article|post)", re.I)


def _int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(site_config.get(key, default))
    except (TypeError, ValueError):
        return default


def check_summary(summary: str, content: str, *, max_chars: int) -> list[str]:
    """Why ``summary`` cannot be kept for ``content``; empty when it can."""
    text = (summary or "").strip()
    problems: list[str] = []
    if not text:
        return ["it is empty"]
    if len(text) > max_chars:
        problems.append(f"it is {len(text)} characters; keep it under {max_chars}")
    if _SELF_REFERENCE_RE.search(text):
        problems.append("it talks about the article instead of stating what it found")
    body_numbers = {n.replace(",", "") for n in _NUMBER_RE.findall(content or "")}
    invented = [n for n in _NUMBER_RE.findall(text) if n.replace(",", "") not in body_numbers]
    if invented:
        problems.append(f"it has numbers the post does not: {', '.join(invented[:5])}")
    return problems


async def summarize_post(*, title: str, content: str, site_config: Any, pool: Any) -> str | None:
    """A checked summary of the post, or ``None`` after two failed attempts."""
    from poindexter.services.llm_text import ollama_chat_text, resolve_local_writer_model
    from poindexter.services.prompt_manager import get_prompt_manager

    max_chars = _int(site_config, "post_summary_max_chars", 420)
    model = str(site_config.get("post_summary_model", "") or "").strip() \
        or resolve_local_writer_model(site_config=site_config)
    feedback = ""
    for attempt in (1, 2):
        prompt = get_prompt_manager().get_prompt(
            "seo.post_summary", title=title, content=content, max_chars=max_chars, feedback=feedback,
        )
        raw = await ollama_chat_text(
            prompt, model=model, site_config=site_config, pool=pool, phase="post_summary",
            timeout_setting="post_summary_timeout_seconds", timeout_default=180.0, think=False,
            max_tokens=_int(site_config, "post_summary_max_tokens", 4000),
        )
        summary = " ".join((raw or "").split()).strip().strip('"')
        problems = check_summary(summary, content, max_chars=max_chars)
        if not problems:
            return summary
        logger.info("[POST_SUMMARY] attempt %d for %r rejected: %s", attempt, title[:60], "; ".join(problems))
        feedback = (
            "\nYour previous summary could not be used because "
            + "; ".join(problems)
            + ". Write a new one that fixes this.\n"
        )
    return None


async def fill_missing_summaries(pool: Any, *, site_config: Any, limit: int) -> dict[str, Any]:
    """Summarize up to ``limit`` published main-feed posts that have none.

    Newest first; dev diary entries are skipped (noindexed status logs).
    Returns ``{"written": [slugs], "failed": [slugs]}``.
    """
    retry_days = _int(site_config, "post_summary_retry_days", 7)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id, p.slug, p.title, p.content
              FROM posts p
              LEFT JOIN pipeline_tasks t ON t.task_id::text = p.metadata->>'pipeline_task_id'
             WHERE p.status = 'published'
               AND COALESCE(t.niche_slug, '') <> 'dev_diary'
               AND COALESCE(p.metadata->>'summary', '') = ''
               AND (p.metadata->>'summary_failed_at' IS NULL
                    OR (p.metadata->>'summary_failed_at')::timestamptz < now() - make_interval(days => $2))
             ORDER BY p.published_at DESC NULLS LAST
             LIMIT $1
            """,
            limit, retry_days,
        )
    written: list[str] = []
    failed: list[str] = []
    for row in rows:
        summary = await summarize_post(
            title=row["title"] or "", content=row["content"] or "", site_config=site_config, pool=pool,
        )
        async with pool.acquire() as conn:
            if summary:
                await conn.execute(
                    "UPDATE posts SET metadata = (COALESCE(metadata, '{}'::jsonb) - 'summary_failed_at')"
                    " || jsonb_build_object('summary', $2::text), updated_at = NOW() WHERE id = $1",
                    row["id"], summary,
                )
                written.append(row["slug"])
            else:
                await conn.execute(
                    "UPDATE posts SET metadata = COALESCE(metadata, '{}'::jsonb)"
                    " || jsonb_build_object('summary_failed_at', now()::text) WHERE id = $1",
                    row["id"],
                )
                failed.append(row["slug"])
    return {"written": written, "failed": failed}


__all__ = ["check_summary", "fill_missing_summaries", "summarize_post"]
