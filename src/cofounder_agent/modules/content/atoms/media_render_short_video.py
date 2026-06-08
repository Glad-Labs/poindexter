"""media.render_short_video — Stage-2 short-form (9:16) render atom (Plan 4).

Sibling to ``media.render_long_video``: reads the persisted ``short_shot_list``
channel (the purpose-built 9:16 director output loaded by ``media.load_scripts``),
renders the short via the same engine, and surfaces the result path on the
``short_video_path`` channel. The 9:16 aspect on the shot list drives the
1080×1920 frame profile inside the shared helper.

Thin by design: all wiring lives in ``_media_render.render_from_state``.

NOTE (#674 trap): ``short_video_path`` MUST be a declared ``PipelineState``
channel (it is — added in Plan 4) or LangGraph silently drops it.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="media.render_short_video",
    type="atom",
    version="1.0.0",
    description=(
        "Stage-2: render the 9:16 short-form video from the persisted "
        "short_shot_list via the director-driven shot-list renderer."
    ),
    inputs=(
        FieldSpec(name="short_shot_list", type="dict", description="9:16 director shot list"),
        FieldSpec(name="podcast_audio_path", type="str", description="narration MP3 path", required=False),
        FieldSpec(name="video_ambient_audio_path", type="str", description="ambient bed path", required=False),
        FieldSpec(name="site_config", type="object", description="DI seam (SDXL/Wan2.1 config)", required=False),
        FieldSpec(name="database_service", type="object", description="DB service (pool source)", required=False),
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
    ),
    outputs=(
        FieldSpec(name="short_video_path", type="str", description="rendered 9:16 MP4 path ('' on no-op/failure)"),
    ),
    requires=("task_id",),
    produces=("short_video_path",),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("filesystem",),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Render the short-form (9:16) video from ``short_shot_list``."""
    from modules.content.atoms._media_render import render_from_state

    return await render_from_state(
        state, shot_list_key="short_shot_list", output_key="short_video_path"
    )


__all__ = ["ATOM_META", "run"]
