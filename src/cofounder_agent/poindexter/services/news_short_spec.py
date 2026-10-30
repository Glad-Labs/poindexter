"""news_short pipeline as a static graph_def.

Pure data — NO imports beyond typing — so the seed migration can import just
this dict without pulling in LangGraph / template_runner (migrations-smoke
runs in a light env). Mirrors ``services/image_rebuild_spec.py``.

A YouTube Short made from one news story, with no blog post behind it
(``services/news_shorts.py``; ``docs/architecture/news-shorts.md``). The task
is queued by ``EnqueueNewsShortsJob`` (or ``poindexter youtube news-short``)
with the story on its metadata as ``news_short``, and the Prefect content flow
claims it like any other task.

    research (news_short.research)           — fetch the story's page (the
                                               whole corpus) + the related post;
                                               fail on an unreadable page
      → write_script (news_short.write_script) — 30-45 s narration, checked in
                                               code: every number in the page
      → shot_list (stage.generate_video_shot_list, short_only)
                                             — the 9:16 director only
      → review_shot_list (stage.review_video_shot_list)
      → render_narration → transcribe_narration → qa_audio
                                             — the Short lane's audio steps;
                                               the long lane has no script and
                                               no-ops
      → ambient_bed (media.generate_ambient_bed)
                                             — the instrumental bed mixed under
                                               the narration, as a blog Short
                                               gets; best-effort
      → render_short_video → package_short   — the 9:16 render, then its
                                               YouTube title + description lead
      → media_qa → persist_media             — video QA, durable video_short row
      → finalize (news_short.finalize)       — mark the row standalone (source,
                                               related article) so it gets its
                                               own review; fail when nothing
                                               rendered
      → done (atoms.set_task_status)         — the task lands at 'completed':
                                               the Short is reviewed on its own
                                               (media_approvals), as a
                                               creative-studio video is

The steps after write_script are the same atoms the blog's Short runs, so a
fix to any of them reaches both.
"""

from __future__ import annotations

from typing import Any

NEWS_SHORT_GRAPH_DEF: dict[str, Any] = {
    "name": "news_short",
    "description": (
        "A YouTube Short from one news story, no blog post: read the story's page, "
        "write a checked 30-45 s script, render it with the Short lane's own "
        "steps, then release it for its own review."
    ),
    "entry": "research",
    "nodes": [
        {"id": "research", "atom": "news_short.research"},
        {"id": "write_script", "atom": "news_short.write_script"},
        {
            "id": "shot_list",
            "atom": "stage.generate_video_shot_list",
            # Plans the 9:16 lane only; the long director would have no
            # narration to align to.
            "config": {"short_only": True},
        },
        {"id": "review_shot_list", "atom": "stage.review_video_shot_list"},
        {"id": "render_narration", "atom": "media.render_narration"},
        {"id": "transcribe_narration", "atom": "media.transcribe_narration"},
        {"id": "qa_audio", "atom": "qa.audio"},
        {"id": "ambient_bed", "atom": "media.generate_ambient_bed"},
        {"id": "render_short_video", "atom": "media.render_short_video"},
        {"id": "package_short", "atom": "media.package_short"},
        {"id": "media_qa", "atom": "media.qa"},
        {"id": "persist_media", "atom": "media.persist"},
        {"id": "finalize", "atom": "news_short.finalize"},
        {
            "id": "done",
            "atom": "atoms.set_task_status",
            # 'completed' is terminal and keeps the task out of the claim,
            # stale-sweep and article-approval queries; the Short itself waits
            # in media_approvals.
            "config": {"target_status": "completed", "percentage": 100},
        },
    ],
    "edges": [
        {"from": "research", "to": "write_script"},
        {"from": "write_script", "to": "shot_list"},
        {"from": "shot_list", "to": "review_shot_list"},
        {"from": "review_shot_list", "to": "render_narration"},
        {"from": "render_narration", "to": "transcribe_narration"},
        {"from": "transcribe_narration", "to": "qa_audio"},
        {"from": "qa_audio", "to": "ambient_bed"},
        {"from": "ambient_bed", "to": "render_short_video"},
        {"from": "render_short_video", "to": "package_short"},
        {"from": "package_short", "to": "media_qa"},
        {"from": "media_qa", "to": "persist_media"},
        {"from": "persist_media", "to": "finalize"},
        {"from": "finalize", "to": "done"},
        {"from": "done", "to": "END"},
    ],
}

__all__ = ["NEWS_SHORT_GRAPH_DEF"]
