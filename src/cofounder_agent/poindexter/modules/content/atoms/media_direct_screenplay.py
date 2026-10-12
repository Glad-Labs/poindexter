"""media.direct_screenplay — a voiced screenplay into a renderable shot list.

Creative studio phase 4 (poindexter#1138). Thin adapter over
``services/screenplay_director.py``: talking shots for each cast line
(lip-synced to the speaker's portrait), cutaways and scene stills for
narration and action beats, timed to the measured dialogue track. Writes
``video_shot_list``, which ``media.render_long_video`` renders as-is.

Node config ``screenplay_visual_style`` sets the art style every still
starts with ("modern anime key visual, cel shading"); without it the niche's
house style applies.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="media.direct_screenplay",
    type="atom",
    version="1.0.0",
    description=(
        "Direct a voiced screenplay: a 16:9 shot list timed to the dialogue "
        "track — lip-synced talking shots for cast lines, animated or still "
        "scene shots for narration and action, art style from node config "
        "screenplay_visual_style (else the niche house style). INPUT "
        "screenplay, dialogue_timeline, long_narration_audio_path. OUTPUT "
        "video_shot_list. USUALLY FOLLOWS media.render_dialogue; USUALLY "
        "FOLLOWED BY media.transcribe_narration then media.render_long_video."
    ),
    inputs=(
        FieldSpec(name="screenplay", type="dict", description="from story.write_screenplay"),
        FieldSpec(name="dialogue_timeline", type="list[dict]", description="from media.render_dialogue"),
        FieldSpec(name="long_narration_audio_path", type="str", description="the dialogue track"),
        FieldSpec(name="screenplay_visual_style", type="str", required=False,
                  description="art style prefix for every still, e.g. 'flat 2D cartoon, bold outlines'"),
    ),
    outputs=(
        FieldSpec(name="video_shot_list", type="dict", description="VideoShotList (16:9)"),
    ),
    requires=("screenplay", "dialogue_timeline", "long_narration_audio_path"),
    produces=("video_shot_list",),
    capability_tier="standard",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls the writer LLM",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.dialogue_audio import probe_seconds
    from poindexter.services.media_subject_policy import resolve_media_policy, video_style_prefix
    from poindexter.services.screenplay_director import direct_screenplay

    site_config = state.get("site_config")
    if site_config is None:
        raise RuntimeError("media.direct_screenplay: no site_config on state")
    screenplay = state.get("screenplay")
    timeline = state.get("dialogue_timeline")
    track = str(state.get("long_narration_audio_path") or "")
    if not isinstance(screenplay, dict) or not timeline or not track:
        raise RuntimeError(
            "media.direct_screenplay needs screenplay, dialogue_timeline and "
            "long_narration_audio_path — run story.write_screenplay and "
            "media.render_dialogue first",
        )
    style = str(state.get("screenplay_visual_style") or "").strip()
    if not style:
        style = video_style_prefix(resolve_media_policy(site_config, state.get("niche_slug")))
    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else state.get("pool")
    listing = await direct_screenplay(
        screenplay, list(timeline), await probe_seconds(track),
        site_config=site_config, style=style, pool=pool,
        task_id=str(state.get("task_id") or "") or None,
    )
    return {"video_shot_list": listing}
