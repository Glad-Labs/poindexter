"""Validate + compile the podcast_pipeline graph_def (Stage-3, #689 deviation).

Mirrors ``test_canonical_blog_spec.py``. The seed migration inserts this dict
without importing the architect (migrations-smoke is a light env), so this
contract test is the ONLY pre-runtime guard that every node atom resolves and
the requires/produces chain connects. A broken spec would otherwise only fail
on the first live podcast dispatch.
"""

from __future__ import annotations

import pytest

from services import pipeline_architect
from services.atom_registry import discover
from services.podcast_pipeline_spec import PODCAST_PIPELINE_GRAPH_DEF


@pytest.mark.unit
class TestPodcastPipelineSpec:
    def test_shape(self):
        spec = PODCAST_PIPELINE_GRAPH_DEF
        assert spec["name"] == "podcast_pipeline"
        assert spec["entry"] == "load_script"
        node_atoms = {n["atom"] for n in spec["nodes"]}
        # The isolated Stage-3 podcast chain (deviation from #689's single
        # media_pipeline graph): load → render → audio-QA → persist.
        assert node_atoms == {
            "podcast.load_script",
            "podcast.render",
            "qa.audio",
            "podcast.persist",
        }

    def test_linear_chain_wired(self):
        """The four nodes run as a strict linear chain terminating at END —
        no video atoms, so a video-render crash can never reach this graph."""
        edges = {(e["from"], e["to"]) for e in PODCAST_PIPELINE_GRAPH_DEF["edges"]}
        assert ("load_script", "render") in edges
        assert ("render", "qa_audio") in edges
        assert ("qa_audio", "persist") in edges
        assert ("persist", "END") in edges

    def test_passes_validator(self):
        discover()  # registers podcast.* atoms + surfaces qa.audio (idempotent)
        ok, errors = pipeline_architect._validate_spec(PODCAST_PIPELINE_GRAPH_DEF)
        assert ok is True, errors

    def test_compiles_via_build_graph_from_spec(self):
        discover()
        graph = pipeline_architect.build_graph_from_spec(
            PODCAST_PIPELINE_GRAPH_DEF, pool=None, record_sink=[],
        )
        compiled = graph.compile()
        assert compiled is not None
