# SEO Harvest Loop

The harvest loop turns Search Console data we already collect into a ranked
"fix these posts" list. **Phase 1 (this doc) is read-only** — it classifies
published posts into opportunity tiers and writes them to `seo_opportunities`.
No content is modified. Design spec:
[`docs/superpowers/specs/2026-06-11-seo-harvest-loop-design.md`](../superpowers/specs/2026-06-11-seo-harvest-loop-design.md).

## Why

Live Search Console data showed the site earns ~600K impressions but ~0.1%
CTR: ~70% of posts rank in striking distance (position 5–20) but get almost no
clicks. The fastest SEO win is harvesting impressions already earned, not
writing new posts. Phase 1 surfaces exactly which posts to fix.

## Tiers

- **page1_push** — ranks position 3–10 with real impressions; one optimization
  from page 1. The fastest wins.
- **striking_distance** — ranks position 5–20; on page 2, close.
- **low_ctr** — ranks somewhere with impressions but the title/meta isn't
  earning the click.

Each post is assigned its single highest-priority tier. `gap_score` (estimated
clicks/month left on the table = `impressions × (target_ctr − current_ctr)`)
orders the "fix this first" list.

## How it runs

`RunSeoOpportunityAnalyzerJob`
([`services/jobs/run_seo_opportunity_analyzer.py`](../../src/cofounder_agent/services/jobs/run_seo_opportunity_analyzer.py))
runs daily, reads the latest `post_performance` snapshot per published post,
classifies via
[`services/seo/striking_distance.py`](../../src/cofounder_agent/services/seo/striking_distance.py),
and upserts `seo_opportunities` (one row per post, recomputed each run). A
findings summary fires when page-1-push candidates exist.

The analyzer lives in substrate (`services/seo/`) rather than
`modules/content/` because Phase 1 is pure analytics over substrate tables; the
content-_mutating_ refresh atoms (Phase 2) go in `modules/content/atoms/`.

## Tuning (all `app_settings`)

`seo.harvest.analyzer_enabled` (default `true`, read-only/safe),
`seo.striking_distance.position_min` / `position_max`,
`seo.push_candidate.position_min` / `position_max` / `min_impressions`,
`seo.low_ctr.min_impressions` / `max_ctr`,
`seo.opportunity.target_ctr` (gap-score target CTR).
The content-mutating refresh path (Phase 2) gates separately on
`seo.refresh.enabled` (default `false`).

## Where to look

Grafana → **SEO Harvest** dashboard (`/d/seo-harvest`): tier counts, the ranked
top-opportunities table, and the sitewide CTR trend.
