"""media_distribute's approved-undispatched SQL vs the REAL schema.

#3767 added ``p.niche_slug`` to the query; ``posts`` has no such column, so
every ``media_distribute`` cycle failed with UndefinedColumnError for three
hours (2026-09-15 01:15 → 04:xx) while the unit tests — which fake the pool —
stayed green. Same lesson as test_chat_watch_schema: execute the real SQL.
"""
import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_approved_undispatched_sql_executes_on_real_schema(test_pool):
    from poindexter.services.jobs import media_distribute as md

    rows = await test_pool.fetch(
        md._APPROVED_UNDISPATCHED_SQL, list(md._TYPE_TO_MEDIUM.values()), 5
    )
    assert isinstance(rows, list)  # zero rows is fine — the schema is the assertion


async def test_adapters_sql_executes_on_real_schema(test_pool):
    from poindexter.services.jobs import media_distribute as md

    rows = await test_pool.fetch(md._ADAPTERS_SQL, ["youtube"])
    assert isinstance(rows, list)
