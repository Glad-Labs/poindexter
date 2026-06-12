"""The ``podcast_pipeline`` Stage-3 graph_def spec (epic poindexter#689).

Pure data — NO imports beyond typing — so the seed migration can import this
dict without pulling in LangGraph / template_runner (the migrations-smoke CI
step applies migrations without a full app boot). This mirrors
``media_pipeline_spec.py`` and ``canonical_blog_spec.py``.

**Deviation from #689 (operator decision 2026-06-11):** the approved
video-pipeline-redesign keeps podcast as a parallel branch inside the single
``media_pipeline`` graph. This splits podcast into its OWN isolated Stage-3
graph so a video-render crash can never halt podcast production, and the two
media have fully independent dispatch / approval / distribution lifecycles. See
``docs/architecture/podcast-pipeline-stage3.md``.

Flow (linear chain):

  podcast.load_script → podcast.render → qa.audio → podcast.persist

- ``podcast.load_script`` loads the persisted Stage-1 ``podcast_script`` (+ the
  ``podcast_intro_audio_path`` sting from #690) by ``task_id``.
- ``podcast.render`` synthesizes the full-read narration via Kokoro/Speaches
  TTS (the ``PodcastService.synthesize`` helper), mixes the intro sting, and
  appends the per-medium CTA outro — surfacing ``podcast_audio_path``.
- ``qa.audio`` runs the deterministic silence / volume / duration checks on the
  narration (reused from the video pipeline; fail-soft).
- ``podcast.persist`` moves the MP3 to durable storage and records a
  task-keyed ``media_assets`` row (``type='podcast'``, ``post_id=NULL`` —
  resolved later by ``podcast_distribute``).

Seeded ``active=true``. The Stage-3 trigger (``dispatch_podcast_pipeline``) is
gated on ``podcast_pipeline_trigger_enabled``.
"""

from __future__ import annotations

from typing import Any

PODCAST_PIPELINE_GRAPH_DEF: dict[str, Any] = {
    "name": "podcast_pipeline",
    "description": (
        "Stage-3 podcast pipeline (#689 deviation): load persisted Stage-1 "
        "podcast_script → render full-read Kokoro/Speaches TTS narration with "
        "intro sting + per-medium CTA → deterministic audio QA "
        "(silence/volume/duration) → persist a durable task-keyed media_assets "
        "row (type='podcast'). Isolated from the video media_pipeline; "
        "post resolution + Gate-2 approval + RSS distribution run post-render "
        "as the podcast_distribute job."
    ),
    "entry": "load_script",
    "nodes": [
        {"id": "load_script", "atom": "podcast.load_script"},
        {"id": "render", "atom": "podcast.render"},
        {"id": "qa_audio", "atom": "qa.audio"},
        {"id": "persist", "atom": "podcast.persist"},
    ],
    "edges": [
        {"from": "load_script", "to": "render"},
        {"from": "render", "to": "qa_audio"},
        {"from": "qa_audio", "to": "persist"},
        {"from": "persist", "to": "END"},
    ],
}

__all__ = ["PODCAST_PIPELINE_GRAPH_DEF"]
