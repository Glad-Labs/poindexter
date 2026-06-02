"""The dev_diary pipeline as a static graph_def spec (atom-cutover #355 follow-through).

dev_diary is the daily build-in-public narrative post. It historically ran via
the hand-coded ``dev_diary`` factory in ``services/pipeline_templates/__init__.py``
— and its ``pipeline_templates`` row carried a node-less factory-pointer shim
(``{"factory": "services.pipeline_templates.dev_diary"}``), which
``load_active_graph_def`` ignores (no ``"nodes"`` key → returns None → runner
falls back to the factory).

This spec moves dev_diary onto the same graph_def/atom path as ``canonical_blog``
so BOTH templates run through atoms (operator request 2026-06-02). The seed
migration replaces the factory-shim row with these real atom nodes; once the
runner finds ``"nodes"`` it compiles + runs them instead of the factory.

Minimal 4-atom chain — a status-report artifact intentionally skips the QA
rails / SEO / writer-self-review / training-capture that canonical_blog runs
(see the legacy factory docstring for the rationale: those either don't fit a
build-in-public post or actively harm clean narrative prose):

    verify_task -> narrate_bundle -> source_featured_image -> finalize_task

``narrate_bundle`` is the dev_diary writer atom (single LLM call over the
preserved PR/commit bundle); the other three are surfaced stage atoms. The
per-task experiment/variant assignment flows through ``state`` (the same seam
canonical_blog uses), so the writer-model A/B harness is preserved on this path.
"""

from __future__ import annotations

from typing import Any

DEV_DIARY_GRAPH_DEF: dict[str, Any] = {
    "name": "dev_diary",
    "description": (
        "dev_diary pipeline (atom-composed): verify_task -> narrate_bundle "
        "(single-call narrative writer) -> source_featured_image -> "
        "finalize_task. Mirrors the retired legacy factory; no QA/SEO rails "
        "by design (status-report artifact)."
    ),
    "entry": "verify_task",
    "nodes": [
        {"id": "verify_task", "atom": "stage.verify_task"},
        {"id": "narrate_bundle", "atom": "atoms.narrate_bundle"},
        {"id": "source_featured_image", "atom": "stage.source_featured_image"},
        {"id": "finalize_task", "atom": "stage.finalize_task"},
    ],
    "edges": [
        {"from": "verify_task", "to": "narrate_bundle"},
        {"from": "narrate_bundle", "to": "source_featured_image"},
        {"from": "source_featured_image", "to": "finalize_task"},
        {"from": "finalize_task", "to": "END"},
    ],
}

__all__ = ["DEV_DIARY_GRAPH_DEF"]
