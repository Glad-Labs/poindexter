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
        # The three serial seo.* atoms were collapsed into one structured call
        # (seo.generate_all_metadata, poindexter#734) — saves ~2 min/post.
        assert "seo.generate_all_metadata" in node_atoms
        assert "seo.generate_title" not in node_atoms
        assert "seo.generate_description" not in node_atoms
        assert "seo.extract_keywords" not in node_atoms
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

    def test_review_video_shot_list_node_between_director_and_training(self):
        spec = CANONICAL_BLOG_GRAPH_DEF
        node_atoms = {n["atom"] for n in spec["nodes"]}
        assert "stage.review_video_shot_list" in node_atoms
        edges = {(e["from"], e["to"]) for e in spec["edges"]}
        assert ("generate_video_shot_list", "review_video_shot_list") in edges
        assert ("review_video_shot_list", "capture_training_data") in edges
        # The old direct edge is gone — review sits in between now.
        assert ("generate_video_shot_list", "capture_training_data") not in edges

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
        # qa_unlinked_attribution (#765) is inserted between citations and
        # consistency — so the old qa_citations → qa_consistency edge is gone.
        assert ("qa_citations", "qa_unlinked_attribution") in edges
        assert ("qa_unlinked_attribution", "qa_consistency") in edges
        assert ("qa_citations", "qa_consistency") not in edges
        # qa_self_consistency is inserted between consistency and web_factcheck
        assert ("qa_consistency", "qa_self_consistency") in edges
        assert ("qa_self_consistency", "qa_web_factcheck") in edges
        assert ("qa_web_factcheck", "qa_aggregate") in edges
        # The old direct qa_vision → qa_aggregate edge must be gone (re-routed).
        assert ("qa_vision", "qa_aggregate") not in edges

    def test_citation_reconciliation_nodes_wired(self):
        """#765: content.reconcile_citations sits after the writer block (before
        quality_evaluation) so its inserted links flow through the dead-link
        check; qa.unlinked_attribution sits after qa.citations."""
        spec = CANONICAL_BLOG_GRAPH_DEF
        node_atoms = {n["atom"] for n in spec["nodes"]}
        assert "content.reconcile_citations" in node_atoms
        assert "qa.unlinked_attribution" in node_atoms
        edges = {(e["from"], e["to"]) for e in spec["edges"]}
        assert ("resolve_internal_link_placeholders", "reconcile_citations") in edges
        assert ("reconcile_citations", "quality_evaluation") in edges
        # The old direct edge must be re-routed through reconcile_citations.
        assert ("resolve_internal_link_placeholders", "quality_evaluation") not in edges

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

    def test_qa_rescue_cycle_wired(self):
        spec = CANONICAL_BLOG_GRAPH_DEF

        node_atoms = {n["atom"] for n in spec["nodes"]}
        assert "qa.rewrite" in node_atoms

        edges = spec["edges"]
        pairs = {(e["from"], e["to"]) for e in edges}
        # Branch edge: qa_aggregate -> qa_rewrite, flagged branch.
        assert ("qa_aggregate", "qa_rewrite") in pairs
        branch_edge = next(
            e for e in edges if e["from"] == "qa_aggregate" and e["to"] == "qa_rewrite"
        )
        assert branch_edge.get("branch") is True
        # Loop edge: qa_rewrite -> qa_programmatic, flagged loop.
        loop_edge = next(
            e for e in edges if e["from"] == "qa_rewrite" and e["to"] == "qa_programmatic"
        )
        assert loop_edge.get("loop") is True
        # The default forward edge from qa_aggregate is unchanged.
        assert ("qa_aggregate", "seo_all_metadata") in pairs

    def test_preview_gate_wired(self):
        spec = CANONICAL_BLOG_GRAPH_DEF

        # The gate node exists — an approval_gate atom with the regen targets in
        # its static config (the atom reads them on a pending-regen short-circuit).
        gate = next(n for n in spec["nodes"] if n["id"] == "preview_gate")
        assert gate["atom"] == "atoms.approval_gate"
        cfg = gate.get("config") or {}
        assert cfg.get("gate_name") == "preview_gate"
        assert cfg.get("regen_targets") == {
            "images": "plan_image_markers", "text": "generate_draft",
        }

        edges = spec["edges"]
        pairs = {(e["from"], e["to"]) for e in edges}
        # Rerouted finalize: record_pipeline_version -> preview_gate -> evaluate.
        assert ("record_pipeline_version", "preview_gate") in pairs
        assert ("preview_gate", "evaluate_auto_publish") in pairs
        # The old direct edge is gone — every post now flows THROUGH the gate
        # (a passthrough no-op while disabled).
        assert ("record_pipeline_version", "evaluate_auto_publish") not in pairs
        # Backward branch+loop regen edges to the image + writer block entries.
        img = next(
            e for e in edges
            if e["from"] == "preview_gate" and e["to"] == "plan_image_markers"
        )
        assert img.get("branch") is True and img.get("loop") is True
        txt = next(
            e for e in edges
            if e["from"] == "preview_gate" and e["to"] == "generate_draft"
        )
        assert txt.get("branch") is True and txt.get("loop") is True

    def test_node_count_is_40(self):
        # 38 + preview_gate (component-scoped regen gate, seeded disabled) + social.generate_drafts
        assert len(CANONICAL_BLOG_GRAPH_DEF["nodes"]) == 40
