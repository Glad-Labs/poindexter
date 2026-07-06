"""Baseline migration — schema + seed data as of the Phase F squash.

This single migration replaces all prior migration files. **Phase F squash,
2026-06-22** (under Glad-Labs/poindexter). It supersedes:

- The 0000_baseline.py that captured the Phase E squash (2026-06-06,
  Glad-Labs/poindexter#1194, which absorbed the Phase D baseline +
  55 migrations through 20260606_233518_*).
- All 73 timestamped migrations from 20260607_* through 20260622_* that
  accumulated after the Phase E squash — including the
  ``pipeline_tasks.category`` add/drop/re-add churn (20260622_032938 dropped
  it via view shims, 20260622_055500 re-added the base column to unbreak the
  claim path, #1867 reconciled it base-table-only). That churn collapses to
  nothing here: the new schema simply omits ``category``.

Key schema deltas since Phase E:
- ``pipeline_tasks.category`` **retired** (superseded by ``niche_slug``, #796;
  0 of 1,830 prod rows were ever non-NULL). The ``content_tasks`` /
  ``pipeline_tasks_view`` views keep a literal ``NULL::character varying AS
  category`` shim, so ``SELECT *`` / ``TaskRecord.category`` / ``?category=``
  readers are unaffected. See the surviving drop migration below.
- ``pipeline_tasks`` regen counters + pending flags, ``media_pipeline_redispatch_count``;
  ``job_run_state`` table; ``topic_pool`` table + ``external_taps.niche_id``;
  ``agent_permissions`` + ``approval_queue`` tables; ``app_settings`` lifecycle
  metadata columns (owner / value_type / deprecated / superseded_by) + the
  value-write ``updated_at`` trigger; ``pipeline_tasks.status`` 15-value CHECK.

Key seed deltas since Phase E (fold-forward from the chain, not a prod re-dump):
- ``pipeline_templates`` 2 -> 5: ``canonical_blog`` v6 (39 nodes — preview_gate),
  ``dev_diary``, plus the migration-seeded ``media_pipeline`` / ``podcast_pipeline``
  / ``seo_refresh`` graph_defs.
- ``retention_policies`` 5 -> 23 (retention-summary + sensor-downsample batch).
- ``external_taps`` 6 -> 1: the 5 dead global ``builtin_topic_source`` taps were
  retired by 20260615_033048 (topic sourcing moved to niche-bound taps); only
  ``corsair_csv`` survives on a seed-only DB. Fresh installs land in the same
  state the chain produced.
- ``app_settings`` 741 -> 761 (net, after retirements); ``qa_gates`` 14 -> 16
  (qa.self_consistency, citation reconciliation rails).

Why:
- Fresh-DB setup time grows linearly with migration count; CI migrations-smoke
  was applying 74 files in series.
- A stale column reference in any one timestamped migration can crash the
  db_pool fixture and silently break every db-backed test.
- The category add/drop/re-add churn was pure wasted motion (#1867 follow-up).

Two sibling files carry the actual SQL, regenerated from a throwaway DB that
ran the full pre-squash chain (correct-by-construction; verified byte-for-byte
against a chain pg_dump):

- ``0000_baseline.schema.sql`` — pg_dump --schema-only sanitized to idempotent
  form (``CREATE TABLE -> CREATE TABLE IF NOT EXISTS``, ``CREATE FUNCTION ->
  CREATE OR REPLACE FUNCTION``, ``CREATE INDEX -> ... IF NOT EXISTS``). No-ops
  on Matt's prod; bootstraps a fresh DB.
- ``0000_baseline.seeds.sql`` — non-secret ``app_settings`` (is_secret = false)
  plus ``qa_gates``, ``content_validator_rules``, ``niches``, ``niche_goals``,
  ``pipeline_templates``, ``external_taps``, ``publishing_adapters``,
  ``webhook_endpoints``, ``retention_policies``, ``fact_overrides``. Secrets +
  operator identity stay out; ``poindexter setup`` writes per-operator config.

**One post-baseline migration survives the squash:**
``20260622_200222_drop_pipeline_tasks_category.py`` =
``ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS category``. A baseline only
``CREATE TABLE IF NOT EXISTS`` — a no-op on prod, which still carries the column
from 20260622_055500 — so a baseline that merely omits ``category`` would leave
prod/fresh schema-drifted. That migration is the convergence step: a no-op on
fresh installs (the column is absent), the real drop on prod. Phase G can fold
it away once every install has dropped it.

New schema changes from here on go in fresh timestamped migrations
(``YYYYMMDD_HHMMSS_<slug>.py``) — same convention; the runner sorts
``0000_baseline.py`` first because ``0`` < ``2`` lexically.
"""


from __future__ import annotations

from pathlib import Path

import asyncpg

from services.logger_config import get_logger

logger = get_logger(__name__)

