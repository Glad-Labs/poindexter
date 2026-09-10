"""Retarget ``pipeline_distributions`` own-site rows onto a ``'site'`` sentinel.

ISSUE: Glad-Labs/poindexter#1038

``pipeline_distributions.target`` names WHERE an artifact of a task landed.
Every value is a platform — ``youtube``, and whatever surfaces come next — with
one exception: the row recording that the post itself went live on the site
this install publishes. That row was stamped with the literal ``'gladlabs.io'``,
the source operator's own domain, hardcoded at three write sites
(``routes/task_publishing_routes.py`` ×2, ``modules/content/auto_publish.py``)
and matched by name on five read sites (the ``content_tasks`` and
``pipeline_tasks_view`` view bodies, three correlated subqueries each; the
``distribution_yield`` placements query; two Grafana panels on Cost &
Analytics).

Nothing was broken by it — the literal matched on both the write and the read
side, so a fresh OSS install worked — but it is operator identity baked into
public-mirror code. Every fork's own-site rows carried one operator's brand, and
the "not the site itself" filters excluded that one domain by name, which is
only true by coincidence on any other install. It survived
``scripts/ci/check_public_mirror_safety.py`` because that guard's ``gladlabs.io``
rule is scoped to a SQL ``VALUES`` tuple (a seeded default), and this is a
kwarg and a ``WHERE`` predicate. A companion rule now covers the target shape.

**Why a sentinel and not the operator's real domain.** The obvious alternative
is to write ``app_settings.site_domain`` into the column. It does not work: the
read side is a VIEW, and a view cannot consult a settings row to build its own
predicate. It is also the wrong model — the row is recording a *relationship*
("this task published to self"), not a hostname, and the hostname can change
without any of these rows meaning something different.

Two steps, both idempotent, and safe to run against a fresh install where the
baseline already created the widened views:

1. Retarget the legacy own-site rows onto ``'site'``. Bound parameters, not an
   f-string, so the operator literal never appears in a SQL predicate that the
   mirror-safety guard would have to carve an exception for. Prod carries 179
   such rows, all ``medium='default'``, ``status='published'``, with no
   task holding both spellings — so the unique key ``(task_id, target, medium)``
   cannot be violated. The ``NOT EXISTS`` guard makes that a checked fact rather
   than an assumption: on any DB where a task somehow holds both, the legacy row
   is left alone and the readers' back-compat resolves it.

2. Widen both view bodies to accept either spelling. The bodies are NOT pasted
   into this file — they are read back with ``pg_get_viewdef`` and transformed,
   then re-issued through ``CREATE OR REPLACE VIEW``. A pasted copy would be a
   second definition of a view whose real definition lives in
   ``0000_baseline.schema.sql``, and it would rot the first time that one
   changes; a transform adapts to whatever the view actually is. ``CREATE OR
   REPLACE`` preserves the three ``INSTEAD OF`` triggers on ``content_tasks``
   because the column list is untouched.

**The readers keep accepting the legacy spelling** rather than switching to the
sentinel outright. Writers only ever produce ``'site'`` from here, so the legacy
value can only re-appear from pre-cutover code — a DB restored from an older
dump, or a task published inside a rollback window. Those rows should still
resolve their post_id / post_slug / published_at instead of silently reading as
an unpublished task.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# The value this code wrote before the cutover, and the sentinel that replaces
# it. Deliberately literals in THIS file rather than an import from
# ``services.pipeline_db``: a migration is a historical artifact and has to keep
# meaning the same thing after that module is renamed or its constants change.
_LEGACY_TARGET = "gladlabs.io"
_SITE_TARGET = "site"

# Only rows that cannot collide with an already-migrated row. `medium` is part
# of the unique key, so the guard has to match on it too.
_RETARGET_ROWS_SQL = """
    UPDATE pipeline_distributions pd
       SET target = $1
     WHERE pd.target = $2
       AND NOT EXISTS (
             SELECT 1 FROM pipeline_distributions other
              WHERE other.task_id = pd.task_id
                AND other.target = $1
                AND other.medium = pd.medium
           )
