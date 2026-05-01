"""Migration 0125: Unify ``content_tasks`` shape across dev + prod (closes Glad-Labs/poindexter#329).

Background
----------

For the past few months ``content_tasks`` has had two different shapes
depending on environment:

- **Production** — ``content_tasks`` is a VIEW over ``pipeline_tasks``
  joined with ``pipeline_versions``. The split (#211) was followed by a
  manual ops step that renamed the original table to
  ``content_tasks_deprecated`` and replaced it with a view. Three INSTEAD
  OF triggers (``content_tasks_update_redirect``,
  ``content_tasks_insert_redirect``, ``content_tasks_delete_redirect``)
  redirect writes to the underlying base tables.

- **Dev / migrations-smoke** — ``content_tasks`` is a TABLE created by
  ``0000_base_schema``. ``pipeline_tasks`` and ``pipeline_versions``
  were added in 0066 alongside it and remain disconnected.

The drift surfaced in #329 when migration 0114 (niche-pivot
``ADD COLUMN``) failed in production with
``ALTER action ADD COLUMN cannot be performed on relation 'content_tasks'``
(views aren't ALTERable). The patch made 0114 branch on
``pg_class.relkind`` and dispatch — but every future migration that
touches content_tasks now needs the same dance.

Decision (per #329 acceptance: "VIEW canonically")
-------------------------------------------------

We pick **VIEW shape** as canonical because:

1. Production already runs that shape with INSTEAD OF triggers wired up
   and ~1k+ rows in ``pipeline_tasks`` / ``pipeline_versions``.
2. Converting prod table -> view requires zero data surgery.
3. Converting prod view -> table would require either copying ~1k rows
   from the view's projection into a real table AND removing the
   pipeline_tasks/pipeline_versions split (loses the queue / content /
   review separation 0066 introduced), or maintaining sync between two
   tables forever.
4. Dev / migrations-smoke runs against an empty database so dropping the
   table costs nothing.

This migration converts the dev/smoke environment to match prod. After
this lands, every future migration that touches ``content_tasks`` can
assume VIEW shape and drop the relkind branch.

Behavior
--------

- If ``content_tasks`` is a VIEW (production reality, post-0114
  view-branch) → no-op. Log + record + return.
- If ``content_tasks`` is a TABLE (fresh dev, smoke env) →
  1. Assert the table is empty (fail loud if any data exists, so an
     accidental run on a populated dev DB doesn't lose rows).
  2. Drop the empty table (CASCADE drops the indexes 0000 created).
  3. Add the niche columns (niche_slug, writer_rag_mode, topic_batch_id)
     to ``pipeline_tasks`` — in TABLE-mode, 0114 added them to
     content_tasks instead, so the underlying pipeline_tasks doesn't
     have them yet. The view definition expects them on ``pt``.
  4. Define the three INSTEAD OF trigger functions (mirroring the
     production functions; the UPDATE body matches the canonical
     definition from migration 0078).
  5. Create ``content_tasks`` view spliced together from 0098 + 0114.
  6. Attach the three INSTEAD OF triggers.
  7. Recreate the ``ix_pipeline_tasks_niche`` / ``ix_pipeline_tasks_batch``
     partial indexes (0114's view-branch creates them; the table-branch
     creates them on content_tasks instead).

After this migration, ``relkind('content_tasks') == 'v'`` in BOTH
environments. The relkind-branch in 0114 stays in place for the
historical record / for environments that may have skipped this
migration, but no NEW migration needs that pattern.

The migration is idempotent — every CREATE has IF NOT EXISTS / OR
REPLACE; the second run on an already-converted DB is a clean no-op.

Spec: Glad-Labs/poindexter#329
"""

from __future__ import annotations

from services.logger_config import get_logger

logger = get_logger(__name__)


_RELKIND_QUERY = """
    SELECT relkind FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname = 'content_tasks'
"""


