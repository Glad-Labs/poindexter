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

Phase 2 (#1193) inserts a ``qa_audio`` node (``qa.audio``) between
``transcribe_narration`` and ``render_long_video``. It runs three deterministic
ffprobe/ffmpeg checks on the raw narration audio BEFORE the GPU-heavy render
step: silence detection (TTS dropout), volume-level check (clipping / too
quiet), and duration-vs-script-estimate consistency. All checks are fail-soft
(no AI model required); a QA failure never halts the graph.

Plan 6 appends a ``media_qa`` node (``media.qa``) AFTER the renders: it
QA-checks the rendered videos — A/V duration sync (probe vs shot-list
``total_duration_s``), caption presence, and a gated/fail-soft frame
human-detection (policy #675) — replacing the audit-era duration+size-only
check. Best-effort: a QA failure never halts the graph.

Per-media narration (#689) inserts ``render_narration``
(``media.render_narration``) between ``load_scripts`` and
``transcribe_narration``. It regenerates the long-form AND short-form video
narration audio from their OWN scripts + CTAs (``media.cta.video`` /
``media.cta.video_short``) into ``long_narration_audio_path`` /
``short_narration_audio_path`` — replacing the shared ``podcast_audio_path`` the
renders used to read (which left every video silent because Stage-2 never
carried narration audio across from Stage-1). ``transcribe_narration``,
``qa_audio``, and the two renders are all re-pointed at these per-lane channels,
so each video plays — and captions / QAs — the narration it actually voices.

``transcribe_narration`` (``media.transcribe_narration``) runs ONE ASR pass
**per lane** over that lane's narration audio, producing per-lane SRT caption
tracks (``long_caption_srt_path`` / ``short_caption_srt_path``) the matching
render burns in (#676), plus a per-lane fidelity check of the ASR transcript vs
its source script (catches TTS dropouts / truncation).

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
        "Stage-2 media pipeline (#689/#675/#676/#1193/#682): load persisted "
        "Stage-1 scripts/shot-lists → render per-lane narration audio "
        "(long+short, own script + CTA) → one ASR pass per lane for per-lane "
        "captions + fidelity QA → audio QA per lane (silence/volume/duration, "
        "deterministic, #1193) → render 16:9 long-form + 9:16 short-form videos "
        "with their own narration + captions burned in → video QA (A/V sync, "
        "caption presence, gated frame human-detection) → persist durable "
        "task-keyed media_assets rows. Gate-2 distribution runs post-approval "
        "as a job (Plans 7-8)."
    ),
    "entry": "load_scripts",
    "nodes": [
        {"id": "load_scripts", "atom": "media.load_scripts"},
        {"id": "render_narration", "atom": "media.render_narration"},
        {"id": "transcribe_narration", "atom": "media.transcribe_narration"},
        {"id": "qa_audio", "atom": "qa.audio"},
        {"id": "render_long_video", "atom": "media.render_long_video"},
        {"id": "render_short_video", "atom": "media.render_short_video"},
        {"id": "media_qa", "atom": "media.qa"},
        {"id": "persist_media", "atom": "media.persist"},
    ],
    "edges": [
        {"from": "load_scripts", "to": "render_narration"},
        {"from": "render_narration", "to": "transcribe_narration"},
        {"from": "transcribe_narration", "to": "qa_audio"},
        {"from": "qa_audio", "to": "render_long_video"},
        {"from": "render_long_video", "to": "render_short_video"},
        {"from": "render_short_video", "to": "media_qa"},
        {"from": "media_qa", "to": "persist_media"},
        {"from": "persist_media", "to": "END"},
    ],
}

__all__ = ["MEDIA_PIPELINE_GRAPH_DEF"]
