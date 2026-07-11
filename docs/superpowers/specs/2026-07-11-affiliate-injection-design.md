# Affiliate-link injection + `/go` click tracking — Design

- **Date:** 2026-07-11
- **Status:** Design approved; ready for implementation plan
- **Branch:** `claude/workspace-referral-status-109666`

## 1. Context & motivation

Glad Labs had an affiliate-link auto-injection feature in early 2026
(`services/affiliate_linker.py`, added `5f566c248`), backed by a populated
`affiliate_links` DB table. The table was dropped as a "dead table" in the
`#686` cleanup sweep (2026-05-28) and the service was removed in a bulk
dead-code purge (`40972fddb`) — neither because the feature was broken, but
because it had never been rewired into the current atom/graph_def pipeline.

The populated rows were recovered from the pre-drop full DB dump
`gladlabs-db-2026-04-09_020001.dump` (11 rows; the only ones carrying real
referral codes are Mercury, Railway, and Grafana Cloud — the last two are for
services Glad Labs has since left). Real referral URLs live **DB-only, never in
source** (a fabricated-code leak in March 2026 established this rule; see
`.shared-context/decisions/DECISION-LOG.md`).

This design rebuilds the feature to current architecture, improving on the old
one: relevance via curated seed, FTC disclosure (previously absent),
first-party click tracking, and no bulk rewrites of live posts.

## 2. Decisions (locked)

| Dimension      | Decision                            | Rationale                                                                                             |
| -------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Behavior       | Curated & relevance-gated           | Fits the quality bar / curate-over-output stance; recovered link set is thin                          |
| Content scope  | **New posts only**                  | Simplest, safest; no edit-published-post path, no bulk-approval flow                                  |
| Matching       | **Keyword-only, trust the seed**    | Curation lives in what gets seeded; guardrails = first-mention + per-post cap. No niche/tag machinery |
| Click tracking | **Redirect tracker `/go/<code>`**   | First-party data + decouples merchant URL from post content (edit one DB row, not published posts)    |
| Redirect host  | **Standalone Cloudflare Worker**    | Mirrors the existing page-views beacon; redirect + logging in one edge hop                            |
| Disclosure     | **Top-of-post banner, conditional** | FTC clear-and-conspicuous, before the links; DB-configurable text                                     |

## 3. Architecture & data flow

```
GENERATION (local, in-pipeline)                    READER (public edge)
─────────────────────────────────                 ──────────────────────────
writer draft (markdown)                            reader clicks /go/<code>
  │                                                     │
  ▼  content.inject_affiliate_links (NEW atom)          ▼  CF Worker: affiliate-redirect
  • keyword match, first-mention only               • look up code→url in affiliate-links.json (R2)
  • cap N links/post (setting)                      • writeDataPoint → AE dataset 'affiliate_clicks'
  • skip code blocks / headings / existing links    • 302 → real merchant URL
  • rewrite mention → [text](/go/<code>)                │
  • set state flag has_affiliate_links = true           ▼
  │                                                 SyncAffiliateClicksJob (every 5 min)
  ▼  QA rails vet the draft (critic, url_validation)   • AE SQL API → affiliate_link_clicks table
  ▼  publish → static export to R2:                    • rollup → affiliate_links.clicks
     • posts/<slug>.json  (has_affiliate_links flag)   •         → post_performance.affiliate_clicks
     • affiliate-links.json  (active code→url map)      │
  │                                                     ▼
  ▼  frontend renders disclosure banner            Grafana: clicks by link / post / day
     when has_affiliate_links = true
```

Each piece hangs off an existing seam: the atom sits beside
`resolve_internal_link_placeholders`; the export rides `rebuild_static_export`;
the Worker + sync job clone the page-views beacon
(`infrastructure/cloudflare/page-views-beacon/` + `SyncCloudflareAnalyticsJob`).

## 4. Components

### 4.1 Schema (migration = DDL only; no real URLs committed)

Restore `affiliate_links`, adapted from the recovered schema:

```
affiliate_links
  id            serial PK
  code          varchar UNIQUE   -- NEW: stable slug for /go/<code> (e.g. "mercury")
  keyword       varchar UNIQUE   -- body match target
  url           text             -- real merchant/referral URL (DB-only)
  display_text  varchar          -- link text (defaults to keyword)
  program       varchar          -- e.g. "Mercury Referral"
  is_active     boolean          -- only active rows inject + export
  clicks        integer          -- rollup cache
  created_at / updated_at
```

```
affiliate_link_clicks   (NEW — mirrors page_views, one row per click event)
  id, code, post_slug, referrer, country, user_agent, created_at
```

Fresh install ships **both tables empty**. Real referral rows are added via the
operator CLI (§4.3), never seeded in `settings_defaults.py` or
`baseline.seeds.sql`. The recovered Mercury + new Google Workspace rows are
loaded into the operator DB at rollout, out-of-band.

### 4.2 Injection atom — `modules/content/atoms/content_inject_affiliate_links.py`

- **Placement:** graph_def writer block, after `resolve_internal_link_placeholders`,
  before `quality_evaluation`/the QA rails — so injected links are vetted by the
  critic and `url_validation`.
- **Logic:** markdown-aware. For each active affiliate row whose `keyword`
  appears as plain prose (not inside a fenced code block, inline code, heading,
  or an existing `[...](...)` link), rewrite the **first** occurrence to
  `[display_text](<affiliate_redirect_base_url>/go/<code>)`. Global cap
  `affiliate_max_links_per_post` (default 3). Deterministic; no LLM.