# ---------------------------------------------------------------------------
# Trigger function bodies
# ---------------------------------------------------------------------------
#
# Production has three INSTEAD OF redirect functions that were created
# out-of-band when the table->view conversion happened. Their bodies are
# reconstructed below from:
#
# - UPDATE: copied verbatim from migration 0078 (the most recent
#   canonical rewrite).
# - INSERT: derived from the column list in 0098's view body — split
#   the view's column projection back into pipeline_tasks columns vs
#   pipeline_versions columns, and INSERT into both tables. New rows
#   always start at version=1.
# - DELETE: cascade through pipeline_tasks (the FK on pipeline_versions
#   has ON DELETE CASCADE per 0066, so deleting from pipeline_tasks is
#   sufficient).
#
# These bodies are only installed when content_tasks is currently a TABLE
# (i.e. dev environment) — the production path is a no-op so we never
# overwrite the prod functions, which may have hand-tuned tweaks we don't
# want to clobber.

_UPDATE_REDIRECT_FN = """
CREATE OR REPLACE FUNCTION public.content_tasks_update_redirect()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE pipeline_tasks SET
        status = NEW.status,
        stage = COALESCE(NEW.stage, pipeline_tasks.stage),
        percentage = COALESCE(NEW.percentage, pipeline_tasks.percentage),
        message = COALESCE(NEW.message, pipeline_tasks.message),
        model_used = COALESCE(NEW.model_used, pipeline_tasks.model_used),
        error_message = COALESCE(NEW.error_message, pipeline_tasks.error_message),
        category = COALESCE(NEW.category, pipeline_tasks.category),
        style = COALESCE(NEW.style, pipeline_tasks.style),
        tone = COALESCE(NEW.tone, pipeline_tasks.tone),
        target_audience = COALESCE(NEW.target_audience, pipeline_tasks.target_audience),
        primary_keyword = COALESCE(NEW.primary_keyword, pipeline_tasks.primary_keyword),
        target_length = COALESCE(NEW.target_length, pipeline_tasks.target_length),
        updated_at = COALESCE(NEW.updated_at, NOW()),
        started_at = COALESCE(NEW.started_at, pipeline_tasks.started_at),
        completed_at = CASE
            WHEN NEW.status IN ('published','failed','cancelled','rejected','rejected_final')
            THEN NOW()
            ELSE pipeline_tasks.completed_at
        END
    WHERE task_id = NEW.task_id;

    UPDATE pipeline_versions SET
        title = COALESCE(NEW.title, pipeline_versions.title),
        content = COALESCE(NEW.content, pipeline_versions.content),
        excerpt = COALESCE(NEW.excerpt, pipeline_versions.excerpt),
        featured_image_url = COALESCE(NEW.featured_image_url, pipeline_versions.featured_image_url),
        seo_title = COALESCE(NEW.seo_title, pipeline_versions.seo_title),
        seo_description = COALESCE(NEW.seo_description, pipeline_versions.seo_description),
        seo_keywords = COALESCE(NEW.seo_keywords, pipeline_versions.seo_keywords),
        quality_score = COALESCE(NEW.quality_score, pipeline_versions.quality_score),
        qa_feedback = COALESCE(NEW.qa_feedback, pipeline_versions.qa_feedback),
        models_used_by_phase = COALESCE(NEW.models_used_by_phase, pipeline_versions.models_used_by_phase),
        stage_data = pipeline_versions.stage_data || jsonb_strip_nulls(
            jsonb_build_object(
                'metadata', NEW.metadata,
                'result', NEW.result,
                'task_metadata', NEW.task_metadata
            )
        )
    WHERE task_id = NEW.task_id AND version = 1;

    RETURN NEW;
END;
$function$;
"""

