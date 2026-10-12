"""media.render_dialogue — a screenplay voiced, line by line, by its cast.

Creative studio phase 3 (poindexter#1137). Thin adapter over
``services/dialogue_audio.py``. Every ``line`` beat is spoken in its
speaker's own persona voice (narrator: ``dialogue_narrator_persona`` node
config, else ``story_default_narrator_persona``, else the house voice), the
clips are joined with scene / line / action-beat pauses, and the measured
timeline comes back with the track.

Outputs land on the channels the video chain already reads, so the existing
steps run unchanged after it:

- ``long_narration_audio_path`` — the dialogue track (``media.render_long_video``,
  ``media.transcribe_narration``, ``media.qa``);
- ``video_long_script`` — the spoken lines, for the caption fidelity check;
- ``dialogue_timeline`` — where each beat sits in the track, for
  ``media.direct_screenplay``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="media.render_dialogue",
    type="atom",
    version="1.0.0",
    description=(
        "Voice a screenplay: each line in its speaker persona's own voice "
        "(Kokoro or Chatterbox clone), joined with pauses into one track with a "
        "measured beat timeline. INPUT screenplay + optional node config "
        "dialogue_narrator_persona. OUTPUT long_narration_audio_path, "
        "video_long_script, dialogue_timeline. USUALLY FOLLOWS "
        "story.write_screenplay; USUALLY FOLLOWED BY media.direct_screenplay."
    ),
    inputs=(
        FieldSpec(name="screenplay", type="dict", description="from story.write_screenplay"),
        FieldSpec(name="dialogue_narrator_persona", type="str", required=False,
                  description="persona slug that voices the narrator; empty = house voice"),
    ),
    outputs=(
        FieldSpec(name="long_narration_audio_path", type="str", description="the dialogue track (wav)"),
        FieldSpec(name="video_long_script", type="str", description="spoken lines in order"),
        FieldSpec(name="dialogue_timeline", type="list[dict]",
                  description="per beat: beat_idx, scene_id, kind, speaker, text, characters, start_s, end_s"),
    ),
    requires=("screenplay",),
    produces=("long_narration_audio_path", "video_long_script", "dialogue_timeline"),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("filesystem", "calls TTS"),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0, retry_on=()),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.dialogue_audio import render_dialogue

    site_config = state.get("site_config")
    if site_config is None:
        raise RuntimeError("media.render_dialogue: no site_config on state")
    screenplay = state.get("screenplay")
    if not isinstance(screenplay, dict) or not screenplay.get("scenes"):
        raise RuntimeError("media.render_dialogue: no screenplay on state")

    task_id = str(state.get("task_id") or "adhoc")
    out_dir = tempfile.mkdtemp(prefix=f"dialogue-{task_id[:8]}-")
    narrator = state.get("dialogue_narrator_persona")
    result = await render_dialogue(
        screenplay,
        site_config=site_config,
        output_path=os.path.join(out_dir, "dialogue.wav"),
        narrator_persona=str(narrator) if narrator is not None else None,
    )
    return {
        "long_narration_audio_path": result["audio_path"],
        "video_long_script": result["spoken_script"],
        "dialogue_timeline": result["timeline"],
    }
