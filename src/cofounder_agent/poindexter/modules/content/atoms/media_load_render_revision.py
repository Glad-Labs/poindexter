"""media.load_render_revision — a kept render plus a revised shot list in, render state out.

Chat drafting, phase 4. Thin adapter over
``render_revision.load_revision_state``: the source render's dialogue track,
captions, timeline and script, the revised shot list a note produced, and
the source's clip-cache key, so ``media.render_long_video`` reuses every
unchanged shot's clip and renders only the changed ones.

Used by the fixed plan ``render_revision.revision_spec`` builds; the
architect does not compose it.
"""

from __future__ import annotations

from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="media.load_render_revision",
    type="atom",
    version="1.0.0",
    description=(
        "Load a finished render's kept clips and a revised shot list so only "
        "the changed shots re-render. INPUT node config render_source (the "
        "clip cache key) and shot_list_file. OUTPUT video_shot_list, "
        "long_narration_audio_path, long_caption_srt_path, dialogue_timeline, "
        "screenplay, video_clip_cache_key. Only used by render revisions."
    ),
    inputs=(
        FieldSpec(name="render_source", type="str", description="the source render's clip cache key"),
        FieldSpec(name="shot_list_file", type="str", description="the revised shot list, in the cache"),
    ),
    outputs=(
        FieldSpec(name="video_shot_list", type="dict", description="the revised shot list"),
        FieldSpec(name="long_narration_audio_path", type="str", description="the kept dialogue track"),
        FieldSpec(name="long_caption_srt_path", type="str", description="the kept captions"),
        FieldSpec(name="dialogue_timeline", type="list[dict]", description="the kept timeline"),
        FieldSpec(name="screenplay", type="dict", description="the script, titled as revised"),
        FieldSpec(name="video_clip_cache_key", type="str", description="where the kept clips are"),
    ),
    requires=("render_source", "shot_list_file"),
    produces=(
        "video_shot_list", "long_narration_audio_path", "long_caption_srt_path",
        "dialogue_timeline", "screenplay", "video_clip_cache_key",
    ),
    capability_tier="standard",
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.render_revision import load_revision_state

    return load_revision_state(
        str(state.get("render_source") or ""), str(state.get("shot_list_file") or ""),
        site_config=state.get("site_config"),
    )
