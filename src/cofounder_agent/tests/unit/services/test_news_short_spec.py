"""The news_short graph_def: compiles, and reuses the blog's Short steps.

Asserts the spec passes both validators the seed path exercises
(``_validate_spec`` reachability + DAG, ``build_graph_from_spec`` schema gate),
mirroring test_image_rebuild_spec.py, plus the shape that makes it a news
Short: Short-only planning, no long render, release before the terminal status.
"""

import pytest

from poindexter.services.atom_registry import discover
from poindexter.services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
from poindexter.services.news_short_spec import NEWS_SHORT_GRAPH_DEF
from poindexter.services.pipeline_architect import _validate_spec, build_graph_from_spec


@pytest.fixture(autouse=True)
def _ensure_atoms_discovered():
    discover()


def _atoms(spec):
    return {n["id"]: n["atom"] for n in spec["nodes"]}


def test_spec_passes_reachability_and_dag():
    ok, errors = _validate_spec(NEWS_SHORT_GRAPH_DEF)
    assert ok, errors


def test_spec_passes_schema_gate():
    assert build_graph_from_spec(NEWS_SHORT_GRAPH_DEF, pool=None) is not None


def test_the_shot_list_plans_the_short_only():
    node = next(n for n in NEWS_SHORT_GRAPH_DEF["nodes"] if n["id"] == "shot_list")
    assert node["atom"] == "stage.generate_video_shot_list"
    assert node["config"] == {"short_only": True}


def test_there_is_no_long_render():
    atoms = set(_atoms(NEWS_SHORT_GRAPH_DEF).values())
    assert "media.render_long_video" not in atoms
    assert "media.render_thumbnail" not in atoms


def test_the_short_steps_are_the_media_pipelines_own():
    """A fix to any Short step reaches both the blog's Short and a news Short."""
    news = set(_atoms(NEWS_SHORT_GRAPH_DEF).values())
    media = set(_atoms(MEDIA_PIPELINE_GRAPH_DEF).values())
    shared = {
        "media.render_narration", "media.transcribe_narration", "qa.audio",
        "media.render_short_video", "media.package_short", "media.qa", "media.persist",
    }
    assert shared <= news and shared <= media


def test_release_follows_persist_and_precedes_the_terminal_status():
    edges = [(e["from"], e["to"]) for e in NEWS_SHORT_GRAPH_DEF["edges"]]
    assert ("persist_media", "finalize") in edges
    assert ("finalize", "done") in edges
    assert ("done", "END") in edges
    done = next(n for n in NEWS_SHORT_GRAPH_DEF["nodes"] if n["id"] == "done")
    assert done["atom"] == "atoms.set_task_status"
    assert done["config"]["target_status"] == "completed"
