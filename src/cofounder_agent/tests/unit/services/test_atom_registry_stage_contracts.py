"""Virtual stage atoms surface declared I/O contracts (poindexter#983).

Every virtual stage atom advertised requires:()/produces:() — the
architect composed blind: 'generate the media scripts' could not be
mapped to the stage that does it, and reachability validation green-lit
render nodes with no script source. Stages now declare
``atom_requires`` / ``atom_produces`` class attrs; the registry surfaces
them onto the virtual atom's contract (and thereby its fingerprint).
"""
import pytest

from services.atom_registry import _stage_to_atom_meta


class _DeclaredStage:
    name = "declared"
    description = "d"
    atom_requires = ("content",)
    atom_produces = ("podcast_script", "video_scenes")


class _LegacyStage:
    name = "legacy"
    description = "d"


@pytest.mark.unit
def test_declared_stage_contract_surfaces():
    meta = _stage_to_atom_meta("declared", _DeclaredStage())
    assert meta.requires == ("content",)
    assert meta.produces == ("podcast_script", "video_scenes")


@pytest.mark.unit
def test_legacy_stage_stays_empty():
    meta = _stage_to_atom_meta("legacy", _LegacyStage())
    assert meta.requires == ()
    assert meta.produces == ()


@pytest.mark.unit
def test_declared_contract_changes_fingerprint():
    declared = _stage_to_atom_meta("declared", _DeclaredStage())
    legacy = _stage_to_atom_meta("legacy", _LegacyStage())
    assert declared.contract_fingerprint() != legacy.contract_fingerprint()


@pytest.mark.unit
def test_media_stages_declare_their_io():
    from modules.content.stages.generate_media_scripts import (
        GenerateMediaScriptsStage,
    )
    from modules.content.stages.generate_video_shot_list import (
        GenerateVideoShotListStage,
    )

    scripts = _stage_to_atom_meta(
        "generate_media_scripts", GenerateMediaScriptsStage())
    assert scripts.requires == ("content",)
    assert "podcast_script" in scripts.produces
    shots = _stage_to_atom_meta(
        "generate_video_shot_list", GenerateVideoShotListStage())
    assert shots.requires == ("content", "podcast_script")
    assert shots.produces == ("video_shot_list",)
