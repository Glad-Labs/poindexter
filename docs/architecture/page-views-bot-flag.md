# page_views bot-flag & the raw-vs-human boundary

> Upstream of everything here: the ingest cursor must never advance past
> `now() - ingestion_lag_seconds`, or rows still inside CF Analytics Engine's
> write→queryable delay are lost permanently —
> [analytics-ingestion-lag.md](analytics-ingestion-lag.md).

The first-party beacon (`CF Analytics Engine → sync_cloudflare_analytics →
page_views`) is inflated by stealth scrapers that present a browser
`User-Agent` and slip the ingest UA drop-filter. `FlagBotPageViewsJob`
(`services/jobs/flag_bot_page_views.py`, 15-min) flags them via two windowed
signatures, materialized as `page_views.is_bot`:

- **Flood cap** — a `(user_agent, path)` pair hit past the cap in the window
  → the pair's entire history flagged (`flood:ua_path`).
- **Path sweep** (poindexter#973) — a single `user_agent` hitting more than
  `beacon_sweep_max_distinct_paths` DISTINCT paths in the window → that UA's
  _window_ rows flagged (`sweep:ua_distinct_paths`). Catches full-site
  crawlers that visit each page once — structurally invisible to the pair cap
  (one hit per path never repeats a pair; observed 147 hits / 145 paths on
  2026-07-26, which poisoned the traffic-anomaly baseline for a week).
  Window-scoped on purpose: bare UA strings are shared across real humans, so
  whole-history flagging on a UA alone would over-flag — and for the same
  reason this signature has no all-history backfill twin.

## Two surfaces

| Question             | Reads                               | Consumers                                                                                                                 |
| -------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Is the pipe flowing? | raw `page_views`                    | Grafana capture-dead alert, Mission-Control "Page-views received", pipeline funnel `is_viewed`, DB freshness + row-count  |
| How many readers?    | `page_views_human` (`is_bot=false`) | console `/api/analytics/views`, `posts.view_count`, `lab_outcomes_v1.views_*_post_publish`, Grafana traffic-anomaly alert |

A bot hit still proves the URL is live and ingest is fresh, so liveness stays on
raw. Reader counts move to the human view — and so does the traffic-anomaly
alert (2026-07-25): it detects _relative drops_, and a bot flood inflates its
7-day baseline so the flood's retreat reads as a week of phantom drops (113
false fires 2026-06-26→07-26 while human traffic was flat). Capture-dead keeps
the raw read and owns the pipe-liveness case.

## Tunables (`app_settings`)

- `beacon_bot_flag_enabled` (default `true`) — master switch.
- `beacon_flood_window_hours` (`24`) — flood window.
- `beacon_flood_cap_per_window` (`20`) — per-`(UA,path)` cap; over → whole pair flagged.
- `beacon_flood_backfill_cap` (`30`) — one-time all-history backfill cap.
- `beacon_sweep_max_distinct_paths` (`25`) — per-UA distinct-path cap; over →
  the UA's window rows flagged. ~25 distinct pages/day is a sixth of the live
  corpus — fanatical for a human reader; raise it if operator browsing sessions
  start getting caught (flags are reversible, see Notes).

## Notes

- `posts.view_count` is recomputed from `page_views_human` by the flag job — it
  is the single writer. The sync ingest no longer bumps it.
- Flagging is monotonic (only sets `is_bot` true). To re-open after raising a
  cap: `UPDATE page_views SET is_bot=false WHERE bot_reason LIKE 'flood:%'` (or
  `'sweep:%'` for the path-sweep signature) then let the next sweep re-evaluate.
- No IP signal exists on the beacon (CF bot-score is plan-gated); the flood-cap
  is the near-term heuristic. Bot rows are retained (audit), not dropped.
- The one-time backfill is guarded by the `beacon_bot_flag_backfilled`
  app_settings sentinel, so it runs once and no-ops thereafter.
