"""Baseline migration — schema + seed data as of the Phase G squash.

This single migration replaces all prior migration files. **Phase G squash,
2026-07-11** (under Glad-Labs/glad-labs-stack). It supersedes:

- The 0000_baseline.py that captured the Phase F squash (2026-06-22, which
  absorbed the Phase E baseline + 73 migrations through 20260622_* and retired
  ``pipeline_tasks.category``).
- All 42 timestamped migrations from 20260622_200222_* through
  20260711_202250_* that accumulated after the Phase F squash.

**True baseline-only — no surviving post-baseline migration.** Phase F had to
keep ``20260622_200222_drop_pipeline_tasks_category.py`` as a convergence step
(a baseline only ``CREATE TABLE IF NOT EXISTS``, so it can add but never drop a
column that prod still carried). By Phase G, prod is verified current through
20260711_202250_* (``schema_migrations`` carries every folded file), so the
column is already dropped everywhere and the new schema simply omits it — the
survivor folds away. Orphan ``schema_migrations`` rows for the 42 deleted files
are harmless (the runner skips by filename and never reconciles in reverse).

Key schema deltas folded in since Phase F:
- New tables: ``clock_skew_samples``, ``live_activity``, ``social_post_drafts``,
  ``remediation_rules``, ``affiliate_links``, ``affiliate_link_clicks``.
- ``page_views`` bot-flag columns (``is_bot`` / ``bot_reason`` / ``flagged_at``)
  + the ``page_views_human`` view; ``lab_outcomes_v1`` repointed onto it.
- ``audit_log.source`` widened ``character varying(50)`` -> ``text``.
- ``pipeline_tasks`` ``trace_context`` + ``podcast_redispatch_count`` +
  ``media_pipeline_cap_reset_at``; ``atom_runs.output_preview`` + run_id seq
  uniqueness; ``app_settings.last_read_at`` read-telemetry column;
  ``topic_candidates.grounding_ref``.

Key seed deltas folded in (fold-forward from the chain, not a prod re-dump):
- ``pipeline_templates`` 5 -> 6: adds the ``image_rebuild`` graph_def
  (20260711_024500); media/podcast graph_defs re-stamped to current contracts;
  canonical_blog graph_def reseeded with the ``inject_affiliate_links`` node
  (20260711_202250).
- ``app_settings`` nets to 691 non-secret rows: the sdxl -> image_gen rename
  plus the dead-model and two zero-reader orphan sweeps (incl. batch2's 6 keys
  — staging_mode / newsletter_email / local_database_url / repo_root /
  site_description / site_tagline) outweigh the additions.
- ``qa_gates`` stays 18 (content_originality + citation_grounding already
  seeded); ``retention_policies`` stays 31; the ``glad_labs_claim`` validator
  rule is seeded as ``company_claim``.

Why:
- Fresh-DB setup time grows linearly with migration count; CI migrations-smoke
  was applying 42 files in series.
- A stale column reference in any one timestamped migration can crash the
  db_pool fixture and silently break every db-backed test.

Two sibling files carry the actual SQL, regenerated from a throwaway DB that
ran the full pre-squash chain (correct-by-construction; verified byte-for-byte
against a chain pg_dump — schema identical + all 11 seed tables md5-identical):

- ``0000_baseline.schema.sql`` — pg_dump --schema-only sanitized to idempotent
  form (``CREATE TABLE -> CREATE TABLE IF NOT EXISTS``, ``CREATE FUNCTION ->
  CREATE OR REPLACE FUNCTION``, ``CREATE INDEX -> ... IF NOT EXISTS``). No-ops
  on Matt's prod; bootstraps a fresh DB.
- ``0000_baseline.seeds.sql`` — the 691 non-secret ``app_settings`` defaults
  plus 2 empty-valued secret placeholders (``cloudflare_analytics_api_token``,
  ``mcp_http_probe_recovery_token`` — seeded so those keys exist for the
  operator to fill; no secret value ever ships), plus ``qa_gates``,
  ``content_validator_rules``, ``niches``, ``niche_goals``,
  ``pipeline_templates``, ``external_taps``, ``publishing_adapters``,
  ``webhook_endpoints``, ``retention_policies``, ``fact_overrides``. Secret
  values + operator identity stay out; ``poindexter setup`` writes per-operator
  config.

New schema changes from here on go in fresh timestamped migrations
(``YYYYMMDD_HHMMSS_<slug>.py``) — same convention; the runner sorts
``0000_baseline.py`` first because ``0`` < ``2`` lexically.

**A table that already exists is converged, not skipped (poindexter#1097).**
``CREATE TABLE IF NOT EXISTS`` does nothing to an existing table: it does not
add the columns it declares. On prod that is the point. But it also means a
table another component created first, narrower than declared here, was
silently accepted, and the first later statement naming a missing column
failed. The brain daemon does exactly that on a compose-first install: it
creates ``app_settings`` so it can seed it before the worker runs a single
migration, and its 8-column table crashed statement #401
(``idx_app_settings_is_active``) on every such install from May 2026. The
pre-squash chain never had the problem: its migrations widened the table with
explicit ``ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS`` steps (0058 said
so, "for databases that pre-date this column"). Folding the chain into one
pg_dump ``CREATE TABLE`` for each table (2026-05-08) dropped those steps.

So when one of this file's ``CREATE TABLE``s meets an existing table,
``_execute_script`` adds the columns and CHECK constraints it lacks and applies
declared ``NOT NULL``s (see ``plan_convergence``). Nothing is dropped, retyped
or re-defaulted. It only happens while ``schema_migrations`` is empty: after
that, a missing column was dropped on purpose by a later migration
(``_any_migration_recorded``). The brain now creates the declared shape itself
(pinned by ``tests/unit/brain/test_seed_loader_app_settings_ddl.py``). The
convergence is still needed because the brain is baked into its image while the
worker runs mounted source, so an older brain can create the old table ahead of
this code.
"""