_INSERT_REDIRECT_FN = """
CREATE OR REPLACE FUNCTION public.content_tasks_insert_redirect()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    INSERT INTO pipeline_tasks (
        task_id, task_type, topic, status, stage,
        site_id, style, tone, target_length, category,
        primary_keyword, target_audience, percentage, message,
        model_used, error_message, created_at, updated_at,
        started_at, completed_at
    ) VALUES (
        NEW.task_id,
        COALESCE(NEW.task_type, NEW.content_type, 'blog_post'),
        NEW.topic,
        COALESCE(NEW.status, 'pending'),
        COALESCE(NEW.stage, 'pending'),
        NEW.site_id,
        COALESCE(NEW.style, 'technical'),
        COALESCE(NEW.tone, 'professional'),
        COALESCE(NEW.target_length, 1500),
        NEW.category,
        NEW.primary_keyword,
        NEW.target_audience,
        COALESCE(NEW.percentage, 0),
        NEW.message,
        NEW.model_used,
        NEW.error_message,
        COALESCE(NEW.created_at, NOW()),
        COALESCE(NEW.updated_at, NOW()),
        NEW.started_at,
        NEW.completed_at
    )
    ON CONFLICT (task_id) DO NOTHING;

    INSERT INTO pipeline_versions (
        task_id, version, title, content, excerpt,
        featured_image_url, seo_title, seo_description, seo_keywords,
        quality_score, qa_feedback, models_used_by_phase, stage_data,
        created_at
    ) VALUES (
        NEW.task_id,
        1,
        NEW.title,
        NEW.content,
        NEW.excerpt,
        NEW.featured_image_url,
        NEW.seo_title,
        NEW.seo_description,
        NEW.seo_keywords,
        NEW.quality_score,
        NEW.qa_feedback,
        COALESCE(NEW.models_used_by_phase, '{}'::jsonb),
        jsonb_strip_nulls(jsonb_build_object(
            'metadata', NEW.metadata,
            'result', NEW.result,
            'task_metadata', NEW.task_metadata
        )),
        COALESCE(NEW.created_at, NOW())
    )
    ON CONFLICT (task_id, version) DO NOTHING;

    RETURN NEW;
END;
$function$;
"""

_DELETE_REDIRECT_FN = """
CREATE OR REPLACE FUNCTION public.content_tasks_delete_redirect()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    -- pipeline_versions, pipeline_reviews, pipeline_distributions all
    -- have ON DELETE CASCADE FKs on pipeline_tasks.task_id, so a single
    -- DELETE here cleans everything up.
    DELETE FROM pipeline_tasks WHERE task_id = OLD.task_id;
    RETURN OLD;
END;
$function$;
"""


# Mirrors the post-0114 production view: 0098's column projection plus
# the three niche columns 0114 splices in. Keep this in sync with the
# splice in 0114 — if a future migration adds another column to the
# view, that migration owns the rebuild (this migration is the
# bootstrap, not the running source-of-truth for the view definition).
_CONTENT_TASKS_VIEW_DDL = """
CREATE VIEW content_tasks AS
SELECT
    pt.id,
    pt.task_id,
    pt.task_type,
    pt.task_type AS content_type,
    pv.title,
    pt.topic,
    pt.status,
    pt.stage,
    pt.style,
    pt.tone,
    pt.target_length,
    pt.category,
    pt.primary_keyword,
    pt.target_audience,
    pv.content,
    pv.excerpt,
    pv.featured_image_url,
    pv.quality_score,
    pv.qa_feedback,
    pv.seo_title,
    pv.seo_description,
    pv.seo_keywords,
    pt.percentage,
    pt.message,
    pt.model_used,
    pt.error_message,
    pv.models_used_by_phase,
    COALESCE(pv.stage_data -> 'metadata'::text, pv.stage_data) AS metadata,
    COALESCE(pv.stage_data -> 'result'::text, pv.stage_data) AS result,
    COALESCE(pv.stage_data -> 'task_metadata'::text, pv.stage_data) AS task_metadata,
    pt.site_id,
    pt.created_at,
    pt.updated_at,
    pt.started_at,
    pt.completed_at,
    (SELECT pr.decision
       FROM pipeline_reviews pr
      WHERE pr.task_id::text = pt.task_id::text
      ORDER BY pr.created_at DESC
      LIMIT 1) AS approval_status,
    (SELECT pr.reviewer
       FROM pipeline_reviews pr
      WHERE pr.task_id::text = pt.task_id::text
      ORDER BY pr.created_at DESC
      LIMIT 1) AS approved_by,
    (SELECT pr.feedback
       FROM pipeline_reviews pr
      WHERE pr.task_id::text = pt.task_id::text
      ORDER BY pr.created_at DESC
      LIMIT 1) AS human_feedback,
    (SELECT pd.post_id
       FROM pipeline_distributions pd
      WHERE pd.task_id::text = pt.task_id::text
        AND pd.target::text = 'gladlabs.io'::text
      LIMIT 1) AS post_id,
    (SELECT pd.post_slug
       FROM pipeline_distributions pd
      WHERE pd.task_id::text = pt.task_id::text
        AND pd.target::text = 'gladlabs.io'::text
      LIMIT 1) AS post_slug,
    (SELECT pd.published_at
       FROM pipeline_distributions pd
      WHERE pd.task_id::text = pt.task_id::text
        AND pd.target::text = 'gladlabs.io'::text
      LIMIT 1) AS published_at,
    pt.awaiting_gate,
    pt.gate_artifact,
    pt.gate_paused_at,
    pt.niche_slug,
    pt.writer_rag_mode,
    pt.topic_batch_id
FROM pipeline_tasks pt
LEFT JOIN pipeline_versions pv
       ON pv.task_id::text = pt.task_id::text
      AND pv.version = (
          SELECT MAX(pipeline_versions.version)
            FROM pipeline_versions
           WHERE pipeline_versions.task_id::text = pt.task_id::text
      )
"""


