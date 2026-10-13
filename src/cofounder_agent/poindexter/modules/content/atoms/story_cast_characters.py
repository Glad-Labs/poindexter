"""story.cast_characters — a request in, a drawn and voiced cast out.

Creative studio (poindexter#1134). Thin adapter over ``services/casting.py``:
reuses the personas that fit the request and designs, draws and voices the
rest, so a story with its own characters (a fairytale, a sketch with people
nobody has made yet) can be asked for in one sentence.

Runs FIRST in the creative chain, before ``story.write_screenplay``. Its
outputs are state, and state wins over node config, so the cast, narrator and
art style belong on THIS node's config, not on the writer's or director's:

    {"atom": "story.cast_characters",
     "config": {"screenplay_format": "story", "screenplay_target_seconds": 120,
                "screenplay_brief": "...", "screenplay_cast": ["<must-have slug>"],
                "screenplay_visual_style": "<the look the request names, if any>"}}

With no look named (or only the medium, "cartoon"), the cast is drawn in the
niche's house style.

A failure raises: the writer cannot write for a cast that does not exist.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="story.cast_characters",
    type="atom",
    version="1.0.0",
    description=(
        "Cast a story: reuse existing personas that fit and design NEW "
        "characters for the rest (name, looks, personality, voice), drawing "
        "each a portrait in one shared art style and giving each a voice, "
        "saved as personas. Use it first whenever the request has characters "
        "that are not in the roster. INPUT topic + optional node config "
        "screenplay_format, screenplay_target_seconds, screenplay_brief, "
        "screenplay_cast (slugs that must appear), screenplay_visual_style "
        "(a look the request names; empty = the house style), screenplay_narrator "
        "('none' = no narrator, a slug = that persona narrates, empty = "
        "casting decides). OUTPUT screenplay_cast, screenplay_visual_style, "
        "screenplay_narrator, cast_sheet. USUALLY FOLLOWED BY "
        "story.write_screenplay."
    ),
    inputs=(
        FieldSpec(name="topic", type="str", description="what the piece is about"),
        FieldSpec(name="screenplay_format", type="str", required=False,
                  description="explainer | story | dialogue | sketch"),
        FieldSpec(name="screenplay_target_seconds", type="float", required=False,
                  description="approximate spoken length"),
        FieldSpec(name="screenplay_brief", type="str", required=False,
                  description="angle, audience, must-include points"),
        FieldSpec(name="screenplay_cast", type="list[str]", required=False,
                  description="persona slugs that must be in the cast"),
        FieldSpec(name="screenplay_visual_style", type="str", required=False,
                  description="a look the request names; empty = the house style"),
        FieldSpec(name="screenplay_narrator", type="str", required=False,
                  description="'none', a persona slug, or empty = casting decides"),
    ),
    outputs=(
        FieldSpec(name="screenplay_cast", type="list[str]",
                  description="persona slugs who may speak and appear"),
        FieldSpec(name="screenplay_visual_style", type="str",
                  description="the art style every still is drawn in"),
        FieldSpec(name="screenplay_narrator", type="str",
                  description="'none' or the narrating persona's slug"),
        FieldSpec(name="cast_sheet", type="dict",
                  description="who was reused, created, drawn and voiced"),
    ),
    requires=("topic",),
    produces=("screenplay_cast", "screenplay_visual_style", "screenplay_narrator", "cast_sheet"),
    capability_tier="standard",
    cost_class="compute",
    idempotent=False,
    side_effects=(
        "calls the writer LLM",
        "creates persona settings",
        "renders and uploads portraits",
        "writes voice reference clips",
    ),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from poindexter.services.casting import build_casting_request, cast_characters

    site_config = state.get("site_config")
    if site_config is None:
        raise RuntimeError("story.cast_characters: no site_config on state")
    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else state.get("pool")

    request = build_casting_request(state, site_config)
    return await cast_characters(
        request, site_config=site_config, pool=pool,
        task_id=str(state.get("task_id") or "") or None,
    )