from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import asyncpg

from poindexter.services.logger_config import get_logger

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


# ---------------------------------------------------------------------------
# Converging a table that existed before its ``CREATE TABLE`` (poindexter#1097)
# ---------------------------------------------------------------------------

_CREATE_TABLE_HEAD_RE = re.compile(
    r"^CREATE TABLE IF NOT EXISTS\s+(?P<table>[^\s(]+)\s*\(", re.IGNORECASE
)
_NOT_NULL_RE = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
# An entry of a ``CREATE TABLE``'s body that is not a column. pg_dump names every
# constraint it inlines (always CHECK today); the unnamed spellings are listed so
# they parse as constraints rather than as a column called "primary".
_TABLE_CONSTRAINT_RE = re.compile(
    r"^(CONSTRAINT|PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|EXCLUDE|LIKE)\b",
    re.IGNORECASE,
)


class TableElement(NamedTuple):
    """One entry of a ``CREATE TABLE``'s body: a column or a table constraint.

    A NamedTuple, not a dataclass: the runner execs this file without
    registering it in ``sys.modules``, and ``@dataclass`` looks the defining
    module up there, so it raises at import.
    """

    kind: str  # "column" or "constraint"
    name: str | None  # as the catalog stores it; None for an unnamed constraint
    sql: str  # the entry's text, verbatim
    not_null: bool = False  # columns only: the entry declares NOT NULL


def _strip_comment_lines(stmt: str) -> str:
    """The statement without the dump's ``--`` header lines."""
    return "\n".join(
        line for line in stmt.splitlines() if not line.lstrip().startswith("--")
    ).strip()


def _create_table_name(stmt: str) -> str | None:
    """The table a ``CREATE TABLE IF NOT EXISTS`` statement names, else None."""
    match = _CREATE_TABLE_HEAD_RE.match(_strip_comment_lines(stmt))
    return match.group("table") if match else None


def _scan(text: str) -> Iterator[tuple[int, str, int, bool]]:
    """Yield ``(index, char, depth, quoted)`` for each character of ``text``.

    ``depth`` counts the open ``(``/``[`` enclosing the character (a bracket
    counts as inside itself) and ``quoted`` is True inside a ``'literal'`` or
    ``"identifier"``, quotes included. A doubled quote closes and immediately
    reopens, so ``'it''s'`` stays quoted throughout.
    """
    depth = 0
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote:
            yield i, ch, depth, True
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            yield i, ch, depth, True
            continue
        if ch in "([":
            depth += 1
            yield i, ch, depth, False
            continue
        if ch in ")]":
            yield i, ch, depth, False
            depth -= 1
            continue
        yield i, ch, depth, False


