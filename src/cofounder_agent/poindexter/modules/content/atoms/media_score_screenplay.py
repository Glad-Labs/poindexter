"""media.score_screenplay — a voiced screenplay in, its soundtrack out.

Chat drafting, phase 6. Thin adapter over ``services/score.py``: music per
scene and sound effects on the actions worth hearing, laid on the dialogue's
own timeline into one track, which ``media.render_long_video`` mixes under
the dialogue as the video's soundtrack (``video_ambient_audio_path``).

Best-effort: no audio engine, a planning failure or nothing generated leaves
the soundtrack unset, and the video renders voices-only as before.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="media.score_screenplay",
    type="atom",
    version="1.0.0",
    description=(
        "Score a voiced cartoon: music under each scene and sound effects on "
        "its actions, timed to the dialogue track, as the video's soundtrack. "
        "INPUT screenplay, dialogue_timeline, long_narration_audio_path "
        "(+ screenplay_visual_style). OUTPUT video_ambient_audio_path. "
        "USUALLY FOLLOWS media.direct_screenplay; best-effort (no score = "
        "voices only)."
    ),
    inputs=(
        FieldSpec(name="screenplay", type="dict", description="the voiced screenplay"),
        FieldSpec(name="dialogue_timeline", type="list[dict]", description="from media.render_dialogue"),
        FieldSpec(name="long_narration_audio_path", type="str", description="the dialogue track"),
        FieldSpec(name="screenplay_visual_style", type="str", required=False, description="the art style"),
    ),
    outputs=(
        FieldSpec(name="video_ambient_audio_path", type="str", description="the score, mixed under the dialogue"),
    ),
    requires=("screenplay", "dialogue_timeline", "long_narration_audio_path"),
    produces=("video_ambient_audio_path",),
    capability_tier="standard",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls the writer LLM", "generates music and sound effects"),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.dialogue_audio import probe_seconds
    from poindexter.services.score import score_screenplay

    site_config = state.get("site_config")
    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else state.get("pool")
    track = str(state.get("long_narration_audio_path") or "")
    try:
        result = await score_screenplay(
            state.get("screenplay") or {}, list(state.get("dialogue_timeline") or []),
            duration_s=await probe_seconds(track), style=str(state.get("screenplay_visual_style") or ""),
            site_config=site_config, pool=pool, task_id=str(state.get("task_id") or "") or None,
        )
    except Exception as exc:  # noqa: BLE001 — a score is never worth failing the video for
        logger.warning("[media.score_screenplay] no score: %s", exc)
        return {}
    return {"video_ambient_audio_path": result["path"]} if result.get("path") else {}
