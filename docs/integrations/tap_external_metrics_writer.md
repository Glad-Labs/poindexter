# Handler: `tap.external_metrics_writer`

Singer-record consumer for analytics-shaped taps. Maps each Singer RECORD into one row per metric in `external_metrics`. Bound to the `record_handler` column of an `external_taps` row that runs `tap.singer_subprocess`.

## Mapping config

Lives under `row.config.metrics_mapping`. One key per stream the operator wants to persist:

```json
{
  "performance_report_date": {
    "source": "google_search_console",
    "date_field": "date",
    "post_field": "slug",
    "metric_fields": ["impressions", "clicks", "ctr", "position"],
    "dimension_fields": ["country", "device", "query"]
  },
  "page_metrics": {
    "source": "ga4",
    "date_field": "date",
    "post_field": "slug",
    "metric_fields": [
      "sessions",
      "users",
      "engaged_sessions",
      "engagement_rate"
    ],
    "dimension_fields": ["country", "device_category"]
  }
}
```

| Field              | Meaning                                                                                                                                                             |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source`           | Goes into `external_metrics.source` (e.g. `google_search_console`, `ga4`, `cloudflare`). Defaults to the stream name.                                               |
| `date_field`       | Record key holding the ISO date the metric is for. Defaults to `"date"`.                                                                                            |
| `post_field`       | _Optional._ Record key holding either a `slug` (looked up against `posts` to fill `post_id`) or a `post_id` directly.                                               |
| `metric_fields`    | List of record keys to persist. **One `external_metrics` row is inserted per metric_field** — keeps the table schema stable across taps with different metric sets. |
| `dimension_fields` | List of record keys to bundle into the `dimensions` jsonb column.                                                                                                   |

## What gets written

For each Singer RECORD on a mapped stream:

```sql
INSERT INTO external_metrics
  (source, metric_name, metric_value, dimensions, post_id, slug, date, fetched_at)
VALUES (...)
```

If a record has 4 metrics declared, 4 rows are inserted. The `dimensions` jsonb is identical across the 4 rows, so post-hoc queries can `GROUP BY source, dimensions, date`.

## Failure handling

- Missing/unparseable `date_field` → record skipped, debug log only. One bad row never aborts the run.
- Stream not in `metrics_mapping` → silent skip. Operators commonly enable a tap with many streams but only configure mapping for a subset.
- Metric value missing or non-numeric → that one metric is skipped; other metrics in the same record still insert.
- `post_field=slug` with no matching post → `post_id` left NULL; the row still inserts (the FK is `ON DELETE SET NULL`).

## Why one-row-per-metric

Two reasons:

1. **Schema stability.** `tap-google-search-console` emits `(impressions, clicks, ctr, position)`. `tap-ga4` emits `(sessions, users, engaged_sessions, engagement_rate)`. Storing each metric in its own row means new taps don't require ALTERing the table.

2. **Time-series friendliness.** `external_metrics` is meant for slicing by `(source, metric_name, date)` patterns, which a row-per-metric layout serves directly. The `dimensions` jsonb captures everything else.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Companion: `tap.singer_subprocess` (the dispatcher that calls this handler)
- GH-27 (`external_metrics` is one of the 8 feedback-loop tables this populates)