def _split_top_level(body: str) -> list[str]:
    """Split the body of a ``CREATE TABLE`` on the commas between its entries.

    Commas inside parentheses or brackets (``numeric(10,2)``, ``ARRAY['a', 'b']``,
    CHECK and generated-column expressions) and inside quotes belong to an entry.
    """
    parts: list[str] = []
    start = 0
    for i, ch, depth, quoted in _scan(body):
        if ch == "," and depth == 0 and not quoted:
            parts.append(body[start:i].strip())
            start = i + 1
    tail = body[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _top_level_text(text: str) -> str:
    """``text`` with every quoted or bracketed span blanked out, so a keyword
    search cannot match inside a default literal or a CHECK expression."""
    return "".join(
        ch if depth == 0 and not quoted and ch not in "()[]" else " "
        for _, ch, depth, quoted in _scan(text)
    )


def _read_identifier(text: str) -> tuple[str, str]:
    """Split ``text`` into its leading identifier, as the catalog stores it
    (unquoted names fold to lower case), and whatever follows it."""
    text = text.lstrip()
    if text.startswith('"'):
        name: list[str] = []
        i = 1
        while i < len(text):
            if text[i] == '"':
                if text[i + 1 : i + 2] == '"':
                    name.append('"')
                    i += 2
                    continue
                return "".join(name), text[i + 1 :]
            name.append(text[i])
            i += 1
        raise ValueError(f"unterminated quoted identifier: {text[:60]!r}")
    match = re.match(r"[^\s(]+", text)
    if not match:
        raise ValueError(f"expected an identifier: {text[:60]!r}")
    return match.group(0).lower(), text[match.end() :]


def _parse_element(entry: str) -> TableElement:
    if _TABLE_CONSTRAINT_RE.match(entry):
        if re.match(r"CONSTRAINT\b", entry, re.IGNORECASE):
            name, _ = _read_identifier(entry[len("CONSTRAINT") :])
            return TableElement("constraint", name, entry)
        return TableElement("constraint", None, entry)
    name, rest = _read_identifier(entry)
    if not rest.strip():
        raise ValueError(f"column {name!r} has no type: {entry!r}")
    return TableElement(
        "column", name, entry, bool(_NOT_NULL_RE.search(_top_level_text(rest)))
    )


def parse_create_table(stmt: str) -> tuple[str, list[TableElement]]:
    """Parse ``CREATE TABLE IF NOT EXISTS <table> (...)`` into its table name and
    entries. Raises ValueError for anything else, including a trailing clause
    (``PARTITION BY``, ``INHERITS``, ``WITH``) this module does not converge."""
    text = _strip_comment_lines(stmt)
    head = _CREATE_TABLE_HEAD_RE.match(text)
    if not head:
        raise ValueError(f"not a table definition this module can converge: {text[:80]!r}")
    open_at = head.end() - 1
    close_at = next(
        (i for i, ch, depth, quoted in _scan(text[open_at:])
         if ch == ")" and depth == 1 and not quoted),
        None,
    )
    if close_at is None:
        raise ValueError(f"unbalanced CREATE TABLE {head.group('table')}")
    close_at += open_at
    trailing = text[close_at + 1 :].strip().rstrip(";").strip()
    if trailing:
        raise ValueError(
            f"CREATE TABLE {head.group('table')} has a trailing clause the "
            f"baseline cannot converge: {trailing[:80]!r}"
        )
    body = text[open_at + 1 : close_at]
    return head.group("table"), [_parse_element(e) for e in _split_top_level(body)]


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def plan_convergence(
    table: str,
    declared: Sequence[TableElement],
    columns: Mapping[str, bool],
    constraints: Collection[str],
) -> list[str]:
    """The ALTERs that bring an existing ``table`` up to its declared shape.

    ``columns`` maps each existing column to whether it is NOT NULL;
    ``constraints`` holds the table's existing constraint names. A declared
    column the table lacks is added with its full declaration; a declared NOT
    NULL the table does not enforce is applied; a named declared constraint the
    table lacks is added. Nothing is dropped, retyped or re-defaulted, and an
    existing column or constraint of the same name is left exactly as it is.
    """
    unnamed = [e.sql for e in declared if e.kind == "constraint" and e.name is None]
    if unnamed:
        raise ValueError(
            f"cannot converge {table}: unnamed table constraint(s) {unnamed}; "
            "only a named constraint can be checked for existence"
        )
    alters: list[str] = []
    for element in declared:
        if element.kind != "column":
            continue
        if element.name not in columns:
            alters.append(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {element.sql}")
        elif element.not_null and not columns[element.name]:
            alters.append(
                f"ALTER TABLE {table} ALTER COLUMN "
                f"{_quote_identifier(element.name)} SET NOT NULL"
            )
    for element in declared:
        if element.kind == "constraint" and element.name not in constraints:
            alters.append(f"ALTER TABLE {table} ADD {element.sql}")
    return alters


async def _converge_existing_table(conn, stmt: str, label: str) -> int:
    """Converge the table ``stmt`` declares, which existed before ``stmt`` ran.
    Returns how many ALTERs it applied."""
    table, declared = parse_create_table(stmt)
    oid = await conn.fetchval("SELECT to_regclass($1)::oid", table)
    columns = {
        r["attname"]: r["attnotnull"]
        for r in await conn.fetch(
            "SELECT attname, attnotnull FROM pg_attribute "
            "WHERE attrelid = $1 AND attnum > 0 AND NOT attisdropped",
            oid,
        )
    }
    constraints = {
        r["conname"]
        for r in await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE conrelid = $1", oid
        )
    }
    applied = 0
    for alter in plan_convergence(table, declared, columns, constraints):
        logger.info(
            "[baseline:%s] %s existed before the baseline created it; converging: %s",
            label, table, alter,
        )
        try:
            await conn.execute(alter)
            applied += 1
        except _DUPLICATE_ERRORS as exc:  # silent-ok: a concurrent runner already added it, which is the state this ALTER wanted
            logger.debug("[baseline:%s] skipped duplicate object: %s", label, exc)
        except Exception as exc:
            logger.error(
                "[baseline:%s] converging pre-existing %s failed (%s): %s\n%s",
                label, table, type(exc).__name__, exc, alter,
            )
            raise
    return applied


async def _any_migration_recorded(conn) -> bool:
    """True once the runner has recorded any migration in this database.

    Converging is only right before that. A table can legitimately predate the
    baseline only on a database nothing has migrated yet (the brain-first boot,
    or one whose first baseline attempt failed). Once any migration is recorded,
    a column the baseline declares but the table lacks was dropped on purpose by
    a later migration, and re-running the baseline (its row deleted by hand)
    must not put it back.
    """
    if not await conn.fetchval("SELECT to_regclass('schema_migrations') IS NOT NULL"):
        return False
    return bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM schema_migrations)"))


