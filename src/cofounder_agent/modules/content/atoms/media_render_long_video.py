"""media.render_long_video — Stage-2 long-form (16:9) render atom (Plan 4).

Wires the EXISTING director-driven render engine
(``services/video_renderers/shot_list_renderer.render_shot_list``) into the
``media_pipeline`` graph_def. Reads the persisted ``video_shot_list`` channel
(loaded by ``media.load_scripts``), renders the 16:9 long-form video, and
surfaces the result path on the ``long_video_path`` channel.

Thin by design: all the wiring lives in the shared
``_media_render.render_from_state`` helper so the long/short atoms can't drift.

NOTE (#674 trap): ``long_video_path`` MUST be a declared ``PipelineState``
channel (it is — added in Plan 4) or LangGraph silently drops it.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="media.render_long_video",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: render the 16:9 long-form video from the persisted "
        "video_shot_list via the director-driven shot-list renderer."
    ),
    inputs=(
        FieldSpec(name="video_shot_list", type="dict", description="16:9 director shot list"),
        FieldSpec(name="podcast_audio_path", type="str", description="narration MP3 path", required=False),
        FieldSpec(name="video_ambient_audio_path", type="str", description="ambient bed path", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (SDXL/Wan2.1 config)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
    ),
    outputs=(
        FieldSpec(name="long_video_path", type="str", description="rendered 16:9 MP4 path ('' on no-op/failure)"),
    ),
    requires=("task_id",),
    produces=("long_video_path",),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Render the long-form (16:9) video from ``video_shot_list``."""
    from modules.content.atoms._media_render import render_from_state

    return await render_from_state(
        state, shot_list_key="video_shot_list", output_key="long_video_path"
    )


__all__ = ["ATOM_META", "run"]
