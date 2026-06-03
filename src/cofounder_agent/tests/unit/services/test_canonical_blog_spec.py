"""Validate + compile the canonical_blog graph_def (atom-cutover Plan 4, #355)."""

from __future__ import annotations

import pytest

from services import pipeline_architect
from services.atom_registry import discover
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF


@pytest.mark.unit
class TestCanonicalBlogSpec:
    def test_shape(self):
        spec = CANONICAL_BLOG_GRAPH_DEF
        assert spec["name"] == "canonical_blog"
        assert spec["entry"] == "verify_task"
        node_atoms = {n["atom"] for n in spec["nodes"]}
        # The qa.* atoms replace cross_model_qa. qa.programmatic restores the
        # programmatic anti-hallucination gate dropped at the #355 cutover.
        assert {"qa.programmatic", "qa.critic", "qa.deepeval", "qa.guardrails",
                "qa.ragas", "qa.aggregate"} <= node_atoms
        # The three seo.* atoms replace generate_seo_metadata (#362).
        assert {"seo.generate_title", "seo.generate_description", "seo.extract_keywords"} <= node_atoms
        # No legacy monolithic QA / SEO stage nodes.
        assert "stage.cross_model_qa" not in node_atoms
        assert "stage.generate_seo_metadata" not in node_atoms
        # The surviving coarse stages are present as stage.* nodes.
        for s in (
            "verify_task", "generate_content", "writer_self_review",
            "resolve_internal_link_placeholders", "quality_evaluation",
            "url_validation", "replace_inline_images", "source_featured_image",
            "generate_media_scripts",
            "generate_video_shot_list", "capture_training_data", "finalize_task",
        ):
            assert f"stage.{s}" in node_atoms, s

    def test_programmatic_runs_before_critic(self):
        """The programmatic validator is the cheap deterministic gate and must
        sit FIRST in the qa block (caption_images → qa_programmatic → qa_critic)."""
        edges = {(e["from"], e["to"]) for e in CANONICAL_BLOG_GRAPH_DEF["edges"]}
        assert ("caption_images", "qa_programmatic") in edges
        assert ("qa_programmatic", "qa_critic") in edges
        # The old caption_images → qa_critic edge must be gone (re-routed).
        assert ("caption_images", "qa_critic") not in edges

    def test_passes_plan1_validator(self):
        discover()  # surfaces stage.* + registers qa.* atoms (idempotent)
        ok, errors = pipeline_architect._validate_spec(CANONICAL_BLOG_GRAPH_DEF)
        assert ok is True, errors

    def test_compiles_via_build_graph_from_spec(self):
        discover()
        graph = pipeline_architect.build_graph_from_spec(
            CANONICAL_BLOG_GRAPH_DEF, pool=None, record_sink=[],
        )
        # compile() resolves entry/edges/nodes — proves every stage.* and
        # qa.* node has a registered callable and the wiring is valid.
        compiled = graph.compile()
        assert compiled is not None
