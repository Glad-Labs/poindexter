"""integration_db: the external_metrics tap writer is idempotent.

Regression cover for the Singer-tap duplication bug — the GA4 / GSC taps
re-emit their whole backfill window every run, and the writer used a bare
``INSERT`` with no unique key, so ``external_metrics`` accumulated one copy
per run (GA4 was 99.8% duplicates, GSC 94%). Any ``SUM(metric_value)`` over
the table — e.g. ``rollup_post_performance``'s GSC impressions/clicks — read
inflated by the run count.

The fix is a natural-key unique index ``(source, metric_name, date, slug,
dimensions)`` (NULLS NOT DISTINCT) plus an ``ON CONFLICT ... DO UPDATE``
upsert in the writer, so a re-emitted row overwrites in place (last-write-wins,
which also lets GSC's maturing values self-correct).

These tests drive that behavior:

- identical records collapse to one row (dedup),
- a re-emitted row updates the value in place (last-write-wins),
- records that differ only by ``dimensions`` stay distinct (the key is not
  over-collapsing — GSC web vs image are legitimately separate rows),
- the unique index actually exists after the migration chain.
"""

from __future__ import annotations

import pytest

from services.integrations.handlers.tap_external_metrics_writer import (
    external_metrics_writer,
)

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


def _row(source: str, dimension_fields: list[str] | None = None) -> dict:
    """A minimal external_taps-shaped row wiring one stream to `source`."""
    return {
        "name": "test_dedup_tap",
        "config": {
            "metrics_mapping": {
                "perf": {
                    "source": source,
                    "date_field": "date",
                    "post_field": "slug",
                    "metric_fields": ["clicks"],
                    "dimension_fields": dimension_fields or [],
                }
            }
        },
    }


def _payload(*, slug: str, clicks: float, dims: dict | None = None) -> dict:
    record = {"date": "2026-06-29", "slug": slug, "clicks": clicks}
    if dims:
        record.update(dims)
    return {"stream": "perf", "record": record}


async def _count(pool, source: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM external_metrics WHERE source = $1", source
        )


async def _cleanup(pool, source: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_metrics WHERE source = $1", source
        )


async def test_identical_records_collapse_to_one_row(test_pool):
    source = "test_dedup_identical"
    row = _row(source)
    payload = _payload(slug="dedup-fixture", clicks=2.0)
    try:
        await external_metrics_writer(payload, site_config=None, row=row, pool=test_pool)
        await external_metrics_writer(payload, site_config=None, row=row, pool=test_pool)
        assert await _count(test_pool, source) == 1
    finally:
        await _cleanup(test_pool, source)


async def test_reemitted_record_updates_value_in_place(test_pool):
    source = "test_dedup_last_write_wins"
    row = _row(source)
    try:
        # GSC finalises data over days: an early pull says 0, a later pull
        # says 5 for the same (slug, date). The later write must win.
        await external_metrics_writer(
            _payload(slug="maturing", clicks=0.0), site_config=None, row=row, pool=test_pool
        )
        await external_metrics_writer(
            _payload(slug="maturing", clicks=5.0), site_config=None, row=row, pool=test_pool
        )
        async with test_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT metric_value FROM external_metrics WHERE source = $1", source
            )
        assert len(rows) == 1
        assert float(rows[0]["metric_value"]) == 5.0
    finally:
        await _cleanup(test_pool, source)


async def test_distinct_dimensions_stay_separate(test_pool):
    source = "test_dedup_dimensions"
    row = _row(source, dimension_fields=["search_type"])
    try:
        # web + image are legitimately separate GSC rows for the same
        # (slug, date, metric); a second web row must collapse onto the first.
        await external_metrics_writer(
            _payload(slug="s", clicks=1.0, dims={"search_type": "web"}),
            site_config=None, row=row, pool=test_pool,
        )
        await external_metrics_writer(
            _payload(slug="s", clicks=1.0, dims={"search_type": "image"}),
            site_config=None, row=row, pool=test_pool,
        )
        await external_metrics_writer(
            _payload(slug="s", clicks=1.0, dims={"search_type": "web"}),
            site_config=None, row=row, pool=test_pool,
        )
        assert await _count(test_pool, source) == 2
    finally:
        await _cleanup(test_pool, source)


async def test_distinct_query_dimension_stays_separate(test_pool):
    # Same page, same date, two different search queries: two GSC rows.
    # A re-emit of one of them must dedup onto itself, not create a third row.
    source = "test_dedup_query"
    row = _row(source, dimension_fields=["query"])
    try:
        await external_metrics_writer(
            _payload(slug="my-post", clicks=1.0, dims={"query": "docker compose tutorial"}),
            site_config=None, row=row, pool=test_pool,
        )
        await external_metrics_writer(
            _payload(slug="my-post", clicks=2.0, dims={"query": "docker compose vs kubernetes"}),
            site_config=None, row=row, pool=test_pool,
        )
        await external_metrics_writer(
            _payload(slug="my-post", clicks=1.0, dims={"query": "docker compose tutorial"}),
            site_config=None, row=row, pool=test_pool,
        )
        assert await _count(test_pool, source) == 2
    finally:
        await _cleanup(test_pool, source)


async def test_site_level_null_slug_records_dedup(test_pool):
    # GSC's performance_report_date stream has no post_field, so slug is NULL
    # (site-wide daily aggregate). The NULLS NOT DISTINCT index is what makes
    # these dedup across runs — a plain unique index would treat every NULL as
    # distinct and let them accumulate.
    source = "test_dedup_null_slug"
    row = {
        "name": "test_dedup_tap",
        "config": {
            "metrics_mapping": {
                "perf": {
                    "source": source,
                    "date_field": "date",
                    "metric_fields": ["clicks"],
                    "dimension_fields": ["search_type"],
                }
            }
        },
    }
    payload = {
        "stream": "perf",
        "record": {"date": "2026-06-29", "clicks": 3.0, "search_type": "web"},
    }
    try:
        await external_metrics_writer(payload, site_config=None, row=row, pool=test_pool)
        await external_metrics_writer(payload, site_config=None, row=row, pool=test_pool)
        async with test_pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT count(*) FROM external_metrics WHERE source = $1", source
            )
            null_slug = await conn.fetchval(
                "SELECT count(*) FROM external_metrics WHERE source = $1 AND slug IS NULL",
                source,
            )
        assert total == 1
        assert null_slug == 1, "site-level row should have NULL slug and still dedup"
    finally:
        await _cleanup(test_pool, source)


async def test_natural_key_unique_index_present(test_pool):
    async with test_pool.acquire() as conn:
        defs = await conn.fetch(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'external_metrics'"
        )
    blob = " ".join(r["indexdef"] for r in defs)
    assert "UNIQUE" in blob, "external_metrics has no unique index"
    for col in ("source", "metric_name", "date", "slug", "dimensions"):
        assert col in blob, f"unique index missing key column {col!r}"
