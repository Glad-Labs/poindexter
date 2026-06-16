"""Single source of truth for the finalize ``task_metadata`` blob.

The ~31-key metadata dict that lands on ``pipeline_versions.stage_data``
(under both ``metadata`` and ``task_metadata``) and on
``pipeline_tasks.task_metadata`` was historically assembled inline in two
places — the dev_diary terminal stage (``FinalizeTaskStage``) and the
canonical_blog graph_def atom (``content.persist_task``). They drifted:
the atom path grew four media keys the stage path never gained, which is
the same class of regression as Glad-Labs/poindexter#693 (metadata
silently diverging between the two finalize paths).

Both call sites now build the blob through :func:`build_task_metadata`,
so a new media / SEO / image field added here lands on **both** paths at
once and the two can never silently diverge again. A parity test
(``tests/unit/services/stages/test_task_metadata_parity_693.py``) pins
the invariant.

Design note — why the derived fields are arguments, not state reads:
``preview_token``, ``content_text`` and the normalized ``seo_*`` values
are computed *differently* by the two callers (``FinalizeTaskStage``
mints a fresh preview token and appends a Sources section to the body;
``content.persist_task`` does neither, and the two compute the rounded
quality score with subtly different falsy-handling). Folding those into
the helper would change dev_diary behaviour. So the caller owns the
derivations and passes the results in; everything else is a straight
``state`` read, keeping the key set — the thing that drifted — unified.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["build_task_metadata"]


def build_task_metadata(
    state: Mapping[str, Any],
    *,
    preview_token: str,
    content_text: str,
    seo_title: str,
    seo_description: str,
    seo_keywords_list: list[Any],
    final_quality_score: float | int,
    early_eval_score: float | int,
) -> dict[str, Any]:
    """Assemble the canonical finalize ``task_metadata`` blob.

    Args:
        state: The pipeline context/state dict. Passthrough fields
            (topic, style, tone, category, target_audience, the
            ``featured_image_*`` group, qa_final_score, and the media
            keys) are read from here by identical key names on both
            paths.
        preview_token: The ``/preview/{token}`` token. The caller mints
            or reuses it (FinalizeTaskStage mints a fresh one when an
            upstream stage didn't; content.persist_task passes through).
        content_text: The finalized body. Used for ``content``,
            ``pre_approve_content``, ``content_length`` and
            ``word_count``. The caller owns normalization / Sources-
            section appending.
        seo_title: Normalized SEO title.
        seo_description: Normalized SEO description.
        seo_keywords_list: SEO keywords as a list (the DB-update side
            stores the comma-joined string; the metadata blob keeps the
            list form).
        final_quality_score: The rounded final quality score.
        early_eval_score: The early pattern-eval score (pre-QA).

    Returns:
        The metadata dict — the same key set on every call.
    """
    return {
        "preview_token": preview_token,
        "featured_image_url": state.get("featured_image_url"),
        "featured_image_alt": state.get("featured_image_alt", ""),
        "featured_image_width": state.get("featured_image_width"),
        "featured_image_height": state.get("featured_image_height"),
        "featured_image_photographer": state.get("featured_image_photographer"),
        "featured_image_source": state.get("featured_image_source"),
        "content": content_text,
        # Pre-approve snapshot for the auto_publish_gate edit-distance
        # signal — publish_service diffs this against the post-approve
        # content (which may carry operator edits) when writing
        # published_post_edit_metrics.
        "pre_approve_content": content_text,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "seo_keywords": seo_keywords_list,
        "topic": state.get("topic", ""),
        "style": state.get("style", ""),
        "tone": state.get("tone", ""),
        "category": state.get("category", ""),
        "target_audience": state.get("target_audience") or "General",
        "post_id": None,
        "quality_score": final_quality_score,
        "quality_score_early_eval": early_eval_score,
        "qa_final_score": state.get("qa_final_score"),
        "content_length": len(content_text),
        "word_count": len(content_text.split()),
        "podcast_script": state.get("podcast_script", ""),
        "video_scenes": state.get("video_scenes", []),
        "short_summary_script": state.get("short_summary_script", ""),
        # Glad-Labs/poindexter#649 PR 2 — the director's shot list
        # rides through to publish_service so it lands on
        # posts.video_shot_list. Absent → downstream falls back to the
        # legacy slideshow renderer.
        "video_shot_list": state.get("video_shot_list"),
        # Short-form (9:16) media keys (#517 / #1233 / #690). Empty on the
        # dev_diary path (no media stages); read defensively downstream.
        "short_shot_list": state.get("short_shot_list"),
        "video_ambient_audio_path": state.get("video_ambient_audio_path", ""),
        "podcast_audio_path": state.get("podcast_audio_path", ""),
        "podcast_intro_audio_path": state.get("podcast_intro_audio_path", ""),
        "video_long_script": state.get("video_long_script", ""),
    }
