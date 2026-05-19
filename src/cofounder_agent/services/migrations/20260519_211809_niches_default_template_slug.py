"""Add ``niches.default_template_slug`` + backfill empty ``pipeline_tasks.template_slug``.

ISSUE: Glad-Labs/glad-labs-stack jank-audit finding #3 (2026-05-19).

Why
---

Multiple ``pipeline_tasks`` rows for niche ``glad-labs`` are landing
with ``status='failed'`` AND ``template_slug=''`` (or NULL) — see
``services/content_router_service.py`` which fails loud on a missing
slug per ``feedback_no_silent_defaults``. The downstream fail-loud
is correct; the upstream bug is that several inserters into
``pipeline_tasks`` were never updated for the Lane C cutover seam
in ``tasks_db.add_task``. The two main offenders:

- ``services/topic_batch_service.py::_handoff_to_pipeline`` —
  niche-driven topic-batch winner enqueue (the path that produces
  the "What Burnout Actually Feels Like" / "Building Sweets Vault"
  tasks we saw failing).
- ``services/topic_proposal_service.py::propose_topic`` — manual
  operator topic proposal (also bypassed the cutover seam).
- ``services/topic_discovery.py::queue_topics`` — legacy
  trend-scraping enqueue (still active fallback path).

This migration sets up the proper seam — a per-niche
``default_template_slug`` column — so the resolver chain becomes
(in order):

  1. explicit caller-supplied ``template_slug``
  2. ``niches.default_template_slug`` for the task's niche
  3. ``app_settings.default_template_slug`` (process-wide fallback)
  4. hard fail loud per ``feedback_no_silent_defaults``

Per ``feedback_filter_on_seams_not_slugs``: the niche table is the
structured seam for "which template should this niche's content
generate through?". No code path should infer a template from the
topic title or category any more.

Schema change
-------------

- ``niches.default_template_slug text`` — nullable; when set, every
  task created for this niche without an explicit caller-supplied
  slug uses this value. NULL falls through to
  ``app_settings.default_template_slug``.

Seeds
-----

- ``dev_diary``  → ``'dev_diary'``  (matches the existing dev_diary cron)
- ``glad-labs`` → ``'canonical_blog'`` (matches the prod app_settings default)

Legacy backfill
---------------

Existing ``pipeline_tasks`` rows where ``template_slug IS NULL`` or
``template_slug = ''``:

- if ``niche_slug = 'dev_diary'`` → set to ``'dev_diary'``
- otherwise                      → set to ``'canonical_blog'``

These are historical rows the worker has already finished with (most
are in terminal states like ``failed`` / ``awaiting_approval`` /
``published``); we backfill them so the post-migration invariant
"every ``pipeline_tasks`` row has a non-empty ``template_slug``"
holds across the whole table, which makes downstream reporting +
``content_router_service`` debugging easier.

Idempotent — ``ADD COLUMN IF NOT EXISTS``, ``UPDATE ... WHERE
default_template_slug IS NULL`` on the niche seeds so operator
overrides survive a replay, and the legacy backfill is gated on
``template_slug IS NULL OR template_slug = ''`` so it doesn't stomp
rows that already have a slug.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Add the column (idempotent).
            await conn.execute(
                """
                ALTER TABLE niches
                ADD COLUMN IF NOT EXISTS default_template_slug text
                """
            )

            # 2. Seed per-niche defaults. Use WHERE
            #    default_template_slug IS NULL so a replay never
            #    clobbers an operator-tuned value.
            await conn.execute(
                """
                UPDATE niches
                   SET default_template_slug = 'dev_diary'
                 WHERE slug = 'dev_diary'
                   AND default_template_slug IS NULL
                """
            )
            await conn.execute(
                """
                UPDATE niches
                   SET default_template_slug = 'canonical_blog'
                 WHERE slug = 'glad-labs'
                   AND default_template_slug IS NULL
                """
            )

            # 3. Backfill legacy pipeline_tasks rows. Two passes —
            #    dev_diary first (the more-specific rule), then the
            #    catch-all for everything else.
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET template_slug = 'dev_diary'
                 WHERE niche_slug = 'dev_diary'
                   AND (template_slug IS NULL OR template_slug = '')
                """
            )
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET template_slug = 'canonical_blog'
                 WHERE (niche_slug IS DISTINCT FROM 'dev_diary')
                   AND (template_slug IS NULL OR template_slug = '')
                """
            )

    logger.info(
        "[migration] niches.default_template_slug column added + "
        "seeded for dev_diary/glad-labs; legacy pipeline_tasks rows "
        "with empty template_slug backfilled by niche."
    )
