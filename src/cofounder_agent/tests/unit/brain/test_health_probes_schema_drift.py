"""Schema-drift regression tests for ``brain/health_probes.py`` (2026-07-11).

Two probes shipped querying columns that never existed in the deployed
schema — ``newsletter_subscribers.confirmed`` (real column: ``verified``)
and ``embeddings.source_type`` (real column: ``source_table``, and the
value is ``'posts'`` plural, matching what ``embedding_service`` writes).
Both failures were invisible because the probes' except blocks treated any
error containing "does not exist" as "table not created yet" and returned
``ok: True`` — a missing *column* (code bug) was masked as a missing
*table* (benign bootstrap state). Only the postgres server logs caught it.

Pins three things:
1. Every identifier in the SQL each probe issues exists as a column in
   ``0000_baseline.schema.sql`` (the drift class, generically).
2. The fixed semantics: active-subscriber predicate mirrors
   ``newsletter_service._get_active_subscribers``; embeddings filter is
   ``source_table = 'posts'``.
3. Missing-relation errors still report the benign bootstrap state, but
   any other DB error (incl. column drift) fails loud with the real error.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from brain.health_probes import (
    probe_embeddings_freshness,
    probe_newsletter_health,
    probe_podcast_health,
)

# ---------------------------------------------------------------------------
# Baseline-schema helpers
# ---------------------------------------------------------------------------


def _baseline_schema_path() -> Path:
    """Locate 0000_baseline.schema.sql from either the src/ or container layout."""
    for parent in Path(__file__).resolve().parents:
        for candidate in (
            parent / "services" / "migrations" / "0000_baseline.schema.sql",
            parent
            / "src"
            / "cofounder_agent"
            / "services"
            / "migrations"
            / "0000_baseline.schema.sql",
        ):
            if candidate.is_file():
                return candidate
    pytest.skip("0000_baseline.schema.sql not reachable from test layout")


def _baseline_columns(table: str) -> set[str]:
    """Parse the CREATE TABLE block for ``table`` and return its column names."""
    sql = _baseline_schema_path().read_text(encoding="utf-8")
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);",
        sql,
        re.DOTALL,
    )
    assert match, f"table {table} not found in baseline schema"
    columns: set[str] = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.upper().startswith(
            ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN")
        ):
            continue
        columns.add(line.split()[0].strip('"'))
    return columns


_SQL_NOISE = {
    # keywords / functions appearing in the probes' SQL — not identifiers
    "select",
    "count",
    "max",
    "min",
    "sum",
    "avg",
    "distinct",
    "from",
    "where",
    "and",
    "or",
    "is",
    "null",
    "not",
    "as",
    "true",
    "false",
    "order",
    "by",
    "limit",
    "on",
    "coalesce",
    "interval",
    "now",
    "in",
}


def _column_identifiers(sql: str) -> set[str]:
    """Extract candidate column identifiers from a probe's SQL.

    Strips string literals, then removes SQL keywords/functions and
    result aliases (the token following ``AS``). What remains must be
    table or column names.
    """
    lowered = re.sub(r"'[^']*'", "", sql.lower())
    tokens = set(re.findall(r"[a-z_][a-z0-9_]*", lowered))
    aliases = set(re.findall(r"\bas\s+([a-z_][a-z0-9_]*)", lowered))
    return tokens - _SQL_NOISE - aliases


def _assert_sql_matches_schema(sql: str, tables: set[str]) -> None:
    allowed = set(tables)
    for table in tables:
        allowed |= _baseline_columns(table)
    unknown = _column_identifiers(sql) - allowed
    assert not unknown, (
        f"SQL references identifiers absent from the baseline schema: "
        f"{sorted(unknown)} — schema drift (see 2026-07-11 incident)"
    )


# ---------------------------------------------------------------------------
# Recording pool stubs
# ---------------------------------------------------------------------------


def _recording_pool(router):
    """MagicMock pool whose fetchrow records SQL and routes rows via ``router``."""
    pool = MagicMock()
    pool.recorded_sql = []

    async def _fetchrow(query, *args, **kwargs):  # noqa: ANN001, ARG001
        pool.recorded_sql.append(query)
        return router(query)

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    return pool


def _newsletter_router(subs: int, last_sent):
    def _route(query: str):
        if "newsletter_subscribers" in query:
            return {"c": subs}
        if "campaign_email_logs" in query:
            return {"last_sent": last_sent}
        raise AssertionError(f"unexpected query: {query}")

    return _route


def _embeddings_router(total: int, embedded: int, last):
    def _route(query: str):
        return {
            "total_posts": total,
            "embedded_posts": embedded,
            "last_embedding": last,
        }

    return _route


def _raising_pool(message: str):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=Exception(message))
    return pool


# ---------------------------------------------------------------------------
# probe_newsletter_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_newsletter_probe_sql_matches_baseline_schema():
    pool = _recording_pool(
        _newsletter_router(subs=3, last_sent=datetime.now(UTC) - timedelta(days=1))
    )
    await probe_newsletter_health(pool)
    assert pool.recorded_sql, "probe issued no SQL"
    for sql in pool.recorded_sql:
        _assert_sql_matches_schema(sql, {"newsletter_subscribers", "campaign_email_logs"})


@pytest.mark.asyncio
async def test_newsletter_probe_counts_active_verified_subscribers():
    """Subscriber count must mirror _get_active_subscribers: verified AND
    not unsubscribed — not the drifted ``confirmed`` column."""
    pool = _recording_pool(
        _newsletter_router(subs=3, last_sent=datetime.now(UTC) - timedelta(days=1))
    )
    await probe_newsletter_health(pool)
    subscriber_sql = pool.recorded_sql[0]
    assert "verified" in subscriber_sql
    assert "unsubscribed_at" in subscriber_sql
    assert "confirmed" not in subscriber_sql


@pytest.mark.asyncio
async def test_newsletter_probe_zero_subscribers_is_ok():
    pool = _recording_pool(_newsletter_router(subs=0, last_sent=None))
    res = await probe_newsletter_health(pool)
    assert res["ok"] is True
    assert res["subscribers"] == 0


@pytest.mark.asyncio
async def test_newsletter_probe_recent_send_is_ok():
    pool = _recording_pool(
        _newsletter_router(subs=5, last_sent=datetime.now(UTC) - timedelta(days=2))
    )
    res = await probe_newsletter_health(pool)
    assert res["ok"] is True
    assert res["subscribers"] == 5


@pytest.mark.asyncio
async def test_newsletter_probe_subscribers_but_no_sends_flags():
    pool = _recording_pool(_newsletter_router(subs=5, last_sent=None))
    res = await probe_newsletter_health(pool)
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_newsletter_probe_missing_relation_reports_bootstrap_state():
    pool = _raising_pool('relation "newsletter_subscribers" does not exist')
    res = await probe_newsletter_health(pool)
    assert res["ok"] is True
    assert "not created yet" in res["detail"]


@pytest.mark.asyncio
async def test_newsletter_probe_column_drift_fails_loud():
    """A missing *column* is a code bug, not a bootstrap state — the probe
    must report it, not mask it as 'tables not created yet'."""
    pool = _raising_pool('column "confirmed" does not exist')
    res = await probe_newsletter_health(pool)
    assert res["ok"] is False
    assert "confirmed" in res["detail"]


# ---------------------------------------------------------------------------
# probe_embeddings_freshness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embeddings_probe_sql_matches_baseline_schema():
    pool = _recording_pool(_embeddings_router(total=10, embedded=9, last=datetime.now(UTC)))
    await probe_embeddings_freshness(pool)
    assert pool.recorded_sql, "probe issued no SQL"
    for sql in pool.recorded_sql:
        _assert_sql_matches_schema(sql, {"posts", "embeddings"})


@pytest.mark.asyncio
async def test_embeddings_probe_filters_on_posts_source_table():
    """embedding_service writes source_table='posts' (plural). Filtering on
    any other column/value silently counts 0 and fires a false alert."""
    pool = _recording_pool(_embeddings_router(total=10, embedded=9, last=datetime.now(UTC)))
    await probe_embeddings_freshness(pool)
    sql = pool.recorded_sql[0]
    assert "source_table = 'posts'" in sql
    assert "source_type" not in sql


@pytest.mark.asyncio
async def test_embeddings_probe_small_gap_is_ok():
    pool = _recording_pool(_embeddings_router(total=10, embedded=8, last=datetime.now(UTC)))
    res = await probe_embeddings_freshness(pool)
    assert res["ok"] is True
    assert res["gap"] == 2


@pytest.mark.asyncio
async def test_embeddings_probe_large_gap_flags():
    pool = _recording_pool(_embeddings_router(total=20, embedded=10, last=datetime.now(UTC)))
    res = await probe_embeddings_freshness(pool)
    assert res["ok"] is False
    assert res["gap"] == 10


@pytest.mark.asyncio
async def test_embeddings_probe_missing_relation_reports_bootstrap_state():
    pool = _raising_pool('relation "embeddings" does not exist')
    res = await probe_embeddings_freshness(pool)
    assert res["ok"] is True
    assert "not created yet" in res["detail"]


@pytest.mark.asyncio
async def test_embeddings_probe_column_drift_fails_loud():
    pool = _raising_pool('column "source_type" does not exist')
    res = await probe_embeddings_freshness(pool)
    assert res["ok"] is False
    assert "source_type" in res["detail"]


# ---------------------------------------------------------------------------
# probe_podcast_health (re-pointed 2026-08-15 — same drift class)
# ---------------------------------------------------------------------------
#
# The podcast probe's failure mode was subtler than the 2026-07-11 pair:
# its SQL was schema-VALID but measured the wrong table entirely
# (pipeline_tasks_view task rows + a topic ILIKE sweep instead of the
# media_assets rows actually produced). These tests pin both properties:
# every identifier exists in the baseline schema, AND the signal source
# is media_assets.created_at.


def _podcast_recording_pool(total: int, last_created):
    """Pool for probe_podcast_health: ``fetch`` answers the app_settings
    read (empty → documented defaults), ``fetchrow`` records SQL and
    returns the media_assets aggregate."""
    pool = MagicMock()
    pool.recorded_sql = []
    pool.fetch = AsyncMock(return_value=[])

    async def _fetchrow(query, *args, **kwargs):  # noqa: ANN001, ARG001
        pool.recorded_sql.append(query)
        return {"total": total, "last_created": last_created}

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    return pool


@pytest.mark.asyncio
async def test_podcast_probe_sql_matches_baseline_schema():
    pool = _podcast_recording_pool(
        total=99, last_created=datetime.now(UTC) - timedelta(days=2)
    )
    await probe_podcast_health(pool)
    assert pool.recorded_sql, "probe issued no SQL"
    for sql in pool.recorded_sql:
        _assert_sql_matches_schema(sql, {"media_assets"})


@pytest.mark.asyncio
async def test_podcast_probe_reads_production_not_task_rows():
    """The staleness signal is media_assets.created_at (production time).

    Not pipeline_tasks (5 task rows ever vs 99 produced assets — a
    near-dead legacy signal), not topic ILIKE (mis-attributes posts that
    merely discuss podcasting), and not updated_at (distribution jobs
    bump it long after production, masking a real stall)."""
    pool = _podcast_recording_pool(
        total=99, last_created=datetime.now(UTC) - timedelta(days=2)
    )
    await probe_podcast_health(pool)
    sql = pool.recorded_sql[0]
    assert "media_assets" in sql
    assert "type = 'podcast'" in sql
    assert "created_at" in sql
    assert "updated_at" not in sql
    assert "pipeline_tasks" not in sql
    assert "ilike" not in sql.lower()


@pytest.mark.asyncio
async def test_podcast_probe_missing_relation_reports_bootstrap_state():
    pool = _raising_pool('relation "media_assets" does not exist')
    pool.fetch = AsyncMock(return_value=[])
    res = await probe_podcast_health(pool)
    assert res["ok"] is True
    assert "not created yet" in res["detail"]


@pytest.mark.asyncio
async def test_podcast_probe_column_drift_fails_loud():
    pool = _raising_pool('column "created_at" does not exist')
    pool.fetch = AsyncMock(return_value=[])
    res = await probe_podcast_health(pool)
    assert res["ok"] is False
    assert "created_at" in res["detail"]
