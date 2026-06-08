"""The ``media_pipeline`` Stage-2 graph_def spec (epic poindexter#689).

Pure data — NO imports beyond typing — so the seed migration can import this
dict without pulling in LangGraph / template_runner (the migrations-smoke CI
step applies migrations without a full app boot). This mirrors
``canonical_blog_spec.py``.

Plan 8 appends a ``persist_media`` node (``media.persist``) as the new terminal
node AFTER ``media_qa``: the renders write the long/short MP4s to the OS temp
dir, which won't survive to the post-Gate-2 distribution pass, so this node
moves them into the durable media dir (``~/.poindexter/video``) and records a
task-keyed ``media_assets`` row per asset (post resolved later at distribution —
8b-2). It's the bridge between the task-keyed render graph and the post-keyed
distribution lane (#682/#678).

Plan 6 appends a ``media_qa`` node (``media.qa``) AFTER the renders: it
QA-checks the rendered videos — A/V duration sync (probe vs shot-list
``total_duration_s``), caption presence, and a gated/fail-soft frame
human-detection (policy #675) — replacing the audit-era duration+size-only
check. Best-effort: a QA failure never halts the graph.

Plan 5 inserts a single ASR pass (``media.transcribe_narration``) between
``load_scripts`` and the renders. It transcribes the podcast narration once,
producing an SRT caption track that BOTH renders burn in (#676) plus an ASR
transcript checked against the source script for fidelity (catches TTS
dropouts / truncation). One ASR pass covers both renders because they narrate
the same ``podcast_audio_path`` (redesign §6).

Plan 4 (the prior revision) added the render spine: the ``media.load_scripts``
entry node loads the persisted Stage-1 artifacts (scripts + shot-lists) from
``pipeline_versions.task_metadata``, then two render nodes
(``media.render_long_video`` → ``media.render_short_video``) turn the persisted
shot-lists into MP4s via the director-driven shot-list renderer. A re-render
therefore never re-invents prompts (the root fix for #674/#675). The
gate/distribute nodes are added in Plans 7-8.

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
        "Stage-2 media pipeline (Plan 8, #689/#675/#676/#1193/#682): load "
        "persisted Stage-1 scripts/shot-lists, run one ASR pass to caption the "
        "narration (and fidelity-check the transcript vs the script), render the "
        "16:9 long-form and 9:16 short-form videos with captions burned in, QA "
        "the rendered videos (A/V duration sync, caption presence, gated frame "
        "human-detection), then persist them to durable storage + record "
        "task-keyed media_assets rows. Distribution runs post-Gate-2 as a job."
    ),
    "entry": "load_scripts",
    "nodes": [
        {"id": "load_scripts", "atom": "media.load_scripts"},
        {"id": "transcribe_narration", "atom": "media.transcribe_narration"},
        {"id": "render_long_video", "atom": "media.render_long_video"},
        {"id": "render_short_video", "atom": "media.render_short_video"},
        {"id": "media_qa", "atom": "media.qa"},
        {"id": "persist_media", "atom": "media.persist"},
    ],
    "edges": [
        {"from": "load_scripts", "to": "transcribe_narration"},
        {"from": "transcribe_narration", "to": "render_long_video"},
        {"from": "render_long_video", "to": "render_short_video"},
        {"from": "render_short_video", "to": "media_qa"},
        {"from": "media_qa", "to": "persist_media"},
        {"from": "persist_media", "to": "END"},
    ],
}

__all__ = ["MEDIA_PIPELINE_GRAPH_DEF"]