"""

# Surgery on a machine-generated definition, so the patterns must not depend on
# how Postgres chose to render the surrounding expression. They deliberately
# match only the LITERAL and the operator around it, never the left-hand side:
# ``pg_get_viewdef(..., pretty := true)`` writes ``pd.target::text`` where the
# unpretty form writes ``(pd.target)::text``, and pinning either one is how this
# transform would quietly match nothing and report success.
#
# ``x = 'legacy'::text`` becomes ``x = ANY (ARRAY['site'::text,
# 'legacy'::text])`` whatever ``x`` is spelled as.
_LEGACY_LITERAL_RE = re.compile(
    rf"'{re.escape(_LEGACY_TARGET)}'(?:::(?:text|character varying))?"
)
_WIDENED_PREDICATE = (
    f"ANY (ARRAY['{_SITE_TARGET}'::text, '{_LEGACY_TARGET}'::text])"
)

# The inverse: collapse an ANY(ARRAY[...]) carrying the legacy value back to a
# plain equality, however the array's elements ended up cast.
_WIDENED_PREDICATE_RE = re.compile(
    rf"=\s*ANY\s*\(\s*ARRAY\[[^\]]*'{re.escape(_LEGACY_TARGET)}'[^\]]*\]\s*\)"
)

# Both views project the same three own-site columns (post_id, post_slug,
# published_at) from the same correlated subqueries.
_VIEWS = ("content_tasks", "pipeline_tasks_view")


async def _view_def(conn, view: str) -> str | None:
    """This view's body as Postgres renders it, or ``None`` if it is absent."""
    return await conn.fetchval(
        "SELECT pg_get_viewdef($1::regclass, true)", f"public.{view}"
    )


async def _widen_view(conn, view: str) -> int:
    """Make one view's own-site predicate accept the sentinel. Returns hits."""
    definition = await _view_def(conn, view)
    if definition is None:
        # A DB that predates the view entirely. Nothing to widen — the baseline
        # that creates it creates the widened form.
        logger.warning("view %s not present — skipping own-site predicate widen", view)
        return 0

    if _WIDENED_PREDICATE_RE.search(definition):
        # Fresh install: the baseline created this view already widened. Not a
        # missing match — an already-correct one.
        return 0

    widened, hits = _LEGACY_LITERAL_RE.subn(_WIDENED_PREDICATE, definition)
    if hits == 0:
        # The view exists, is not already widened, and carries no legacy
        # literal. Either it never referenced pipeline_distributions or the
        # rendering changed under us. Loud, because a transform that matched
        # nothing has not run.
        raise RuntimeError(
            f"view {view} carries neither the legacy own-site literal nor the "
            f"widened predicate — refusing to report a no-op as success"
        )

    await conn.execute(
        f"CREATE OR REPLACE VIEW public.{view} AS " + widened
    )  # nosec B608 - view name is a hardcoded constant; the body is Postgres' own pg_get_viewdef output
    return hits


async def up(pool) -> None:
    async with pool.acquire() as conn:
        retargeted = await conn.execute(
            _RETARGET_ROWS_SQL, _SITE_TARGET, _LEGACY_TARGET
        )
        widened = {view: await _widen_view(conn, view) for view in _VIEWS}

        stranded = await conn.fetchval(
            "SELECT count(*) FROM pipeline_distributions WHERE target = $1",
            _LEGACY_TARGET,
        )

    if stranded:
        # Not a failure: the readers accept both spellings, so these rows still
        # resolve. Loud anyway — it means a task holds the sentinel AND the
        # legacy value, which the write path can no longer produce.
        logger.warning(
            "Migration own-site distribution sentinel: %s row(s) still carry the "
            "legacy target because the same (task_id, medium) already holds the "
            "sentinel. They remain readable via the widened views.",
            stranded,
        )

    logger.info(
        "Migration own-site distribution sentinel: rows %s; view predicates widened %s",
        retargeted, widened,
    )


async def down(pool) -> None:
    """Restore the operator-domain literal on both the rows and the views.

    A true inverse: nothing was dropped and no row is ambiguous, because the
    forward pass only ever moved rows that had no sentinel twin. The views go
    back to matching a single value, which is safe in this direction — every
    own-site row carries the legacy spelling again by the time the view is
    narrowed.
    """
    async with pool.acquire() as conn:
        reverted = await conn.execute(
            _RETARGET_ROWS_SQL, _LEGACY_TARGET, _SITE_TARGET
        )
        for view in _VIEWS:
            definition = await _view_def(conn, view)
            if definition is None:
                continue
            narrowed, hits = _WIDENED_PREDICATE_RE.subn(
                f"= '{_LEGACY_TARGET}'::text", definition
            )
            if hits:
                await conn.execute(
                    f"CREATE OR REPLACE VIEW public.{view} AS " + narrowed
                )  # nosec B608 - view name is a hardcoded constant; the body is Postgres' own pg_get_viewdef output
    logger.warning(
        "Migration own-site distribution sentinel rolled back: %s row(s) retargeted "
        "to the operator-domain literal and both views narrowed back to it.",
        reverted,
    )