async def _execute_script(
    conn, sql: str, label: str, *, converge_existing: bool = True
) -> tuple[int, int, int]:
    """Apply ``sql`` statement by statement. Returns ``(applied, skipped, converged)``:
    statements run, statements skipped as already-present, and ALTERs spent
    converging tables that existed before their CREATE TABLE (only when
    ``converge_existing``)."""
    statements = [s for s in _split_sql_statements(sql) if _is_executable(s)]
    applied = 0
    skipped = 0
    converged = 0
    for idx, stmt in enumerate(statements):
        table = _create_table_name(stmt) if converge_existing else None
        existed = table is not None and await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", table
        )
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
        if existed:
            converged += await _converge_existing_table(conn, stmt, label)
    logger.info(
        "[baseline:%s] %d statement(s) applied, %d skipped (already-exists), "
        "%d convergence ALTER(s) on pre-existing tables",
        label, applied, skipped, converged,
    )
    return applied, skipped, converged


async def up(pool) -> None:
    schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    seeds_sql = _SEEDS_FILE.read_text(encoding="utf-8")

    async with pool.acquire() as conn:
        converge = not await _any_migration_recorded(conn)
        await _execute_script(conn, schema_sql, "schema", converge_existing=converge)
        await _execute_script(conn, seeds_sql, "seeds", converge_existing=converge)
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
