# Handler: `tap.youtube_reporting`

Pull YouTube Reporting API bulk reports and land them in `external_metrics`, through the same [`external_metrics_writer`](/docs/integrations/tap_external_metrics_writer) every other metrics tap uses. One `external_taps` row subscribes to one report. Three rows are seeded:

| Row                | Report                      | `source`           | What it holds                                                                              |
| ------------------ | --------------------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| `youtube_reach`    | `channel_reach_basic_a1`    | `youtube`          | Thumbnail impressions and thumbnail click-through rate.                                    |
| `youtube_activity` | `channel_basic_a3`          | `youtube_activity` | Views, engaged views, watch minutes, likes, comments, shares, subscribers gained and lost. |
| `youtube_traffic`  | `channel_traffic_source_a3` | `youtube_traffic`  | Views and watch minutes per traffic source; for YouTube search, the search term.           |

Until 2026-10-18 only `youtube_reach` existed. A Short is mostly watched from the Shorts feed, where a swipe is not a thumbnail impression, so the one number collected missed most of the channel, and nothing read it. The activity and traffic rows close that gap, and [`youtube_performance`](#reading-it) reads all three.

**Each row writes its own `source`.** Both new reports have a `views` column, and a shared source would let anything summing `views` count every view twice.

## How the Reporting API delivers data

From Google's [bulk reports guide](https://developers.google.com/youtube/reporting/v1/reports):

- A **reporting job** is created once per report type. The handler creates it on its first run and caches the id in the row's `state`.
- YouTube writes one CSV per Pacific-time day. The first arrives **within 48 hours** of the job's creation, along with reports for the **30 days before** the job existed.
- Reports stay downloadable for 60 days (30 for the historical ones).
- A corrected day arrives as a NEW report with a new id. The writer's natural-key upsert makes it overwrite the earlier value.

## Row configuration

The `youtube_reach` row; the other two differ only in `name`, `tap_type` and `config`.

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
- **Missing scope or disabled API:** the run raises with the fix above. The tap runner records it on the row (`poindexter taps show <row>`) and raises a `tap_failure` finding, routed from `tap_failure_alert_after_consecutive` failures on.
- **Anything else** (5xx, network): raises and retries on the next run. Landed reports are remembered one at a time, so a run that dies half-way resumes where it stopped.

## Split rows: store counts, derive averages

`channel_basic_a3` and `channel_traffic_source_a3` split each video's day by `live_or_on_demand`, `subscribed_status` and `country_code` (the traffic report also by `traffic_source_type` and `traffic_source_detail`). Those columns are all in the rows' `dimension_fields`, so they are part of the writer's natural key and one split row never overwrites the next.

Only additive counts are stored. The reports also carry `average_view_duration_seconds` and `average_view_duration_percentage`, but each is the average of one split row, and summing them across rows gives nonsense. Average view duration is therefore watch minutes × 60 / views, and retention is that over the video's length from `media_assets.duration_ms`. A video the pipeline did not render has no length there, so it has no retention.

`traffic_source_type` is a numeric code (`5` = YouTube search, `7` = suggested videos, `24` = Shorts feed; the full table is in [the dimensions reference](https://developers.google.com/youtube/reporting/v1/reports/dimensions) and in `services/youtube_performance.py::TRAFFIC_SOURCE_NAMES`). For code `5`, `traffic_source_detail` is the search term.

## Reading it

```bash
poindexter integrations youtube performance --days 28
```

prints, per video, views, average view seconds, the share kept, net subscribers, thumbnail impressions and CTR, then views by type, where the views came from, and the top search terms. The **YouTube** Grafana board (`/d/youtube`) shows the same numbers over the dashboard's time range. Both read through `services/youtube_performance.py` (the board repeats its SQL), so the same rules apply to each.

`video_thumbnail_impressions_ctr` is a **fraction**: measured values run 0.005–0.25 (a Short with 293 impressions at 0.0443 is 13 clicks). It is a per-day rate, so a window's CTR weights each day by its impressions rather than averaging the days.

YouTube's reports trail by about two days, so the last two days of any window read low.
