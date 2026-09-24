"""After baseline + migrations, no jsonb column holds a container as a string.

poindexter#1061: the baseline seeded 58 jsonb values double-encoded, and no CI
job ever READ these columns, so a green migrations-smoke only proved the SQL
applied. This reads every jsonb column in the schema — derived from
information_schema, not listed — and asserts none holds a JSON object/array
wrapped in a jsonb string. It also proves the convergence migration unwraps a
row that an older install still carries.
"""

from __future__ import annotations

import importlib
import json

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MIGRATION = (
    "poindexter.services.migrations."
    "20260924_190722_unwrap_double_encoded_jsonb_in_the_seeded_declarative_tables"
)


async def test_no_jsonb_column_holds_a_string_wrapped_container(test_pool):
    async with test_pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND data_type = 'jsonb' "
            "AND table_name IN (SELECT table_name FROM information_schema.tables "
            "                   WHERE table_schema = 'public' AND table_type = 'BASE TABLE')"
        )
        assert len(cols) > 20, "jsonb column discovery went blind"
        bad = []
        for c in cols:
            t, col = '"' + c["table_name"] + '"', '"' + c["column_name"] + '"'
            n = await conn.fetchval(
                f"SELECT count(*) FROM {t} WHERE jsonb_typeof({col}) = 'string' "  # nosec B608 - identifiers from information_schema
                f"AND ltrim({col} #>> '{{}}') ~ '^[\\[{{]'"
            )
            if n:
                bad.append(f"{c['table_name']}.{c['column_name']}={n}")
    assert not bad, f"double-encoded jsonb after baseline+migrations: {bad}"


async def test_the_migration_unwraps_an_old_install_row(test_pool):
    mod = importlib.import_module(_MIGRATION)
    async with test_pool.acquire() as conn:
        name = await conn.fetchval("SELECT name FROM qa_gates ORDER BY name LIMIT 1")
        original = await conn.fetchval("SELECT metadata FROM qa_gates WHERE name = $1", name)
        original = json.loads(original) if isinstance(original, str) else original
        await conn.execute(
            "UPDATE qa_gates SET metadata = to_jsonb($1::text) WHERE name = $2",
            json.dumps({"probe": "double-encoded"}), name,
        )
    try:
        await mod.up(test_pool)
        async with test_pool.acquire() as conn:
            kind, value = await conn.fetchrow(
                "SELECT jsonb_typeof(metadata), metadata->>'probe' FROM qa_gates WHERE name = $1",
                name,
            )
        assert (kind, value) == ("object", "double-encoded")
        await mod.up(test_pool)  # idempotent
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "UPDATE qa_gates SET metadata = $1::jsonb WHERE name = $2",
                json.dumps(original), name,
            )
