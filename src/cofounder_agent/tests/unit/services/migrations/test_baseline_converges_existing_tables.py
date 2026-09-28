"""0000_baseline converges a table that already existed (poindexter#1097).

``CREATE TABLE IF NOT EXISTS`` does nothing to an existing table, so a table
another component created first, narrower than the baseline declares, used to
be accepted silently. The next statement naming a missing column then failed.
On a compose-first install the brain daemon creates ``app_settings`` before any
migration runs, and its 8-column table crashed baseline statement #401
(``idx_app_settings_is_active``) on every such install. The baseline now adds
the columns and CHECK constraints an existing table lacks and applies its
declared NOT NULLs, but only while no migration has been recorded.

These tests pin the parser (against every CREATE TABLE in the real schema, with
an independent line-based oracle), the planner, the order of operations inside
``_execute_script``, and the guard. The real-Postgres round trips are in
``tests/integration_db/test_install_orders_converge.py`` and in
migrations-smoke's ``--brain-first`` step.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tests.unit._nonempty import nonempty

_MIGRATIONS = Path(__file__).resolve().parents[4] / "poindexter" / "services" / "migrations"
_SCHEMA = _MIGRATIONS / "0000_baseline.schema.sql"

# The table the brain created before this fix -- a fixture of history, not an
# expectation, so it is written out rather than derived.
_LEGACY_BRAIN_COLUMNS = {
    "id": True, "key": True, "value": False, "category": False,
    "description": False, "is_secret": False, "created_at": False, "updated_at": False,
}
_LEGACY_BRAIN_CONSTRAINTS = {"app_settings_pkey", "app_settings_key_key"}


def _load_baseline():
    """Load 0000_baseline.py exactly as the migration runner does: exec'd from
    its path, never registered in ``sys.modules``."""
    spec = importlib.util.spec_from_file_location("0000_baseline", _MIGRATIONS / "0000_baseline.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def baseline():
    return _load_baseline()


@pytest.fixture(scope="module")
def create_tables(baseline) -> list[str]:
    statements = [
        s for s in baseline._split_sql_statements(_SCHEMA.read_text(encoding="utf-8"))
        if baseline._is_executable(s)
    ]
    return [s for s in statements if baseline._create_table_name(s)]


def _app_settings(baseline, create_tables) -> list:
    stmt = next(s for s in create_tables if baseline._create_table_name(s) == "public.app_settings")
    return baseline.parse_create_table(stmt)[1]


def test_module_loads_without_a_sys_modules_entry():
    """The runner never registers a migration in ``sys.modules``. ``@dataclass``
    looks its module up there and raised at import on the first draft of this
    change, so a helper type in this file has to survive that load."""
    assert "0000_baseline" not in sys.modules
    assert _load_baseline().TableElement._fields == ("kind", "name", "sql", "not_null")


def test_every_create_table_in_the_schema_parses(baseline, create_tables):
    """The dump's own count of CREATE TABLE statements is the floor: a table the
    head regex stopped recognising would silently never converge."""
    declared = len(re.findall(r"^CREATE TABLE", _SCHEMA.read_text(encoding="utf-8"), re.MULTILINE))
    assert declared >= 100
    assert len(create_tables) == declared
    for stmt in nonempty(create_tables, "CREATE TABLE statements"):
        table, elements = baseline.parse_create_table(stmt)
        assert table.startswith("public.")
        assert any(e.kind == "column" for e in elements), table
        assert all(e.name for e in elements), f"{table} has an unnamed entry"


def test_parser_agrees_with_the_dumps_one_entry_per_line_layout(baseline, create_tables):
    """pg_dump writes one entry per line. Reading names off line starts is an
    independent oracle for the quote- and bracket-aware comma splitter."""
    for stmt in nonempty(create_tables, "CREATE TABLE statements"):
        body = baseline._strip_comment_lines(stmt).split("(", 1)[1].rsplit(")", 1)[0]
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        expected = [
            ln.split()[1] if ln.startswith("CONSTRAINT ") else ln.split()[0].strip('"')
            for ln in lines
        ]
        _, elements = baseline.parse_create_table(stmt)
        assert [e.name for e in elements] == expected
        assert [e.sql for e in elements] == [ln.rstrip(",") for ln in lines]


def test_quoted_and_generated_columns(baseline, create_tables):
    elements = {
        (baseline._create_table_name(s), e.name): e
        for s in create_tables
        for e in baseline.parse_create_table(s)[1]
    }
    generated = elements[("public.embeddings", "text_search")]
    assert "GENERATED ALWAYS AS" in generated.sql and generated.sql.endswith("STORED")
    assert generated.not_null is False
    quoted = [e for (_, name), e in elements.items() if name == "timestamp"]
    assert quoted and all(e.sql.startswith('"timestamp" ') for e in quoted)


def test_not_null_is_read_outside_quotes_and_brackets_only(baseline):
    _, elements = baseline.parse_create_table(
        "CREATE TABLE IF NOT EXISTS public.t (\n"
        "    a text DEFAULT ''::text NOT NULL,\n"
        "    b text DEFAULT 'NOT NULL'::text,\n"
        "    c boolean GENERATED ALWAYS AS ((b IS NOT NULL)) STORED,\n"
        '    "Mixed Case" integer NOT NULL\n'
        ")"
    )
    assert [(e.name, e.not_null) for e in elements] == [
        ("a", True), ("b", False), ("c", False), ("Mixed Case", True),
    ]


def test_commas_inside_types_literals_and_checks_stay_in_their_entry(baseline):
    _, elements = baseline.parse_create_table(
        "CREATE TABLE IF NOT EXISTS public.t (\n"
        "    amount numeric(10,2) DEFAULT 0,\n"
        "    tags text[] DEFAULT ARRAY['a'::text, 'b,c'::text],\n"
        "    note text DEFAULT 'it''s, fine'::text,\n"
        "    CONSTRAINT t_tags_check CHECK ((tags <@ ARRAY['a'::text, 'b,c'::text]))\n"
        ")"
    )
    assert [(e.kind, e.name) for e in elements] == [
        ("column", "amount"), ("column", "tags"), ("column", "note"),
        ("constraint", "t_tags_check"),
    ]
    assert elements[1].sql == "tags text[] DEFAULT ARRAY['a'::text, 'b,c'::text]"


@pytest.mark.parametrize(
    "stmt",
    [
        "CREATE TABLE IF NOT EXISTS public.t (a int) PARTITION BY RANGE (a)",
        "CREATE TABLE IF NOT EXISTS public.t (a int) INHERITS (public.parent)",
        "CREATE TABLE public.t (a int)",
    ],
)
def test_shapes_it_cannot_converge_are_refused(baseline, stmt):
    with pytest.raises(ValueError):
        baseline.parse_create_table(stmt)


def test_plan_for_the_table_the_old_brain_created(baseline, create_tables):
    """Everything the baseline declares and the 8-column table lacks, taken from
    the schema itself: missing columns in declared order (so they land in the
    reference's column positions), the NOT NULL the old table relaxed, and the
    constraints last, once every column they name exists."""
    declared = _app_settings(baseline, create_tables)
    plan = baseline.plan_convergence(
        "public.app_settings", declared, _LEGACY_BRAIN_COLUMNS, _LEGACY_BRAIN_CONSTRAINTS
    )
    columns = [e for e in declared if e.kind == "column"]
    adds = [
        f"ALTER TABLE public.app_settings ADD COLUMN IF NOT EXISTS {e.sql}"
        for e in columns if e.name not in _LEGACY_BRAIN_COLUMNS
    ]
    tightens = [
        f'ALTER TABLE public.app_settings ALTER COLUMN "{e.name}" SET NOT NULL'
        for e in columns
        if e.name in _LEGACY_BRAIN_COLUMNS and e.not_null and not _LEGACY_BRAIN_COLUMNS[e.name]
    ]
    constraints = [
        f"ALTER TABLE public.app_settings ADD {e.sql}"
        for e in declared if e.kind == "constraint" and e.name not in _LEGACY_BRAIN_CONSTRAINTS
    ]
    assert [a for a in plan if " ADD COLUMN " in a] == adds
    assert [a for a in plan if a.endswith(" SET NOT NULL")] == tightens
    assert plan[len(plan) - len(constraints):] == constraints
    assert len(plan) == len(adds) + len(tightens) + len(constraints)
    # The pieces the crash and the NOT NULL contract are about, named once:
    assert any("ADD COLUMN IF NOT EXISTS is_active " in a for a in adds)
    assert tightens == ['ALTER TABLE public.app_settings ALTER COLUMN "value" SET NOT NULL']
    assert any("app_settings_value_type_check" in a for a in constraints)


def test_plan_is_empty_for_a_table_already_in_shape(baseline, create_tables):
    declared = _app_settings(baseline, create_tables)
    columns = {e.name: e.not_null for e in declared if e.kind == "column"}
    constraints = {e.name for e in declared if e.kind == "constraint"}
    assert baseline.plan_convergence("public.app_settings", declared, columns, constraints) == []


def test_plan_only_ever_adds(baseline, create_tables):
    """An extra column, a column of another type and a NOT NULL the baseline does
    not declare are all left alone: convergence adds, it never drops, retypes or
    relaxes."""
    declared = _app_settings(baseline, create_tables)
    columns = {e.name: e.not_null for e in declared if e.kind == "column"}
    columns["operator_extra"] = False
    columns["category"] = True  # stricter than declared
    constraints = {e.name for e in declared if e.kind == "constraint"} | {"operator_check"}
    assert baseline.plan_convergence("public.app_settings", declared, columns, constraints) == []


def test_unnamed_constraint_cannot_be_converged(baseline):
    _, elements = baseline.parse_create_table(
        "CREATE TABLE IF NOT EXISTS public.t (a int, CHECK ((a > 0)))"
    )
    with pytest.raises(ValueError, match="unnamed"):
        baseline.plan_convergence("public.t", elements, {"a": False}, set())


class _Catalog:
    """Just enough of an asyncpg connection for ``_execute_script``: a catalog
    of existing tables, plus a log of every statement executed."""

    def __init__(self, tables: dict[str, tuple[dict[str, bool], set[str]]], migrated=0):
        self.tables = tables
        self.migrated = migrated
        self.executed: list[str] = []

    async def fetchval(self, query, *args):
        if "to_regclass('schema_migrations')" in query:
            return True
        if "FROM schema_migrations" in query:
            return self.migrated > 0
        if "::oid" in query:
            return args[0]
        return args[0] in self.tables  # to_regclass($1) IS NOT NULL

    async def fetch(self, query, *args):
        columns, constraints = self.tables[args[0]]
        if "pg_attribute" in query:
            return [{"attname": c, "attnotnull": nn} for c, nn in columns.items()]
        return [{"conname": c} for c in constraints]

    async def execute(self, stmt):
        self.executed.append(" ".join(stmt.split()))
        return "OK"


def _script(create_tables, baseline) -> str:
    create = next(s for s in create_tables if baseline._create_table_name(s) == "public.app_settings")
    return (
        f"{create};\n"
        "CREATE INDEX IF NOT EXISTS idx_app_settings_is_active ON public.app_settings "
        "USING btree (is_active) WHERE (is_active = true);\n"
    )


async def test_a_pre_existing_table_is_converged_before_the_next_statement(baseline, create_tables):
    """The crash was statement ordering: the index on is_active ran against a
    table that never got the column. The ALTER has to land in between."""
    conn = _Catalog({"public.app_settings": (dict(_LEGACY_BRAIN_COLUMNS), set(_LEGACY_BRAIN_CONSTRAINTS))})
    applied, skipped, converged = await baseline._execute_script(
        conn, _script(create_tables, baseline), "schema"
    )
    add_is_active = next(i for i, s in enumerate(conn.executed) if "ADD COLUMN IF NOT EXISTS is_active" in s)
    index = next(i for i, s in enumerate(conn.executed) if s.startswith("CREATE INDEX"))
    create = next(i for i, s in enumerate(conn.executed) if "CREATE TABLE IF NOT EXISTS public.app_settings" in s)
    assert create < add_is_active < index
    assert (applied, skipped) == (2, 0)
    assert converged == 8  # six columns, one NOT NULL, one CHECK


async def test_a_new_table_is_created_and_left_alone(baseline, create_tables):
    conn = _Catalog({})
    _, _, converged = await baseline._execute_script(conn, _script(create_tables, baseline), "schema")
    assert converged == 0
    assert not any(s.startswith("ALTER TABLE") for s in conn.executed)


def _pool(conn):
    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    return _Pool()


@pytest.mark.parametrize(("migrated", "converged"), [(0, True), (1, False)])
async def test_up_converges_only_before_anything_is_migrated(
    baseline, create_tables, tmp_path, monkeypatch, migrated, converged
):
    """Once a migration is recorded, a declared column the table lacks was
    dropped on purpose by a later migration, and re-running the baseline (its
    row deleted by hand) must not restore it. Goes through ``up()`` so the
    guard's wiring is what is tested."""
    schema, seeds = tmp_path / "schema.sql", tmp_path / "seeds.sql"
    schema.write_text(_script(create_tables, baseline), encoding="utf-8")
    seeds.write_text("", encoding="utf-8")
    monkeypatch.setattr(baseline, "_SCHEMA_FILE", schema)
    monkeypatch.setattr(baseline, "_SEEDS_FILE", seeds)
    conn = _Catalog(
        {"public.app_settings": (dict(_LEGACY_BRAIN_COLUMNS), set(_LEGACY_BRAIN_CONSTRAINTS))},
        migrated=migrated,
    )
    await baseline.up(_pool(conn))
    alters = [s for s in conn.executed if s.startswith("ALTER TABLE")]
    assert bool(alters) is converged
    assert len(alters) == (8 if converged else 0)