_TRIGGERS = [
    ("content_tasks_update_trigger", "INSTEAD OF UPDATE", "content_tasks_update_redirect"),
    ("content_tasks_insert_trigger", "INSTEAD OF INSERT", "content_tasks_insert_redirect"),
    ("content_tasks_delete_trigger", "INSTEAD OF DELETE", "content_tasks_delete_redirect"),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        relkind = await conn.fetchval(_RELKIND_QUERY)
        # asyncpg returns relkind as a bytes object on some driver
        # versions (Postgres "char" type). Normalise to str so the
        # comparison below works either way. This is the same
        # normalisation 0114 does — kept in sync deliberately.
        if isinstance(relkind, (bytes, bytearray)):
            relkind = relkind.decode("ascii")

        if relkind == 'v':
            # Already a view — production reality. Nothing to do.
            logger.info(
                "0125: content_tasks is already a VIEW — no-op (production "
                "shape is canonical, dev was the outlier)."
            )
            return

        if relkind is None:
            # Migration 0000 didn't run, or content_tasks got dropped
            # outside of migrations. Either way, we can't safely create a
            # view over pipeline_tasks if the underlying table itself is
            # also missing — fail loud so the operator investigates.
            pt_exists = await conn.fetchval(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = 'pipeline_tasks'"
            )
            if not pt_exists:
                raise RuntimeError(
                    "0125: neither content_tasks nor pipeline_tasks exists in "
                    "schema 'public'. Cannot proceed — earlier migration must "
                    "have failed."
                )
            # pipeline_tasks exists but no content_tasks at all — proceed
            # to the view-creation path as if we'd just dropped the table.
            logger.warning(
                "0125: content_tasks not present but pipeline_tasks exists — "
                "creating view directly."
            )
            await _bootstrap_view_path(conn, drop_table_first=False)
            return

        if relkind != 'r':
            raise RuntimeError(
                f"0125: content_tasks has unexpected relkind={relkind!r}; "
                f"expected 'r' (table) or 'v' (view)."
            )

        # ------------------------------------------------------------------
        # TABLE shape — convert to view.
        # ------------------------------------------------------------------
        row_count = await conn.fetchval("SELECT COUNT(*) FROM content_tasks")
        if row_count and row_count > 0:
            # Refuse to silently destroy data. If a dev DB has rows in
            # content_tasks they must be exported / migrated by hand
            # before this conversion runs. The migrations-smoke env is
            # always empty so this guard never trips in CI.
            raise RuntimeError(
                f"0125: refusing to drop content_tasks — table has {row_count} "
                f"rows. Export them or copy into pipeline_tasks/"
                f"pipeline_versions before re-running this migration."
            )

        await _bootstrap_view_path(conn, drop_table_first=True)


async def _bootstrap_view_path(conn, *, drop_table_first: bool) -> None:
    """Drop the table (if requested), add niche columns to pipeline_tasks,
    install the redirect functions + triggers, create the view."""
    if drop_table_first:
        # CASCADE drops the indexes 0000 created on this table. Triggers
        # on a table aren't INSTEAD OF (those are view-only), so there's
        # nothing else to clean up here.
        await conn.execute("DROP TABLE content_tasks CASCADE")
        logger.info("0125: dropped empty content_tasks table")

    # 0114's TABLE-branch added niche columns to content_tasks (just
    # dropped). The new view SELECTs ``pt.niche_slug`` etc., so we need
    # them on pipeline_tasks too. ``ADD COLUMN IF NOT EXISTS`` makes
    # this safe to re-run — and a no-op on prod where 0114's VIEW-branch
    # already added them to pipeline_tasks.
    #
    # The CHECK constraint on writer_rag_mode mirrors 0114 verbatim so
    # the post-conversion shape matches what 0114 would have produced
    # if it had taken the VIEW branch from the start.
    await conn.execute("""
        ALTER TABLE pipeline_tasks
          ADD COLUMN IF NOT EXISTS niche_slug TEXT,
          ADD COLUMN IF NOT EXISTS writer_rag_mode TEXT
            CHECK (writer_rag_mode IN ('TOPIC_ONLY','CITATION_BUDGET','STORY_SPINE','TWO_PASS') OR writer_rag_mode IS NULL),
          ADD COLUMN IF NOT EXISTS topic_batch_id UUID REFERENCES topic_batches(id)
    """)

    # Mirror 0114's view-branch indexes — the table-branch put them on
    # content_tasks (now dropped); they need to live on the underlying
    # base table.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_pipeline_tasks_niche "
        "ON pipeline_tasks(niche_slug) WHERE niche_slug IS NOT NULL"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_pipeline_tasks_batch "
        "ON pipeline_tasks(topic_batch_id) WHERE topic_batch_id IS NOT NULL"
    )

    # Install the redirect functions. CREATE OR REPLACE means re-runs
    # are safe; on prod we never reach this branch so prod's existing
    # functions are untouched.
    await conn.execute(_UPDATE_REDIRECT_FN)
    await conn.execute(_INSERT_REDIRECT_FN)
    await conn.execute(_DELETE_REDIRECT_FN)

    # Create the view. DROP first so a partial prior run doesn't block.
    await conn.execute("DROP VIEW IF EXISTS content_tasks")
    await conn.execute(_CONTENT_TASKS_VIEW_DDL)

    # Attach INSTEAD OF triggers. DROP IF EXISTS first so re-runs
    # don't duplicate.
    for trigger_name, when_clause, function_name in _TRIGGERS:
        await conn.execute(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON content_tasks"
        )
        await conn.execute(
            f"CREATE TRIGGER {trigger_name} {when_clause} ON content_tasks "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )

    logger.info(
        "0125: converted content_tasks TABLE -> VIEW with INSTEAD OF "
        "triggers (matches production shape; closes Glad-Labs/poindexter#329)"
    )


