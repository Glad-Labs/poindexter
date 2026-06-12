"""seo_refresh pipeline as a static graph_def (SEO Harvest Loop Phase 2, #763).

Pure data — NO imports beyond typing — so the seed migration can import just
this dict without pulling in LangGraph / template_runner (migrations-smoke runs
in a light env). Mirrors ``services/canonical_blog_spec.py``.

Unlike canonical_blog (which *generates* a draft), seo_refresh *hydrates* state
from an existing published post and re-optimizes only its title/meta:

    load_post (content.load_existing_post)   — hydrate content/title/slug/meta +
                                                target_query from the posts +
                                                seo_opportunities rows
      → optimize_meta (seo.optimize_metadata) — query-aware title/description
                                                rewrite for CTR (meta_only;
                                                body is read-only)
      → refresh_gate (atoms.approval_gate,    — pause for operator sign-off
         gate_name='seo_refresh_gate')          (approval-FIRST: the gate is
                                                seeded ENABLED, unlike draft_gate;
                                                resume via `poindexter pipeline
                                                resume <task_id>`). This IS the
                                                Lock 2 graduation mechanism.
      → republish (content.republish_post)    — apply meta_only, R2 export, ISR
                                                revalidate, stamp opportunity

Why no QA-rail atoms in v1
--------------------------
The body is unchanged from an already-published, already-QA'd post; only the
title/meta change, and the human approval gate is the quality control for a
conservative meta-only edit (design §9: "the minimum viable gate is
check_title_originality alone"). The canonical QA rails were intentionally left
off because (a) ``content.check_title_originality`` would flag the post's own
unchanged title as a duplicate of itself, and (b) ``qa.aggregate`` is built to
consume the full 12-rail block. QA rails are a clean fast-follow for deeper
scopes (meta_and_intro / full) — they ADD atoms, never branch this graph.
"""

from __future__ import annotations

from typing import Any

SEO_REFRESH_GRAPH_DEF: dict[str, Any] = {
    "name": "seo_refresh",
    "description": (
        "Re-optimize an existing post's title/meta toward its target query "
        "(meta_only), gated on operator approval, then republish."
    ),
    "entry": "load_post",
    "nodes": [
        {"id": "load_post", "atom": "content.load_existing_post"},
        {"id": "optimize_meta", "atom": "seo.optimize_metadata"},
        {
            "id": "refresh_gate",
            "atom": "atoms.approval_gate",
            # gate_artifact_keys surfaces the PROPOSED meta in pipeline_tasks.gate_artifact
            # so the operator reviews the actual change (the default artifact keys —
            # topic/title/excerpt/… — don't include seo_title/seo_description, which
            # ARE the thing being approved on a meta refresh). post_slug + title give
            # the reviewer the post identity; the live DB row holds the pre-edit meta
            # for comparison. Node `config` seeds the atom's state (see
            # pipeline_architect.build_graph_from_spec).
            "config": {
                "gate_name": "seo_refresh_gate",
                "gate_artifact_keys": [
                    "title",
                    "post_slug",
                    "seo_title",
                    "seo_description",
                    "target_query",
                ],
            },
        },
        {"id": "republish", "atom": "content.republish_post"},
    ],
    "edges": [
        {"from": "load_post", "to": "optimize_meta"},
        {"from": "optimize_meta", "to": "refresh_gate"},
        {"from": "refresh_gate", "to": "republish"},
        {"from": "republish", "to": "END"},
    ],
}

__all__ = ["SEO_REFRESH_GRAPH_DEF"]
