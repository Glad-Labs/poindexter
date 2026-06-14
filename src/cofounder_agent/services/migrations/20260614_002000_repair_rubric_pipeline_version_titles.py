"""Migration: repair rubric/reasoning-leak titles in pipeline_versions.

Backfill for the canonical_blog stored-title bug. The title-generation LLM
occasionally emitted a rubric/reasoning bullet ("<label>: They/These <verb>…")
describing what good titles do, instead of a real headline. The #1280
junk-guard's denylist + length cap let novel/short variants through (e.g.
"Tone:" / "Neutral Tone:" labels, sub-90-char), so the raw string was stored
as ``pipeline_versions.title`` and shown on the preview / approval-queue
surfaces (the ``content_tasks`` view reads ``pv.title``).

The PUBLISHED posts rendered the CORRECT title regardless — ``posts.title`` is
derived independently from the body H1 by
``publish_service.publish_post_from_task`` (NOT from the stored title) — so for
the affected published rows ``posts.title`` is the authoritative clean
headline. This migration copies it back over the garbage
``pipeline_versions.title`` so the preview surface matches the live post.

Scope: canonical_blog rows linked to a published post whose stored title still
matches the rubric shape. At authoring time this is exactly 2 rows (tasks
b14b8413…, 433d67bd…, published 2026-06-10). Pattern-based + the published-post
join, so it is:
  - idempotent — repaired titles no longer match the pattern, re-running no-ops;
  - self-limiting — only rows with an authoritative ``posts.title`` are touched
    (rejected-task rows are left as-is; their inert titles never publish).

The going-forward fix ships in the same change
(``services/title_generation.py::_RUBRIC_REASONING_RE``).

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# POSIX mirror of services.title_generation._RUBRIC_REASONING_RE. Case-sensitive
# (~): the meta-pronoun is always sentence-capitalised in the captured data, and
# the lowercase ``[a-z]`` immediately after it is the discriminator separating a
# rubric clause ("These avoid…") from a legitimate title-cased subtitle
# ("RTX 5090: These Are…").
_REPAIR = """
    UPDATE pipeline_versions pv
    SET title = p.title
    FROM posts p, pipeline_tasks pt
    WHERE p.metadata->>'pipeline_task_id' = pv.task_id::text
      AND pt.task_id::text = pv.task_id::text
      AND pt.template_slug = 'canonical_blog'
      AND p.title IS NOT NULL
      AND length(trim(p.title)) > 0
      AND pv.title ~ '^([^:]{1,40}:[[:space:]]+)?(They|These)[[:space:]]+[a-z]'
    RETURNING pv.task_id
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_REPAIR)
    logger.info(
        "Migration repair_rubric_pipeline_version_titles: repaired %d "
        "pipeline_versions title(s) from authoritative posts.title",
        len(rows),
    )


async def down(pool) -> None:
    # Intentionally irreversible: the prior values were corrupted rubric strings
    # we never want to restore. Re-running ``up`` is safe (idempotent).
    logger.info(
        "Migration repair_rubric_pipeline_version_titles down: no-op "
        "(refusing to restore corrupted titles)"
    )