_HERE = Path(__file__).parent
_SCHEMA_FILE = _HERE / "0000_baseline.schema.sql"
_SEEDS_FILE = _HERE / "0000_baseline.seeds.sql"


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements.

    Respects single-quoted strings and ``$$`` dollar-quoted blocks
    (used by ``CREATE FUNCTION`` bodies in the schema dump). The schema
    dump uses only the unnamed ``$$`` tag — no ``$body$`` style tags —
    so we don't need a full lexer.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_dollar = False
    in_squote = False
    in_line_comment = False
    while i < n:
        ch = sql[i]

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_dollar:
            if sql.startswith("$$", i):
                buf.append("$$")
                i += 2
                in_dollar = False
            else:
                buf.append(ch)
                i += 1
            continue

        if in_squote:
            buf.append(ch)
            if ch == "'":
                # Postgres escapes '' as two single quotes — peek ahead.
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_squote = False
            i += 1
            continue

        # Not inside any quote
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            in_line_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_squote = True
            buf.append(ch)
            i += 1
            continue
        if sql.startswith("$$", i):
            in_dollar = True
            buf.append("$$")
            i += 2
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


# Errors that mean "the object already exists" — safe to swallow when
# applying the baseline against a DB that's already at v0.5.0 (i.e. Matt's
# prod). pg_dump emits ALTER TABLE ADD CONSTRAINT without an IF NOT EXISTS
# form, and Postgres surfaces "constraint already there" as several
# different sqlstate codes depending on the constraint kind:
#   - 42P07 DuplicateTableError      (CREATE TABLE / TYPE collisions)
#   - 42710 DuplicateObjectError     (CREATE INDEX / EXTENSION / etc.)
#   - 42701 DuplicateColumnError     (ALTER TABLE ADD COLUMN)
#   - 42P16 InvalidTableDefinitionError ("multiple primary keys ...")
#   - 23505 UniqueViolationError     (seed INSERTs landing on existing rows)
# CREATE TRIGGER is emitted plain (no DROP TRIGGER IF EXISTS guard — matching
# the sanitized dump's house style); a duplicate trigger surfaces as 42710
# DuplicateObjectError, already in the swallow set below.
_DUPLICATE_ERRORS = (
    asyncpg.exceptions.DuplicateTableError,
    asyncpg.exceptions.DuplicateObjectError,
    asyncpg.exceptions.DuplicateColumnError,
    asyncpg.exceptions.DuplicateSchemaError,
    asyncpg.exceptions.DuplicateAliasError,
    asyncpg.exceptions.DuplicateFunctionError,
    asyncpg.exceptions.InvalidTableDefinitionError,
    asyncpg.exceptions.UniqueViolationError,
)


def _is_executable(stmt: str) -> bool:
    """True if the statement has any non-comment, non-whitespace content.

    The schema dump separates objects with ``-- ... -- ...`` blocks; my
    splitter happily emits those as their own "statements" and asyncpg
    crashes on the NULL command tag they produce. Filter them out.
    """
    for line in stmt.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("--"):
            continue
        return True
    return False


async def _execute_script(conn, sql: str, label: str) -> tuple[int, int]:
    statements = [s for s in _split_sql_statements(sql) if _is_executable(s)]
    applied = 0
    skipped = 0
    for idx, stmt in enumerate(statements):
        try:
            await conn.execute(stmt)
            applied += 1
        except _DUPLICATE_ERRORS as exc:
            skipped += 1
            logger.debug("[baseline:%s] skipped duplicate object: %s", label, exc)
        except Exception as exc:
            # Surface the offending statement so we can diagnose splitter
            # bugs or seed-data problems instead of getting a bare
            # "Migrations failed" with no breadcrumbs.
            logger.error(
                "[baseline:%s] statement #%d failed (%s): %s\n--- statement preview ---\n%s",
                label, idx, type(exc).__name__, exc, stmt[:500],
            )
            raise
    logger.info(
        "[baseline:%s] %d statement(s) applied, %d skipped (already-exists)",
        label, applied, skipped,
    )
    return applied, skipped


async def up(pool) -> None:
    schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    seeds_sql = _SEEDS_FILE.read_text(encoding="utf-8")

    async with pool.acquire() as conn:
        await _execute_script(conn, schema_sql, "schema")
        await _execute_script(conn, seeds_sql, "seeds")
    logger.info("[baseline] applied — schema + seeds in sync with v0.7.0 prod state")


async def down(pool) -> None:
    """Refuse to revert. The baseline absorbs 169 historical migrations
    spanning 18 months of schema evolution — there is no meaningful
    'previous state' to roll back to. If you need to start over, drop
    the database and re-run forward.
    """
    raise NotImplementedError(
        "0000_baseline is irreversible — it represents the v0.5.0 schema "
        "snapshot, not an incremental change. Drop the database to revert."
    )
