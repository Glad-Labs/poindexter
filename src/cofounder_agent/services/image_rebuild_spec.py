"""image_rebuild pipeline as a static graph_def.

Pure data — NO imports beyond typing — so the seed migration can import just
this dict without pulling in LangGraph / template_runner (migrations-smoke
runs in a light env). Mirrors ``services/seo_refresh_spec.py``.

Replaces the synchronous ``ImageRebuildService`` path (which ran the image
atoms in-process inside a worker HTTP request, blocking the CLI for the whole
render). ``poindexter tasks rebuild-images`` now enqueues a pipeline task with
``template_slug='image_rebuild'`` + ``task_metadata={target_task_id,
allow_stock}`` and returns immediately; the Prefect worker claims it and this
graph does the work — with per-atom ``atom_runs`` capture, retries, and
live-activity visibility the in-request path never had.

    load_draft (content.load_draft_for_image_rebuild)
        — hydrate content/topic/version from the target awaiting_approval
          draft's latest pipeline_versions row; strip existing <img> blocks;
          fail loud if the target is no longer awaiting_approval
      → plan (content.plan_image_markers)      — Image Decision Agent re-plans
                                                 [IMAGE-N] slots + hero plan
      → generate (content.generate_images)     — image-gen primary, Pexels
                                                 fallback, media_assets rows
      → featured (content.rebuild_featured_image) — same two-strategy for the
                                                 hero slot
      → gate (content.image_rebuild_gate)      — fail-loud: empty slots always
                                                 fail; stock slots fail unless
                                                 allow_stock. Raising leaves
                                                 the draft unchanged.
      → inject (content.inject_images)         — swap [IMAGE-N] markers for
                                                 <img> tags
      → persist (content.persist_draft_images) — write content + featured back
                                                 to the TARGET draft, bump
                                                 regen_images_attempts, audit
      → finalize (atoms.set_task_status)       — flip THIS rebuild job row to
                                                 'completed' (the target draft
                                                 stays awaiting_approval); without
                                                 it the job strands in_progress and
                                                 the stale-sweep re-runs it

Why no approval gate: the target draft is already ``awaiting_approval`` — the
operator's normal approve/reject decision on the draft (with its fresh images)
IS the human gate. The rebuild task itself is a utility run.
"""

from __future__ import annotations

from typing import Any

IMAGE_REBUILD_GRAPH_DEF: dict[str, Any] = {
    "name": "image_rebuild",
    "description": (
        "Rebuild every image (featured + inline) on an awaiting_approval draft "
        "by re-planning from the article text; fail-loud on stock fallback "
        "unless allow_stock."
    ),
    "entry": "load_draft",
    "nodes": [
        {"id": "load_draft", "atom": "content.load_draft_for_image_rebuild"},
        {"id": "plan", "atom": "content.plan_image_markers"},
        {"id": "generate", "atom": "content.generate_images"},
        {"id": "featured", "atom": "content.rebuild_featured_image"},
        {"id": "gate", "atom": "content.image_rebuild_gate"},
        {"id": "inject", "atom": "content.inject_images"},
        {"id": "persist", "atom": "content.persist_draft_images"},
        {
            "id": "finalize",
            "atom": "atoms.set_task_status",
            # The rebuild JOB row is otherwise orphaned at in_progress (the target
            # draft stays awaiting_approval, untouched by persist). 'completed' is
            # terminal, keeps the job out of the claim/stale-sweep/approval queries,
            # and post_pipeline_actions treats it as decided.
            "config": {"target_status": "completed", "percentage": 100},
        },
    ],
    "edges": [
        {"from": "load_draft", "to": "plan"},
        {"from": "plan", "to": "generate"},
        {"from": "generate", "to": "featured"},
        {"from": "featured", "to": "gate"},
        {"from": "gate", "to": "inject"},
        {"from": "inject", "to": "persist"},
        {"from": "persist", "to": "finalize"},
        {"from": "finalize", "to": "END"},
    ],
}

__all__ = ["IMAGE_REBUILD_GRAPH_DEF"]
