# The AE ingestion-lag window (why the ingest cursors trail wall-clock)

Two jobs pull from Cloudflare Analytics Engine with a high-water-mark cursor —
`SyncCloudflareAnalyticsJob` (the first-party page-view beacon → `page_views`)
and `SyncAffiliateClicksJob` (`/go/<code>` redirects → `affiliate_link_clicks`).
Both walk their dataset the same way:

```sql
SELECT ... FROM analytics_events WHERE timestamp > toDateTime('{since}', 'UTC')
```

**Cloudflare AE does not make a data point queryable the instant
`writeDataPoint` returns.** There is a non-zero, non-uniform visibility
delay. That single fact is the whole hazard: a row that is _written_ but not
yet _visible_ when a poll runs is silently absent from the result set, and if
that poll then advances `since` past the row's own timestamp, the row is
**below the cursor forever** the moment it appears. Nothing errors. Nothing
retries. The view is simply gone.

## How it was found (2026-08-31, stack#3523)

Two identical beacons to `/__origin-enforcement-e2e` — same path, same UA,
same job, same config. A clean natural control:

| Written    | Preceding poll | Outcome                                                                                                                                                   |
| ---------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `19:32:46` | `19:33:09`     | **Lost.** Poll ran 23 s after the write, saw nothing, advanced the cursor to `19:33:09`. The row surfaced moments later at `19:32:46` — below the cursor. |
| `19:45:04` | `19:43:09`     | **Ingested.** Written _after_ its preceding poll, so no empty poll ever stepped over it. Picked up at `19:48:16`.                                         |

Both rows were confirmed present in AE via the SQL API; only one ever reached
`page_views`. Backfilling the same comparison across August: **40 genuine
reader page views were in AE and absent from `page_views` — 7.3 % of the 546
rows that passed the job's own bot/empty filters.**

The empty-response path was the worst case (it advanced straight to `now()`),
and on a low-traffic site _nearly every poll is empty_, so nearly every real
page view was exposed. But the success path shared the race: AE visibility is
not strictly ordered, so a row written at `max_ts - 2s` can surface after the
row at `max_ts` that set the cursor.

## The rule

One expression in one place — `services/watermark_cursor.py::next_high_water()`
— applied to **both** watermark paths of **both** jobs:

```python
new = max(since, min(observed_ceiling, now - ingestion_lag_seconds))
```

`observed_ceiling` is the newest row the batch actually saw, or `now()` when
Cloudflare returned nothing.

- `min(..., horizon)` keeps a row that is still inside its visibility delay
  **above** the cursor, so the next cycle re-selects it.
- `max(since, ...)` keeps the cursor **monotonic**. A widened margin, a clock
  skew, or a stale row in the batch can only stall it — never rewind it into
  an ever-widening re-scan.
- The AE query is `timestamp > since` (strict), so the cursor always
  progresses: an empty response advances to the horizon, and a batch whose
  rows were all filtered (bots, dedup) advances to the horizon too.

## The costs, and why they are free

**The cursor trails wall-clock by the margin**, so `page_views` is up to
~2 × the margin behind real time. Nothing reads it on a tighter deadline —
liveness/freshness signals tolerate minutes, and the reader surfaces are
daily-or-wider rollups.

**Each poll re-pulls roughly one margin's worth of already-ingested rows.**
Two existing mechanisms make that overlap free, and both must stay:

1. The insert-time `(slug, path, created_at, user_agent)` dedup probe skips
   anything already in `page_views`, so the table never gains a duplicate.
2. The rollups are **recomputes, never increments**, so a re-pulled row
   cannot double-count: `posts.view_count` is a full `COUNT(*)` from
   `page_views_human`, owned solely by `FlagBotPageViewsJob` (the sync job's
   old incremental bump was removed for the bot-flag work — do not restore it,
   see [page-views-bot-flag.md](page-views-bot-flag.md)), and
   `_rollup_clicks` recomputes `affiliate_links.clicks` from
   `affiliate_link_clicks`.

Any future caller of `next_high_water` must bring both properties with it.

The rule is **shared, not copied**. Both jobs already share their
transient-network posture (`services/net_transient.py`, stack#3161) for the
same reason: a fix — or a re-tune — that reaches only half the ingest surface
is worse than one that reaches neither, because the working half makes the gap
invisible. `TestBothIngestsShareTheRule` fails if either job grows its own
copy, or writes a watermark from a bare `now()` / raw `max_ts` again.

## Tunable

`config.ingestion_lag_seconds` on either job's `plugin.job.<name>` row (default
`300`, one poll interval). Same job-config seam as `batch_size` and
`lookback_hours` — edit the JSON row, no redeploy.

`0` collapses the horizon onto `now()`, restoring the pre-2026-08-31 behaviour
exactly. That is an escape hatch for a future in which CF makes writes
immediately visible, **not** a tuning knob: setting it to `0` re-opens the
data-loss window. `TestIngestionLagRace::test_margin_zero_reproduces_the_loss`
pins that by driving four real cycles with the margin disabled and asserting
the row is _still lost_ — it demonstrates the mechanism rather than merely
asserting the fix.

## Recovering rows already lost

AE retains far more than the cursor ever reached — measured 2026-08-31:
**7,544 rows back to 2026-06-03** (~90 days). Diffing that whole window
against `page_views`, applying the job's own bot/empty filters, **344 real
page views are recoverable — 5.1% of the 6,775 that should have been
ingested.** So the loss was not a one-off; it ran at a few percent
continuously for as long as AE can still prove it.

Rewinding the watermark once re-pulls them. The re-pull is safe precisely
because of the dedup + recompute properties above — and because the cursor
now advances by what each batch _read_, so a mostly-deduped backfill batch
cannot skip the rows its `LIMIT` has not reached yet:

```bash
poindexter settings set cloudflare_analytics_last_sync 2026-06-03T00:00:00+00:00
```

Each 5-minute fire pulls up to `config.batch_size` (default 5000) rows,
inserts only what is missing, and leaves the cursor on the newest row it read;
a full-window rewind therefore drains over two fires. `FlagBotPageViewsJob`
recomputes `view_count` on its next pass — no double counting, by
construction. The affiliate ingest recovers the same way via
`affiliate_clicks_last_sync`.

**Do this after the fix is deployed**, not before: the old code would re-lose
rows at the tail of the window as fast as the backfill recovered the head.
