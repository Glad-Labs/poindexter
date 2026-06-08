"""The ``media_pipeline`` Stage-2 graph_def spec (epic poindexter#689).

Pure data — NO imports beyond typing — so the seed migration can import this
dict without pulling in LangGraph / template_runner (the migrations-smoke CI
step applies migrations without a full app boot). This mirrors
``canonical_blog_spec.py``.

This is the **spine** (Plan 2): a single ``media.load_scripts`` entry node that
loads the persisted Stage-1 artifacts (scripts + shot-lists) from
``pipeline_versions.task_metadata`` so the downstream render/QA/gate/distribute
nodes — added in Plans 3-8 — have them in graph state. A re-render therefore
never re-invents prompts (the root fix for #674/#675).

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
        "Stage-2 media pipeline (spine, #689): load persisted Stage-1 "
        "scripts/shot-lists. Render/QA/gate/distribute nodes added in "
        "Plans 3-8."
    ),
    "entry": "load_scripts",
    "nodes": [
        {"id": "load_scripts", "atom": "media.load_scripts"},
    ],
    "edges": [
        {"from": "load_scripts", "to": "END"},
    ],
}

__all__ = ["MEDIA_PIPELINE_GRAPH_DEF"]
