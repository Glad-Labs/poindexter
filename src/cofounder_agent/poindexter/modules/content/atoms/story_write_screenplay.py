"""story.write_screenplay — topic + cast in, validated screenplay JSON out.

Creative studio phase 2 (poindexter#1136). Thin adapter over
``services/screenplay.py``: the atom only reads its inputs from state / node
config and hands them to the library, so a graph that composes the writer
shows the whole contract here.

The architect passes the creative choices as node config (seeded onto the
atom input; state wins over config):

    {"atom": "story.write_screenplay",
     "config": {"screenplay_format": "dialogue", "screenplay_cast": ["dex", "nova"],
                "screenplay_target_seconds": 120, "screenplay_brief": "...",
                "screenplay_tone": "playful", "screenplay_narrator": "none"}}

Every key but ``topic`` is optional; gaps fill from ``story_default_*``
settings. A failure raises: a missing screenplay must stop the graph before
the dialogue and render steps spend GPU time on nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="story.write_screenplay",
    type="atom",
    version="1.0.0",
    description=(
        "Write a short animated screenplay about the topic for a cast of "
        "personas: JSON scenes of spoken lines (speaker, text, delivery) and "
        "visible actions. INPUT topic + optional node config "
        "screenplay_format (explainer|story|dialogue|sketch), screenplay_cast "
        "(persona slugs), screenplay_target_seconds, screenplay_brief, "
        "screenplay_tone, screenplay_narrator ('none' disables narration). "
        "OUTPUT screenplay. USUALLY FOLLOWED BY media.render_dialogue."
    ),
    inputs=(
        FieldSpec(name="topic", type="str", description="what the piece is about"),
        FieldSpec(name="screenplay_format", type="str", required=False,
                  description="explainer | story | dialogue | sketch"),
        FieldSpec(name="screenplay_cast", type="list[str]", required=False,
                  description="persona slugs who may speak/appear"),
        FieldSpec(name="screenplay_target_seconds", type="float", required=False,
                  description="approximate spoken length"),
        FieldSpec(name="screenplay_brief", type="str", required=False,
                  description="angle, audience, must-include points"),
        FieldSpec(name="screenplay_tone", type="str", required=False,
                  description="e.g. playful, earnest, deadpan"),
        FieldSpec(name="screenplay_narrator", type="str", required=False,
                  description="'none' = cast only; otherwise a narrator may speak"),
    ),
    outputs=(
        FieldSpec(name="screenplay", type="dict",
                  description="validated screenplay: title, logline, scenes[beats]"),
    ),
    requires=("topic",),
    produces=("screenplay",),
    capability_tier="standard",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls the writer LLM",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.screenplay import build_request, write_screenplay

    site_config = state.get("site_config")
    if site_config is None:
        raise RuntimeError("story.write_screenplay: no site_config on state")
    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else state.get("pool")

    request = build_request(state, site_config)
    screenplay = await write_screenplay(
        request, site_config=site_config, pool=pool,
        task_id=str(state.get("task_id") or "") or None,
    )
    return {"screenplay": screenplay}
