# Handler: `tap.youtube_reporting`

Pull YouTube Reporting API bulk reports and land them in `external_metrics`, through the same [`external_metrics_writer`](/docs/integrations/tap_external_metrics_writer) every other metrics tap uses. The seeded row, `youtube_reach`, reads the **reach report**: thumbnail impressions and thumbnail click-through rate per video per day. A thumbnail exists to move exactly that number, and before this tap no YouTube number reached the database at all.

## How the Reporting API delivers data

From Google's [bulk reports guide](https://developers.google.com/youtube/reporting/v1/reports):

- A **reporting job** is created once per report type. The handler creates it on its first run and caches the id in the row's `state`.
- YouTube writes one CSV per Pacific-time day. The first arrives **within 48 hours** of the job's creation, along with reports for the **30 days before** the job existed.
- Reports stay downloadable for 60 days (30 for the historical ones).
- A corrected day arrives as a NEW report with a new id. The writer's natural-key upsert makes it overwrite the earlier value.

## Row configuration

```
name:             youtube_reach
handler_name:     youtube_reporting
tap_type:         youtube_reach            (informational)
target_table:     external_metrics
record_handler:   external_metrics_writer
schedule:         every 12 hours
enabled:          true
```

`config`:

| Key                       | Default                       | Meaning                                                                                                                                                                                                      |
| ------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `report_type_id`          | `channel_reach_basic_a1`      | Which report. Basic = date, channel_id, video_id, `video_thumbnail_impressions`, `video_thumbnail_impressions_ctr`. `channel_reach_combined_a1` adds traffic source, operating system and device dimensions. |
| `job_name`                | `poindexter-<report_type_id>` | Name given to the job if one has to be created. An existing job for the report type is reused, whatever its name.                                                                                            |
| `max_reports_per_run`     | `60`                          | Reports landed per run, oldest first.                                                                                                                                                                        |
| `include_unmapped_videos` | `true`                        | Keep rows for videos no `media_assets` row knows (uploaded outside the pipeline), with `post_id` NULL.                                                                                                       |
| `metrics_mapping`         | see below                     | The writer's mapping, keyed by the report type id.                                                                                                                                                           |

```json
"metrics_mapping": {
  "channel_reach_basic_a1": {
    "source": "youtube",
    "date_field": "date",
    "post_field": "post_id",
    "metric_fields": ["video_thumbnail_impressions", "video_thumbnail_impressions_ctr"],
    "dimension_fields": ["video_id", "medium"]
  }
}
```

The handler adds two fields to every report row before the writer sees it:

- `post_id`: the post whose `media_assets` row holds that YouTube id.
- `medium`: `long` for a `video` asset, `short` for `video_short`, or `unknown`.

Keep `video_id` in `dimension_fields`. With `post_id` as the post field the writer leaves `slug` NULL, so `dimensions` is the only per-video part of the natural key.

## One-time setup

Two operator steps:

1. **Grant the analytics scope.** Scopes live in the token, not in code:

   ```bash
   poindexter integrations youtube setup --with-analytics
   ```

   This keeps every scope the stored token already holds; `--reset-scopes` narrows on purpose. Check the result with `poindexter integrations youtube scopes`.

2. **Enable the YouTube Reporting API** for the OAuth client's Google Cloud project: APIs & Services → Library → "YouTube Reporting API". It is a separate API from the YouTube Data API the uploads use.

**Cadence:** the tap runner (`jobs/run_taps.py`) walks every enabled tap
hourly and does not honour a row's `schedule` yet, so this row runs hourly.
Each run lands only reports it has not seen, so the extra runs are cheap, and a
failing run raises a finding at most once per `findings.tap_failure.cooldown_minutes`.

## Failure posture

- **YouTube publishing not set up** (`plugin.publish_adapter.youtube.enabled` false or the OAuth secrets missing): a quiet 0-record run. Most installs never publish to YouTube, so that zero is legitimate.
- **Missing scope or disabled API:** the run raises with the fix above. The tap runner records it on the row (`poindexter taps show youtube_reach`) and raises a `tap_failure` finding, routed from `tap_failure_alert_after_consecutive` failures on.
- **Anything else** (5xx, network): raises and retries on the next run. Landed reports are remembered one at a time, so a run that dies half-way resumes where it stopped.

## Reading it

```sql
-- Thumbnail CTR per video over its reported days, with the post it belongs to.
SELECT p.title,
       m.dimensions->>'medium'  AS medium,
       sum(m.metric_value) FILTER (WHERE m.metric_name = 'video_thumbnail_impressions') AS impressions,
       avg(m.metric_value) FILTER (WHERE m.metric_name = 'video_thumbnail_impressions_ctr') AS avg_daily_ctr
  FROM external_metrics m
  LEFT JOIN posts p ON p.id = m.post_id
 WHERE m.source = 'youtube'
 GROUP BY 1, 2
 ORDER BY impressions DESC NULLS LAST;
```

The docs do not say whether `video_thumbnail_impressions_ctr` is a fraction or a percentage. The handler stores the value exactly as delivered. Compare the first reports with YouTube Studio's Reach tab before building thresholds on it.
