"""news_short.write_script — write and check a news Short's narration.

The director model writes the script from the ``news_short.script`` prompt
(``skills/content/news-short/SKILL.md``) and code checks it
(``services.news_shorts.clean_script``): spoken prose, a length sized from
``video_short_target_seconds``, an opener that makes a claim, and every number
present in the story's page. One corrective retry; a script that fails twice
fails the task, because no Short is better than a wrong one.

Produces ``short_summary_script`` (what the Short lane narrates, plans and
packages from) and an empty ``podcast_script``: the graph has no long lane,
and ``stage.generate_video_shot_list`` declares the long script as an input.
"""

from __future__ import annotations

from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="news_short.write_script",
    type="atom",
    version="1.0.0",
    description=(
        "News Short: write the 30-45 s narration from the story page and check it "
        "in code (length, spoken prose, claim opener, every number in the source)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="news_short", type="dict", description="the story with its page text (news_short.research)"),
        FieldSpec(name="site_config", type="object", description="DI seam (news_shorts_* / video_short_* settings)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
    ),
    outputs=(
        FieldSpec(name="short_summary_script", type="str", description="the checked narration"),
        FieldSpec(name="podcast_script", type="str", description="always empty: a news Short has no long lane"),
    ),
    requires=("task_id", "news_short"),
    produces=("short_summary_script", "podcast_script"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("one director-model LLM call, plus one corrective retry",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.news_shorts import NewsStory, write_script

    raw = state.get("news_short")
    researched: dict[str, Any] = raw if isinstance(raw, dict) else {}
    story = NewsStory.from_metadata(researched)
    source_text = str(researched.get("source_text") or "")
    if story is None or not source_text:
        raise ValueError("news_short.write_script: no researched story on state (run news_short.research first)")
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    task_id = str(state.get("task_id") or "") or None
    script, note = await write_script(
        pool=pool, site_config=state.get("site_config"), story=story,
        source_text=source_text, subject=str(researched.get("subject") or ""), task_id=task_id,
    )
    if not script:
        raise ValueError(f"news_short.write_script: no usable script for {story.url} ({note})")
    return {"short_summary_script": script, "podcast_script": ""}


__all__ = ["ATOM_META", "run"]
