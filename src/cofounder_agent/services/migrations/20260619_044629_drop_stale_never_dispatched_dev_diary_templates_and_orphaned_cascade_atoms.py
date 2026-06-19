"""Migration: drop 7 stale never-dispatched pipeline_templates + 2 orphaned atoms.

Consolidation/deletion audit (Tier 4). The live system dispatches only three
templates — ``canonical_blog`` (1637 tasks), ``dev_diary`` (80), and
``seo_refresh`` (80). Seven other ``active=true`` rows have **never** produced a
single ``pipeline_tasks`` row and exist only as leftover experiments from the
pre-#355 dev_diary / content-pipeline iterations:

  content_publishing_pipeline  daily_dev_diary  daily_dev_diary_with_approval
  dev_diary_compose  dev_diary_prose  dev_diary_quality_check
  dev_diary_quality_pipeline

``content_publishing_pipeline`` is additionally broken: its graph_def references
``stage.cross_model_qa``, a stage whose code was deleted at the #355 atom-cutover.

Removing these rows closes the long-standing 12-active-templates-vs-4-code-specs
drift and de-references the only two atoms that were kept alive solely by these
dead templates — ``atoms.aggregate_reviews`` and ``atoms.run_validators`` (whose
module files are deleted in the same PR). Their rows in the ``pipeline_atoms``
catalog are pruned here too, because ``atom_registry.sync_to_db`` only ever
upserts (INSERT … ON CONFLICT DO UPDATE) and never deletes, so a removed atom's
row would otherwise linger with a frozen ``last_seen_at`` forever.

``atoms.review_with_critic`` is intentionally NOT removed: it survives as the
referenced prompt-resolver exemplar (5 callers cite it) with a registered
``atoms.review_with_critic.system_prompt`` and is a composable atom in the
architect catalog.

The matching seed INSERTs were removed from ``0000_baseline.seeds.sql`` so fresh
installs never create these rows; this migration cleans existing databases.

IMPORTANT: stdlib-only (no LangGraph / template_runner imports) so the
migrations-smoke CI step can apply it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_STALE_TEMPLATE_SLUGS = (
    "content_publishing_pipeline",
    "daily_dev_diary",
    "daily_dev_diary_with_approval",
    "dev_diary_compose",
    "dev_diary_prose",
    "dev_diary_quality_check",
    "dev_diary_quality_pipeline",
)

_ORPHANED_ATOM_NAMES = (
    "atoms.aggregate_reviews",
    "atoms.run_validators",
)


async def up(pool) -> None:
    """Delete the stale templates + orphaned atom catalog rows.

    Idempotent: ``DELETE … WHERE … IN (…)`` removes the rows on first run and
    is a no-op on every subsequent apply (and on fresh installs, where the
    baseline never seeded them).
    """
    async with pool.acquire() as conn:
        tmpl_result = await conn.execute(
            """
            DELETE FROM pipeline_templates
             WHERE slug = ANY($1::text[])
            """,
            list(_STALE_TEMPLATE_SLUGS),
        )
        atom_result = await conn.execute(
            """
            DELETE FROM pipeline_atoms
             WHERE name = ANY($1::text[])
            """,
            list(_ORPHANED_ATOM_NAMES),
        )
    logger.info(
        "Migration drop_stale_never_dispatched_dev_diary_templates: "
        "templates=%s atoms=%s",
        tmpl_result, atom_result,
    )


async def down(pool) -> None:
    """No-op revert.

    Re-creating these rows would restore never-dispatched (and, for
    content_publishing_pipeline, code-broken) config plus two atom files that no
    longer exist in the image. Operators who genuinely need them back should
    restore from a pipeline_templates row backup. Same posture as
    20260610_230000_drop_dead_qa_guardrails_node.
    """
    logger.warning(
        "Migration drop_stale_never_dispatched_dev_diary_templates down: no-op "
        "— refusing to re-seed never-dispatched / code-broken templates."
    )
