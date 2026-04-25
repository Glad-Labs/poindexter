"""Handler: ``tap.external_metrics_writer`` — Singer-record consumer
that lands rows in ``external_metrics``.

Bound to the ``record_handler`` column of an ``external_taps`` row
when the tap's stream emits per-post or per-day metrics that should
land in the analytics table. The writer maps a Singer RECORD into
one or more ``external_metrics`` rows, one per metric column the
operator declared.

## Per-tap mapping config

Lives under ``row.config.metrics_mapping``. Shape:

::

    {
      "<stream-name>": {
        "source":      "google_search_console",   # external_metrics.source
        "date_field":  "date",                     # record key holding ISO date
        "post_field":  "post_id" | "slug",         # optional — links the row to a post
        "metric_fields": ["impressions", "clicks", "ctr", "position"],
        "dimension_fields": ["country", "device", "query"]
      }
    }

For each RECORD on ``<stream-name>``:

- One ``external_metrics`` row is inserted **per metric_field**
  (so a record with 4 metrics becomes 4 rows — keeps the table
  schema-stable across taps that emit different metric sets).
- ``dimension_fields`` get bundled into the ``dimensions`` jsonb.
- Missing date or unparseable values cause the record to be skipped
  with a debug log; one bad record never aborts the run.

## Example mapping for tap-google-search-console

::

    "config": {
      "command": "tap-google-search-console",
      "tap_config": { ... GSC-specific ... },
      "metrics_mapping": {
        "performance_report_date": {
          "source": "google_search_console",
          "date_field": "date",
          "post_field": "slug",
          "metric_fields": ["impressions", "clicks", "ctr", "position"],
          "dimension_fields": ["country", "device", "query"]
        }
      }
    }
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


def _parse_date(value: Any) -> _dt.date | None:
    """Singer dates arrive as ISO strings, sometimes with time component."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _extract_slug_from_url_or_path(value: str) -> str | None:
    """Best-effort extraction of a post slug from a Singer-emitted URL or path.

    Handles common shapes:
      https://www.gladlabs.io/posts/the-engine-room-...   -> the-engine-room-...
      /posts/some-slug                                    -> some-slug
      /posts/some-slug/                                   -> some-slug

    Returns None when the value doesn't look like a /posts/<slug> URL.
    """
    if not value:
        return None
    # Strip schema + host if present.
    after_path = value
    if value.startswith(("http://", "https://")):
        try:
            from urllib.parse import urlparse
            after_path = urlparse(value).path
        except Exception:
            return None
    # Look for /posts/<slug> pattern.
    after_path = after_path.strip("/")
    parts = after_path.split("/")
    if len(parts) >= 2 and parts[0] == "posts":
        return parts[1] or None
    return None


def _parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@register_handler("tap", "external_metrics_writer")
async def external_metrics_writer(
    payload: dict[str, Any],
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Land a Singer RECORD into the ``external_metrics`` table.

    ``payload`` shape (set by the singer_subprocess dispatcher):

    ::

        {"stream": "...", "record": {...}, "schema": {...},
         "time_extracted": "..."}
    """
    if pool is None:
        raise RuntimeError("tap.external_metrics_writer: pool unavailable")

    stream = payload.get("stream")
    record = payload.get("record") or {}
    if not stream or not isinstance(record, dict):
        return {"inserted": 0, "skipped": 1, "reason": "no stream/record"}

    config = row.get("config") or {}
    # asyncpg returns jsonb as a raw string unless a codec is registered.
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return {"inserted": 0, "skipped": 1, "reason": "row.config not parseable JSON"}
    if not isinstance(config, dict):
        return {"inserted": 0, "skipped": 1, "reason": "row.config invalid"}

    mappings = config.get("metrics_mapping") or {}
    if not isinstance(mappings, dict) or stream not in mappings:
        # Unmapped stream — operator only configured a subset. Silent skip.
        return {"inserted": 0, "skipped": 1, "reason": f"stream {stream!r} not in metrics_mapping"}

    mapping = mappings[stream]
    if not isinstance(mapping, dict):
        return {"inserted": 0, "skipped": 1, "reason": "mapping not a dict"}

    source = mapping.get("source") or stream
    date_field = mapping.get("date_field", "date")
    post_field = mapping.get("post_field")  # nullable
    metric_fields = mapping.get("metric_fields") or []
    dimension_fields = mapping.get("dimension_fields") or []

    if not metric_fields or not isinstance(metric_fields, list):
        return {"inserted": 0, "skipped": 1, "reason": "no metric_fields configured"}

    date_value = _parse_date(record.get(date_field))
    if date_value is None:
        logger.debug(
            "[tap.external_metrics_writer] %s/%s: skipping record with bad date in %r",
            row.get("name"), stream, date_field,
        )
        return {"inserted": 0, "skipped": 1, "reason": "bad date"}

    # Resolve post_id / slug from whatever the tap emits. Three shapes:
    #   1. post_field = "slug" — value is a clean slug; look up posts.id.
    #   2. post_field = "post_id" — value is already a UUID; trust it.
    #   3. post_field = anything else (e.g. "page" for GSC, "pagePath"
    #      for GA4) — value is typically a URL or path. We try to
    #      extract the slug from a "/posts/<slug>" pattern and look up
    #      posts.id; if we can't extract, we just stash whatever string
    #      we have in the slug column (text) and leave post_id NULL.
    post_id: Any = None
    slug: Any = None
    if post_field:
        raw_ref = record.get(post_field)
        if isinstance(raw_ref, str) and raw_ref:
            if post_field == "slug":
                slug = raw_ref[:255]
                async with pool.acquire() as conn:
                    found = await conn.fetchval(
                        "SELECT id FROM posts WHERE slug = $1", slug,
                    )
                if found:
                    post_id = found
            elif post_field == "post_id":
                # Operator's responsibility to provide a UUID string.
                # If it's not a UUID we'd rather skip the FK than crash;
                # let asyncpg validate at INSERT time.
                post_id = raw_ref
            else:
                # URL or path — try to extract a slug.
                extracted = _extract_slug_from_url_or_path(raw_ref)
                if extracted:
                    slug = extracted[:255]
                    async with pool.acquire() as conn:
                        found = await conn.fetchval(
                            "SELECT id FROM posts WHERE slug = $1", slug,
                        )
                    if found:
                        post_id = found
                else:
                    # Stash the raw value in the slug text column so we
                    # at least preserve it for analytics. post_id stays NULL.
                    slug = raw_ref[:255]

    # Build dimensions jsonb from non-metric, non-date fields.
    dimensions: dict[str, Any] = {}
    for dim_field in dimension_fields:
        if dim_field in record:
            dimensions[dim_field] = record[dim_field]

    inserted = 0
    skipped_metrics = 0

    async with pool.acquire() as conn:
        for metric_name in metric_fields:
            metric_value = _parse_numeric(record.get(metric_name))
            if metric_value is None:
                skipped_metrics += 1
                continue

            await conn.execute(
                """
                INSERT INTO external_metrics
                  (source, metric_name, metric_value, dimensions,
                   post_id, slug, date, fetched_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, now())
                """,
                source,
                metric_name,
                metric_value,
                json.dumps(dimensions),
                post_id,
                slug,
                date_value,
            )
            inserted += 1

    return {"inserted": inserted, "skipped": skipped_metrics}
