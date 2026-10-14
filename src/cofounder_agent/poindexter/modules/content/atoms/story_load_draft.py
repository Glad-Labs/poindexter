"""story.load_draft — a chat draft in, the state its render starts from out.

Chat drafting, phase 3. Thin adapter over
``creative_drafts.load_for_render``: a kept or ready script draft hands the
render its screenplay (voiced and directed downstream); a storyboard draft
hands over the screenplay, timeline, shot list and full-quality dialogue
track it was storyboarded with, so the video is the approved storyboard,
shot for shot. A cast draft, when named, supplies the art style and any
per-piece portraits.

Used by the fixed plan ``creative_drafts.render_spec`` builds; the
architect does not compose it.
"""

from __future__ import annotations

from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="story.load_draft",
    type="atom",
    version="1.0.0",
    description=(
        "Load a chat draft (a script or a storyboard) as the start of its "
        "render. INPUT node config creative_draft (the draft id) and optional "
        "cast_draft. OUTPUT screenplay, screenplay_visual_style, "
        "cast_portraits, and for a storyboard also dialogue_timeline, "
        "video_shot_list, video_long_script and long_narration_audio_path "
        "(plus video_ambient_audio_path when its rough cut was scored). "
        "Only used by plans built from a draft."
    ),
    inputs=(
        FieldSpec(name="creative_draft", type="str", description="the script or storyboard draft id"),
        FieldSpec(name="cast_draft", type="str", required=False,
                  description="a cast draft id supplying the art style"),
    ),
    outputs=(
        FieldSpec(name="screenplay", type="dict", description="the drafted screenplay"),
        FieldSpec(name="screenplay_visual_style", type="str", required=False, description="the art style"),
        FieldSpec(name="cast_portraits", type="dict", required=False,
                  description="characters redrawn for this piece, slug -> URL"),
        FieldSpec(name="dialogue_timeline", type="list[dict]", required=False,
                  description="a storyboard's measured beat timeline"),
        FieldSpec(name="video_shot_list", type="dict", required=False,
                  description="a storyboard's shot list"),
        FieldSpec(name="video_long_script", type="str", required=False,
                  description="a storyboard's spoken script, for captions"),
        FieldSpec(name="long_narration_audio_path", type="str", required=False,
                  description="a storyboard's dialogue track, downloaded"),
        FieldSpec(name="video_ambient_audio_path", type="str", required=False,
                  description="a storyboard's rough-cut score, downloaded, kept as the video's soundtrack"),
    ),
    requires=("creative_draft",),
    produces=(
        "screenplay", "screenplay_visual_style", "cast_portraits", "dialogue_timeline",
        "video_shot_list", "video_long_script", "long_narration_audio_path", "video_ambient_audio_path",
    ),
    capability_tier="standard",
    cost_class="free",
    idempotent=True,
    side_effects=("downloads a storyboard's dialogue track",),
    retry=RetryPolicy(max_attempts=2),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.creative_drafts import load_for_render

    site_config = state.get("site_config")
    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else state.get("pool")
    if pool is None:
        raise RuntimeError("story.load_draft: no database pool on state")
    return await load_for_render(
        pool, str(state.get("creative_draft") or ""), site_config=site_config,
        cast_draft=str(state.get("cast_draft") or "") or None,
    )
