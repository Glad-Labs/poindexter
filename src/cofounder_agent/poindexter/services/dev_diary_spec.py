"""dev_diary pipeline as a static graph_def.

Pure data — NO imports beyond typing — so the seed migration can import just
this dict without pulling in LangGraph / template_runner (migrations-smoke runs
in a light env). Mirrors ``services/image_rebuild_spec.py``.

    verify_task            (stage.verify_task)          — shared with canonical_blog
    narrate_bundle         (atoms.narrate_bundle)       — dev_diary's own writer
    seo_all_metadata       (seo.generate_all_metadata)  — shared with canonical_blog
    source_featured_image  (stage.source_featured_image)— shared with canonical_blog
    compile_meta           (content.compile_meta)       — shared with canonical_blog
    persist_task           (content.persist_task)       — shared with canonical_blog
    record_pipeline_version(content.record_pipeline_version) — shared
    evaluate_auto_publish  (content.evaluate_auto_publish)   — shared

**Why this spec exists: every node except the writer is now the same node
canonical_blog runs.** Before this, dev_diary's stored graph_def used the coarse
``stage.finalize_task`` and had no SEO node at all, so improvements landing on
the canonical atoms were invisible to the dev blog. That was not theoretical:

* The 2026-06-02 "empty SERP snippet" fix added ``generate_seo_metadata`` to the
  legacy ``TEMPLATES`` **factory** — but by then the factory no longer ran
  (``pipeline_use_graph_def=true`` + an active dev_diary graph_def row wins in
  ``TemplateRunner.run``). The stored graph_def never got the node, so dev_diary
  posts kept shipping with no ``<meta description>``: 5/25 missing in May,
  13/25 in June, 15/25 in July, **5/5 in August**, against 0/40 for
  canonical_blog. A green unit test (``test_dev_diary_seo.py``) asserted the
  node existed — on the factory, i.e. on dead code.
* ``seo.generate_all_metadata`` is not merely "the SEO node": it is one
  structured call, draft-grounded, and carries the topic-echo guard from
  ``atoms/_seo_common.py``. The old ``stages/generate_seo_metadata.py`` reached
  none of that — it imports ``services.seo_content_generator`` +
  ``utils.title_utils.derive_seo_title`` and never touches ``_seo_common``. The
  dev_diary topic is literally ``"Daily dev diary — <date> (N PRs, M commits)"``,
  exactly the raw-directive string that guard exists to keep out of ``seo_title``.

Behaviour deliberately PRESERVED from the ``stage.finalize_task`` it replaces:

* ``content.persist_task`` writes ``status='awaiting_approval'`` +
  ``approval_status='pending'`` — same terminal state, so per-post operator
  sign-off is unchanged (``feedback_human_approval``).
* ``content.evaluate_auto_publish`` is included **because
  ``stage.finalize_task`` ran the auto-publish gate inline** (it imports
  ``auto_publish_gate.evaluate`` directly). dev_diary has live opt-in keys
  (``dev_diary_auto_publish_threshold=69``, ``_dry_run=false``,
  ``_min_clean_runs=3``), so omitting this node would have silently revoked a
  capability the operator had turned on.

Deliberately NOT adopted from canonical_blog:

* The 14 ``qa.*`` rails. A build-in-public status report has no external claims
  to fact-check; the QA block exists for researched articles.
* ``social.generate_drafts``. Adding it would start generating Postiz promos for
  dev diaries — a new outward-facing behaviour, not a migration. Available as a
  one-line addition whenever that's wanted.
* The image block (``plan_image_markers`` → ``generate_images`` →
  ``inject_images``) and ``caption_images``. dev_diary takes a hero image only.
* ``preview_gate`` / ``draft_gate``. Both are seeded disabled on canonical_blog
  anyway; adding dormant gates here would only add nodes.
"""

from __future__ import annotations

from typing import Any

DEV_DIARY_GRAPH_DEF: dict[str, Any] = {
    "name": "dev_diary",
    "description": (
        "dev_diary pipeline (shared-atom composed): verify_task -> narrate_bundle "
        "-> seo.generate_all_metadata -> source_featured_image -> compile_meta -> "
        "persist_task -> record_pipeline_version -> evaluate_auto_publish. Every "
        "node but the writer is the same atom canonical_blog runs; no QA rails by "
        "design (a status report makes no external claims)."
    ),
    "entry": "verify_task",
    "nodes": [
        {"id": "verify_task", "atom": "stage.verify_task"},
        # The one genuinely dev_diary-specific node: a single LLM call over the
        # preserved PR/commit bundle. Produces content + title + model_used.
        {"id": "narrate_bundle", "atom": "atoms.narrate_bundle"},
        # Runs over the narrated prose (requires content + topic, both present:
        # every dev_diary task row carries a non-empty topic). Restores the
        # <meta description> these posts lost, via the shared atom rather than a
        # second implementation.
        {"id": "seo_all_metadata", "atom": "seo.generate_all_metadata"},
        # Additive only — writes featured_image_url/_data, never touches prose.
        {"id": "source_featured_image", "atom": "stage.source_featured_image"},
        # stage.finalize_task decomposition, matching canonical_blog's chain.
        {"id": "compile_meta", "atom": "content.compile_meta"},
        {"id": "persist_task", "atom": "content.persist_task"},
        {"id": "record_pipeline_version", "atom": "content.record_pipeline_version"},
        # Preserves the auto-publish evaluation stage.finalize_task did inline.
        {"id": "evaluate_auto_publish", "atom": "content.evaluate_auto_publish"},
    ],
    "edges": [
        {"from": "verify_task", "to": "narrate_bundle"},
        {"from": "narrate_bundle", "to": "seo_all_metadata"},
        {"from": "seo_all_metadata", "to": "source_featured_image"},
        {"from": "source_featured_image", "to": "compile_meta"},
        {"from": "compile_meta", "to": "persist_task"},
        {"from": "persist_task", "to": "record_pipeline_version"},
        {"from": "record_pipeline_version", "to": "evaluate_auto_publish"},
        {"from": "evaluate_auto_publish", "to": "END"},
    ],
}

__all__ = ["DEV_DIARY_GRAPH_DEF"]
