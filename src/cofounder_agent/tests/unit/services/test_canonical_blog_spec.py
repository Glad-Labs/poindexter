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
        # programmatic anti-hallucination gate dropped at the #355 cutover;
        # qa.vision restores the image-relevance + rendered-preview gates (#563);
        # qa.topic_delivery / qa.citations / qa.consistency / qa.web_factcheck
        # restore four more checks the cutover dropped (#658/#659/#660/#661).
        # qa.guardrails was removed in #730: guardrails-ai was uninstalled
        # 2026-05-12, the native re-implementation is disabled behind
        # guardrails_enabled=false, and the atom was a dead no-op.
        assert {"qa.programmatic", "qa.critic", "qa.deepeval",
                "qa.ragas", "qa.vision", "qa.topic_delivery", "qa.citations",
                "qa.consistency", "qa.self_consistency", "qa.web_factcheck",
                "qa.aggregate"} <= node_atoms
        assert "qa.guardrails" not in node_atoms
        # The three seo.* atoms replace generate_seo_metadata (#362).
        assert {"seo.generate_title", "seo.generate_description", "seo.extract_keywords"} <= node_atoms
        # The content.* atoms replace the three coarse stages decomposed in
        # the #362 atom-granularity refactor: generate_content,
        # replace_inline_images, and finalize_task.
        assert {
            "content.generate_draft", "content.generate_title",
            "content.check_title_originality", "content.normalize_draft",
            "content.plan_image_markers", "content.generate_images",
            "content.inject_images",
            "content.compile_meta", "content.persist_task",
            "content.record_pipeline_version", "content.evaluate_auto_publish",
        } <= node_atoms
        # No legacy monolithic QA / SEO stage nodes.
        assert "stage.cross_model_qa" not in node_atoms
        assert "stage.generate_seo_metadata" not in node_atoms
        # The three coarse stages decomposed in #362 are NO LONGER stage.* nodes
        # (they survive as Stage classes for the dev_diary legacy path only).
        assert "stage.generate_content" not in node_atoms
        assert "stage.replace_inline_images" not in node_atoms
        assert "stage.finalize_task" not in node_atoms
        # The surviving coarse stages are present as stage.* nodes.
        for s in (
            "verify_task", "writer_self_review",
            "resolve_internal_link_placeholders", "quality_evaluation",
            "url_validation", "source_featured_image", "caption_images",
            "generate_media_scripts",
            "generate_video_shot_list", "capture_training_data",
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

    def test_vision_gate_wired_after_ragas(self):
        """qa.vision (#563) sits after qa.ragas in the rail block. Its
        image-relevance + rendered-preview reviews feed the aggregate decision.
        Restores the gates the #355 cutover dropped."""
        edges = {(e["from"], e["to"]) for e in CANONICAL_BLOG_GRAPH_DEF["edges"]}
        assert ("qa_ragas", "qa_vision") in edges
        # The old direct qa_ragas → qa_aggregate edge must be gone (re-routed).
        assert ("qa_ragas", "qa_aggregate") not in edges

    def test_four_restored_rails_wired_before_aggregate(self):
        """The four checks the #355 cutover silently dropped (#658/#659/#660/#661)
        run between qa.vision and qa.aggregate. qa.web_factcheck is LAST,
        immediately before qa.aggregate, so the known_wrong_fact rescue (#661)
        can read its verdict. qa.self_consistency (#621) is inserted between
        qa_consistency and qa_web_factcheck."""
        edges = {(e["from"], e["to"]) for e in CANONICAL_BLOG_GRAPH_DEF["edges"]}
        assert ("qa_vision", "qa_topic_delivery") in edges
        assert ("qa_topic_delivery", "qa_citations") in edges
        assert ("qa_citations", "qa_consistency") in edges
        # qa_self_consistency is inserted between consistency and web_factcheck
        assert ("qa_consistency", "qa_self_consistency") in edges
        assert ("qa_self_consistency", "qa_web_factcheck") in edges
        assert ("qa_web_factcheck", "qa_aggregate") in edges
        # The old direct qa_vision → qa_aggregate edge must be gone (re-routed).
        assert ("qa_vision", "qa_aggregate") not in edges

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
