"""news_short.finalize — release a rendered news Short for review.

Runs after ``media.persist``. Marks the task's ``video_short`` row standalone
(``metadata.standalone``, poindexter#1140) with the story, its source URL and
the related post's slug, and titles it with the story's headline, so
``media_distribute`` seeds a review row for it instead of waiting for a post
that will never exist. Also stores the narration where the existing Short
readers look for it (``pipeline_versions.stage_data.task_metadata``).

Raises when no Short was rendered: a run that produced nothing must fail,
not land at ``completed`` looking like a success.
"""

from __future__ import annotations

from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="news_short.finalize",
    type="atom",
    version="1.0.0",
    description=(
        "News Short: mark the rendered Short standalone (source credit, related "
        "article link) so it gets its own review; fail when nothing was rendered."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="news_short", type="dict", description="the researched story"),
        FieldSpec(name="short_summary_script", type="str", description="the narration", required=False),
        FieldSpec(name="media_assets_recorded", type="list", description="ids media.persist wrote", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
    ),
    outputs=(
        FieldSpec(name="news_short", type="dict", description="the story plus the ids of the rows marked standalone"),
    ),
    requires=("task_id", "news_short"),
    produces=("news_short",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("db_write",),
    retry=RetryPolicy(),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.news_shorts import NewsStory, mark_standalone

    task_id = str(state.get("task_id") or "")
    raw = state.get("news_short")
    story = NewsStory.from_metadata(raw)
    if not task_id or story is None:
        raise ValueError("news_short.finalize requires task_id and the researched news_short story")
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    if pool is None:
        raise ValueError("news_short.finalize: no DB pool")
    related = raw.get("related_post") if isinstance(raw, dict) else None
    marked = await mark_standalone(
        pool, task_id, story,
        related=related if isinstance(related, dict) else None,
        script=str(state.get("short_summary_script") or ""),
    )
    if not marked:
        raise RuntimeError(
            f"news_short.finalize: task {task_id} has no rendered video_short to release "
            "(the render or persist step produced nothing; see its atom_runs row)"
        )
    # The page text has done its work; keep the checkpoint small.
    kept = {k: v for k, v in (raw or {}).items() if k != "source_text"}
    return {"news_short": {**kept, "released_asset_ids": marked}}


__all__ = ["ATOM_META", "run"]
