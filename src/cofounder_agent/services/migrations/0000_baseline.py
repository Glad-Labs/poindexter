"""Baseline migration — schema + seed data as of v0.5.0.

This single migration replaces the 169 legacy ``0xxx_*.py`` and
``20260*_*.py`` files that previously evolved the schema incrementally.
Squashed 2026-05-08 because:

- Running 169 migrations on every fresh DB took ~5 seconds and was a
  ticking time bomb — a stale column reference like the one in
  ``20260508_215727_seed_qa_gate_deepeval_brand_fabrication.py`` (which
  inserted into a non-existent ``app_settings.value_type``) crashed the
  unit-tier ``db_pool`` fixture and silently broke every db-backed test.
  The audit on ``Glad-Labs/poindexter#30`` flagged that drift directly.
- Glad Labs runs solo with one prod DB; the per-step audit trail of
  149 historical migrations carried zero operational value. The git log
  is the audit trail.
- Open-source onboarding (``poindexter setup``) wants ONE step from
  empty Postgres to a working schema, not 169.

Two sibling files carry the actual SQL (kept out of this Python module
so diffs are readable):

- ``0000_baseline.schema.sql`` — sanitized ``pg_dump --schema-only``
  with ``CREATE TABLE → CREATE TABLE IF NOT EXISTS`` etc., so the same
  file no-ops on Matt's prod and bootstraps a fresh DB.
- ``0000_baseline.seeds.sql`` — non-secret ``app_settings`` rows,
  ``qa_gates``, ``content_validator_rules``. Secrets stay out by design;
  ``poindexter setup`` writes per-operator credentials into bootstrap.toml
  + ``app_settings`` at install time.

New schema changes from here on go in fresh timestamped migrations
(``YYYYMMDD_HHMMSS_<slug>.py``) — same convention as before; the runner
sorts ``0000_baseline.py`` first because ``0`` < ``2`` lexically.
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
# CREATE TRIGGER paths through DROP TRIGGER IF EXISTS first (added by
# the sanitizer), so we don't need to swallow trigger-create errors.
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
    logger.info("[baseline] applied — schema + seeds in sync with v0.5.0 prod state")


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