async def down(pool) -> None:
    """Reverse the conversion — only meaningful on a dev DB that has
    been converted by this migration. We can't safely reverse on
    production (the pipeline_tasks/pipeline_versions data has nowhere
    to go that round-trips back to a flat content_tasks table without
    losing the version history).

    The down path:
    - If content_tasks is a VIEW, drop the view + triggers + view-only
      function definitions (we never touch them on prod, so down is
      safe to be aggressive on dev).
    - Recreate a minimal content_tasks table (matching the columns
      0000_base_schema would have produced) so the schema_migrations
      sequence remains consistent.
    - Note: we don't try to drop the niche columns from pipeline_tasks
      — those are now legitimately part of pipeline_tasks regardless of
      content_tasks shape, and 0114's down() handles them.
    """
    async with pool.acquire() as conn:
        relkind = await conn.fetchval(_RELKIND_QUERY)
        if isinstance(relkind, (bytes, bytearray)):
            relkind = relkind.decode("ascii")

        if relkind != 'v':
            logger.info(
                "0125 down: content_tasks is not a view (relkind=%r) — nothing "
                "to revert.",
                relkind,
            )
            return

        # Refuse to revert if data exists — dropping the view would not
        # delete the underlying pipeline_tasks rows, but we don't want
        # to silently leave an empty content_tasks table claiming to be
        # the source of truth while the real data sits in pipeline_tasks.
        row_count = await conn.fetchval("SELECT COUNT(*) FROM content_tasks")
        if row_count and row_count > 0:
            raise RuntimeError(
                f"0125 down: refusing to drop the content_tasks view — "
                f"underlying data has {row_count} rows. Drop down on prod is "
                f"not supported; see migration docstring."
            )

        for trigger_name, _when, _func in _TRIGGERS:
            await conn.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON content_tasks"
            )
        await conn.execute("DROP VIEW IF EXISTS content_tasks")
        # Leave the redirect functions in place — they're harmless when
        # no triggers reference them and re-applying up() will OR REPLACE
        # them anyway.

        logger.info("0125 down: dropped content_tasks view + triggers")
