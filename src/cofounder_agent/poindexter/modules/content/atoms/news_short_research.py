"""news_short.research — read a news story's own page for its Short.

Entry node of the ``news_short`` graph (``services/news_short_spec.py``). The
story arrives on the task's metadata as ``news_short`` (a topic_pool row:
title, url, summary, source), queued by ``services.news_shorts.enqueue_news_short``.
This node fetches the story's page, which becomes the run's ``content`` (the
director reads it, and the script's numbers are checked against it), and finds
the published post the upload will link to.

Raises when the page cannot be read, so the task fails with the reason rather
than writing a Short from a headline. The work lives in
``services.news_shorts.research``; this atom adapts graph state to it.
"""

from __future__ import annotations

from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="news_short.research",
    type="atom",
    version="1.0.0",
    description=(
        "News Short entry: fetch the story's own page (the script's whole corpus) "
        "and the most related published post; fail when the page is unreadable."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="news_short", type="dict", description="the story (topic_pool row) from the task's metadata"),
        FieldSpec(name="niche_slug", type="str", description="the task's niche ('' when none): its topic subject frames the script", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (news_shorts_* settings)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
    ),
    outputs=(
        FieldSpec(name="news_short", type="dict", description="the story plus its page text and related post"),
        FieldSpec(name="title", type="str", description="the story's headline (the director's title)"),
        FieldSpec(name="content", type="str", description="the story page's text (the director's article)"),
    ),
    requires=("task_id", "news_short"),
    produces=("news_short", "title", "content"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("network",),
    retry=RetryPolicy(max_attempts=2, backoff_s=10.0),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.news_shorts import NewsStory, research

    story = NewsStory.from_metadata(state.get("news_short"))
    if story is None:
        raise ValueError(
            "news_short.research: the task carries no news_short story "
            "(title + url); queue it with services.news_shorts.enqueue_news_short"
        )
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    found = await research(
        pool, state.get("site_config"), story, niche=str(state.get("niche_slug") or ""),
    )
    return {
        "news_short": {**story.as_metadata(), **found},
        "title": story.title,
        "content": found["source_text"],
    }


__all__ = ["ATOM_META", "run"]
