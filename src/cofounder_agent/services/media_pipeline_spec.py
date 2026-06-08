"""The ``media_pipeline`` Stage-2 graph_def spec (epic poindexter#689).

Pure data — NO imports beyond typing — so the seed migration can import this
dict without pulling in LangGraph / template_runner (the migrations-smoke CI
step applies migrations without a full app boot). This mirrors
``canonical_blog_spec.py``.

Plan 4 extends the Plan-2 spine: the ``media.load_scripts`` entry node loads
the persisted Stage-1 artifacts (scripts + shot-lists) from
``pipeline_versions.task_metadata``, then two render nodes
(``media.render_long_video`` → ``media.render_short_video``) turn the persisted
shot-lists into MP4s via the director-driven shot-list renderer. A re-render
therefore never re-invents prompts (the root fix for #674/#675). The
QA/gate/distribute nodes are added in Plans 5-8.

Seeded ``active=true`` but **dormant**: nothing calls
``TemplateRunner.run("media_pipeline", …)`` yet (the Gate-1 → Stage-2 trigger
lands in Plan 7), so seeding the row is a behavior no-op in prod — the same
way ``canonical_blog`` was seeded before its cutover (#355).
"""

from __future__ import annotations

from typing import Any

MEDIA_PIPELINE_GRAPH_DEF: dict[str, Any] = {
    "name": "media_pipeline",
    "description": (
        "Stage-2 media pipeline (Plan 4, #689/#675): load persisted Stage-1 "
        "scripts/shot-lists, then render the 16:9 long-form and 9:16 short-form "
        "videos. QA/gate/distribute nodes added in Plans 5-8."
    ),
    "entry": "load_scripts",
    "nodes": [
        {"id": "load_scripts", "atom": "media.load_scripts"},
        {"id": "render_long_video", "atom": "media.render_long_video"},
        {"id": "render_short_video", "atom": "media.render_short_video"},
    ],
    "edges": [
        {"from": "load_scripts", "to": "render_long_video"},
        {"from": "render_long_video", "to": "render_short_video"},
        {"from": "render_short_video", "to": "END"},
    ],
}

__all__ = ["MEDIA_PIPELINE_GRAPH_DEF"]
