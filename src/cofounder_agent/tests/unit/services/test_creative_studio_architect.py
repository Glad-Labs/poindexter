"""Creative studio phase 5 (poindexter#1139): the architect can compose it.

The architect only composes what its prompt and catalog tell it about. These
pin that the creative chain it is taught validates, that it knows which
characters exist, and that the rendered prompt carries them.
"""

from __future__ import annotations

from poindexter.services import pipeline_architect as pa
from poindexter.services.atom_registry import discover
from poindexter.services.persona_service import cast_roster
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    base = {
        "site_name": "Glad Labs",
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
        "persona.dex.style_policy": "stylized", "persona.dex.portrait_url": "https://cdn/dex.png",
        "persona.dex.appearance": "a young fox engineer in goggles",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true",
        "persona.off.display_name": "Off", "persona.off.enabled": "false",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


def test_roster_lists_enabled_characters_with_their_faces():
    roster = cast_roster(_sc())
    assert "- dex: Dex (stylized, has a portrait); looks: a young fox engineer in goggles" in roster
    assert "- nova: Nova (photoreal, no portrait — voice only)" in roster
    assert "off" not in roster


def test_empty_roster_says_how_to_make_one():
    assert "poindexter personas create" in cast_roster(SiteConfig(initial_config={}))


def test_architect_prompt_carries_the_roster_and_the_creative_pattern():
    rendered = pa._resolve_system_prompt(_sc())
    assert "- dex: Dex" in rendered
    assert "story.write_screenplay -> media.render_dialogue" in rendered
    assert "{cast_roster}" not in rendered


CREATIVE_SPEC = {
    "name": "dex_explains_vram",
    "description": "a two-minute cartoon dialogue",
    "entry": "write",
    "nodes": [
        {"id": "write", "atom": "story.write_screenplay",
         "config": {"screenplay_format": "dialogue", "screenplay_cast": ["dex", "nova"],
                    "screenplay_target_seconds": 120}},
        {"id": "voice", "atom": "media.render_dialogue"},
        {"id": "direct", "atom": "media.direct_screenplay",
         "config": {"screenplay_visual_style": "flat 2D cartoon"}},
        {"id": "captions", "atom": "media.transcribe_narration"},
        {"id": "render", "atom": "media.render_long_video"},
        {"id": "qa", "atom": "media.qa"},
        {"id": "persist", "atom": "media.persist"},
    ],
    "edges": [
        {"from": "write", "to": "voice"}, {"from": "voice", "to": "direct"},
        {"from": "direct", "to": "captions"}, {"from": "captions", "to": "render"},
        {"from": "render", "to": "qa"}, {"from": "qa", "to": "persist"},
        {"from": "persist", "to": "END"},
    ],
}


def test_the_taught_creative_chain_validates():
    discover()
    ok, errors = pa._validate_composed_spec(CREATIVE_SPEC)
    assert ok, errors


def test_a_script_only_plan_validates():
    discover()
    spec = {"name": "script_only", "description": "d", "entry": "write",
            "nodes": [{"id": "write", "atom": "story.write_screenplay"}],
            "edges": [{"from": "write", "to": "END"}]}
    ok, errors = pa._validate_composed_spec(spec)
    assert ok, errors


def test_the_validator_cannot_order_declared_channels_so_the_atom_does():
    """The architect's validator treats every declared PipelineState channel
    as an initial-state field, so it accepts the director before the writer.
    The director's own input check is what catches that order at run time,
    with the fix in the message."""
    import asyncio

    import pytest

    from poindexter.modules.content.atoms import media_direct_screenplay

    discover()
    spec = {**CREATIVE_SPEC, "entry": "direct",
            "nodes": [n for n in CREATIVE_SPEC["nodes"] if n["id"] in ("direct", "write")],
            "edges": [{"from": "direct", "to": "write"}, {"from": "write", "to": "END"}]}
    ok, _ = pa._validate_composed_spec(spec)
    assert ok  # documents the validator's leniency
    with pytest.raises(RuntimeError, match="run story.write_screenplay and media.render_dialogue first"):
        asyncio.run(media_direct_screenplay.run({"site_config": _sc(), "topic": "t"}))
