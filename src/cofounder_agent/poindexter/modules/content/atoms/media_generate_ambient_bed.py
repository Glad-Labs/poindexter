"""media.generate_ambient_bed — the music bed under a video's narration.

A blog post's videos get their bed from ``generate_media_scripts``; a graph
without that stage (``news_short``) had none, so news Shorts went out
narration-only beside blog Shorts with music. This node makes the same bed with
the same code (``modules/content/ambient_bed``) and hands it to the render as
``video_ambient_audio_path``, which the compositor mixes at a constant -18 dB.

Best-effort: no audio engine, a busy GPU or a failed render leaves the bed
unset and the video renders without music. A bed already in state is kept.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.modules.content.ambient_bed import ambient_bed_or_nothing, ambient_mood_cues
from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="media.generate_ambient_bed",
    type="atom",
    version="1.0.0",
    description=(
        "Make the instrumental music bed mixed under a video's narration. "
        "INPUT task_id, title (+ video_scenes for mood). OUTPUT "
        "video_ambient_audio_path. USUALLY PRECEDES media.render_short_video / "
        "media.render_long_video; best-effort (no bed = narration only)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="names the durable bed file"),
        FieldSpec(name="title", type="str", required=False, description="mood cue when there are no scenes"),
        FieldSpec(name="video_scenes", type="list[str]", required=False, description="mood cues"),
    ),
    outputs=(
        FieldSpec(name="video_ambient_audio_path", type="str", description="the bed, mixed under the narration"),
    ),
    requires=("task_id",),
    produces=("video_ambient_audio_path",),
    capability_tier="standard",
    cost_class="compute",
    idempotent=False,
    side_effects=("generates music on the GPU",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.video_service import VIDEO_DIR

    if str(state.get("video_ambient_audio_path") or "").strip():
        logger.info("[media.generate_ambient_bed] keeping the bed already set")
        return {}

    task_id = str(state.get("task_id") or "")
    if not task_id:
        return {}
    mood = ambient_mood_cues(list(state.get("video_scenes") or [])) or str(state.get("title") or "")
    path = await ambient_bed_or_nothing(
        mood,
        site_config=state.get("site_config"),
        # Durable + deterministic (poindexter#1021): the render reads it in
        # another container.
        output_dir=str(VIDEO_DIR),
        output_stem=f"{task_id}_ambient",
        task_id=task_id,
        stage="media.generate_ambient_bed",
    )
    return {"video_ambient_audio_path": path} if path else {}