- **State:** sets `has_affiliate_links` on `PipelineState`, carried through
  `content.compile_meta` into the post record/JSON. Emits an `atom_runs` row.
- **Idempotent / fail-open:** re-runs skip already-present `/go/` links; DB
  unreachable → log and pass content through unchanged (same posture as
  `internal_link_coherence`).

### 4.3 Link-management service + CLI

- **Service:** `modules/content/affiliate_links.py` — CRUD, the matcher used by
  the atom, and the click-rollup helper. Single home for the SQL.
- **CLI (CLI-first):** `poindexter affiliate {add,list,enable,disable,rm}`, a
  thin adapter delegating to the service (no inline SQL in the adapter, per the
  transport-adapter-contract ADR). Keeps real URLs out of source and gives
  phone/terminal row management.

### 4.4 Static export — `affiliate-links.json`

Extend `rebuild_static_export` to also write `affiliate-links.json` to R2: a
minimal `code → { url }` map of **active** rows only. Regenerated on publish and
whenever the table changes. This is the map the Worker resolves against; it
carries no PII beyond the referral URLs themselves.

### 4.5 Redirect Worker — `infrastructure/cloudflare/affiliate-redirect/`

Cloned from the page-views beacon Worker:

- `GET /go/<code>` → resolve `code→url` from `affiliate-links.json` (fetched from
  R2, cached via the Cache API with a short TTL) → `env.ANALYTICS_ENGINE.writeDataPoint`
  (blobs: code, post slug from `Referer`, referrer, country, UA) → `302` to the
  merchant URL.
- Unknown/inactive code → `302` to the site homepage (never a hard 404 for a
  reader mid-click).
- Rate-limited per IP via the Workers rate-limit binding, same as the beacon.
- `/go/*` is `robots`-disallowed and non-indexed so redirects never enter the
  crawl/sitemap.

### 4.6 Click sync job — `services/jobs/sync_affiliate_clicks.py`

Straight off the `SyncCloudflareAnalyticsJob` template: pull the affiliate-click
AE dataset via the CF SQL HTTP API every 5 min, insert into
`affiliate_link_clicks`, then roll up counts into `affiliate_links.clicks` and
`post_performance.affiliate_clicks`. High-water-mark in `app_settings`; fail-loud

- `emit_finding` on a half-configured/degraded state (the #555 lesson).

### 4.7 Disclosure (frontend)

`has_affiliate_links` flag on the post JSON drives a conditional banner
component near the top of the post body. Banner copy comes from a
DB-configurable `affiliate_disclosure_text` setting (with a sensible default),
surfaced into the static export so the frontend can render without a live
lookup.

### 4.8 Config keys (`app_settings`, all DB-tunable)

- `affiliate_injection_enabled` — default **false** (dark-launch the atom)
- `affiliate_max_links_per_post` — default `3`
- `affiliate_disclosure_text` — banner copy
- `affiliate_redirect_base_url` — e.g. the `/go` Worker origin
- `plugin.job.sync_affiliate_clicks.{enabled,interval_seconds}` — `true` / `300`
- Reuses `cloudflare_account_id` + `cloudflare_analytics_api_token` (already
  present for the page-views ingest)

### 4.9 Observability

Grafana panels on Cost & Analytics: clicks by link, by post, over time
(sourced from `affiliate_link_clicks`). Degraded-sync findings route the same
way page-views ingest findings do.

### 4.10 Testing & docs

- Atom: match, per-post cap, skip-in-code-block, skip-existing-link,
  idempotency, fail-open on DB error.
- Service: CRUD round-trip; matcher unit cases.
- Sync job: AE-response parse, dedup, watermark advance, degraded-config finding.
- Worker: redirect target + AE write + unknown-code fallback + rate-limit.
- Frontend: banner renders iff `has_affiliate_links`.
- Docs: an operations note on managing affiliate rows + the redirect Worker;
  contract tests per the docs-and-tests default.

## 5. Non-goals (explicit)

- Backfilling the ~110 existing published posts (new posts only).
- Conversion / revenue attribution — the affiliate programs' own dashboards own
  that; we track clicks, not sales.
- Niche/tag relevance gating and LLM relevance judgment (keyword-only by choice).

## 6. Rollout plan

1. Ship schema + service + CLI + atom (behind `affiliate_injection_enabled=false`)
   - static export + Worker + sync job + disclosure + panels.
2. Deploy the Worker; confirm `/go/<code>` resolves + logs against a test row.
3. Load the recovered Mercury row + the new Google Workspace row via
   `poindexter affiliate add` (real URLs, DB-only).
4. Flip `affiliate_injection_enabled=true`; watch the next generated post get a
   vetted link + disclosure banner, and the click panel populate.

## 7. Implementation notes / to-confirm

- **Worker routing:** confirm whether `gladlabs.io`'s zone is on Cloudflare (so a
  `gladlabs.io/go/*` route can front the Worker) or whether to use a
  `go.gladlabs.io` subdomain / `workers.dev` origin. `affiliate_redirect_base_url`
  absorbs whichever it is.
- **Referer-based post attribution** in the Worker is best-effort (some clients
  strip `Referer`); rows with no slug still count as clicks, just unattributed.
- **`post_performance` rollup** is per-post; confirm the join key
  (`post_slug` → `posts.slug`) at implementation.
