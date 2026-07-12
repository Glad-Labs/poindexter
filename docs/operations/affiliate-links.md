# Affiliate links — curated injection + `/go` click tracking

Glad Labs injects a small number of **curated** affiliate links into generated
posts: the pipeline rewrites the first prose mention of a seeded keyword into a
tracked `/go/<code>` link, a Cloudflare Worker 302-redirects that to the real
merchant URL while logging the click, and posts that carry a link show an FTC
disclosure banner. The feature ships **disabled**; nothing injects until you
seed links and flip one setting.

## The one hard rule: real referral URLs are DB-only

Never commit a real referral URL or code to source (`settings_defaults.py`,
`baseline.seeds.sql`, any repo file). The tables ship **empty**; you add rows at
runtime with the `poindexter affiliate` CLI, which writes straight to the
database. (Origin: a March-2026 incident where fabricated referral codes leaked
into the public mirror; real URLs have been DB-only ever since.)

## Quick start

```bash
# 1. Seed a link (real URL stays in the DB, never in git):
poindexter affiliate add \
  --code mercury \
  --keyword Mercury \
  --url "https://mercury.com/r/<your-referral>" \
  --program "Mercury Referral" \
  --category service \
  --description "Business banking we use daily."

# Pass --keyword multiple times for aliases (e.g. a product with several names):
#   --keyword "RTX 5090" --keyword "ASUS ROG Astral" --keyword "5090 GPU"

# 2. Review what's live:
poindexter affiliate list

# 3. Turn injection on (off by default):
poindexter settings set affiliate_injection_enabled true
```

The next generated post that mentions "Mercury" in prose gets one
`[Mercury](/go/mercury)` link (first mention only), a disclosure banner, and
starts accruing clicks.

### CLI reference

| Command                                                                                                                  | Effect                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `poindexter affiliate add --code --keyword ... --url --category --description [--display-text] [--program] [--platform]` | Add or update a link (idempotent on `code`). `--keyword` is repeatable — pass it multiple times for aliases. |
| `poindexter affiliate list [--all]`                                                                                      | List links (active only by default; `--all` includes inactive).                                              |
| `poindexter affiliate enable <code> \| --all`                                                                            | Re-activate one link, or every inactive link at once.                                                        |
| `poindexter affiliate disable <code>`                                                                                    | Deactivate without deleting (stops new injections + drops it from the published map).                        |
| `poindexter affiliate rm <code>`                                                                                         | Delete a link.                                                                                               |
| `poindexter affiliate import-csv <path> [--force]`                                                                       | Bulk-import from a spreadsheet export (see "Bulk import" below).                                             |

`--keyword` is a phrase matched in body prose (case-sensitive on the first
letter as written) — pass it multiple times on one `add` call to give a
link several aliases; different links can share a keyword (see "How
injection works" below for how that's resolved). `--code` is the stable
slug used in `/go/<code>` and as the click-tracking key — keep it short and
URL-safe. `--platform` is a free-text label (e.g. `Amazon`, `direct`) for
your own tracking, not used by the matcher.

## How injection works

The `content.inject_affiliate_links` atom runs in the `canonical_blog` pipeline
between `content.llm_reconcile_citations` and `quality_evaluation` — i.e. inside
the writer block, **before** the QA rails, so every injected link is vetted by
the same review the prose gets. For each active link it:

- rewrites only the **first** prose mention of the keyword into
  `[display_text](/go/<code>)` (`display_text` defaults to the keyword);
- skips fenced/inline code, headings, and text already inside a link;
- caps the whole post at `affiliate_max_links_per_post` links (default 3).

When a link's keyword is shared with another active link (e.g. "Corsair" on
several different Corsair products), the matcher resolves which one wins a
given mention in two steps, both free and deterministic: first, does one of
the tied candidates have another of _its own_ keywords also present
elsewhere in the same post (e.g. the post also says "HX1500i" — that
candidate wins)? If that doesn't disambiguate, it rotates to whichever
candidate was least-recently linked into a published post, so exposure
spreads across your catalog instead of one product always winning a generic
mention.

It is deterministic (no LLM) and **fails open**: any config/DB read error leaves
the content untouched rather than breaking the pipeline. When
`affiliate_injection_enabled` is `false`, or there are no active links, it is a
no-op.

## Click tracking: the `/go` Worker

The Worker lives at `infrastructure/cloudflare/affiliate-redirect/` (see its
README for deploy details). Flow:

1. A reader clicks `/go/mercury`.
2. The Worker reads `static/affiliate-links.json` from R2 — a `code → url` map
   published by the static export (`_export_affiliate_links`) on every publish
   and full rebuild, edge-cached 5 minutes.
3. Known code → **302** to the merchant URL. Blank/unknown code → **302** to
   `HOME_URL` (never a broken link).
4. The click writes one data point to the `affiliate_clicks` Analytics Engine
   dataset (`code`, referrer, country, user-agent).
5. `SyncAffiliateClicksJob` (`services/jobs/sync_affiliate_clicks.py`, every
   5 min) pulls those points via the CF SQL HTTP API into
   `affiliate_link_clicks`, attributes each to its source post (from the
   referrer's `/posts/<slug>` path), and rolls per-code totals into
   `affiliate_links.clicks`. It reuses the page-views ingest credentials
   (`cloudflare_account_id` + secret `cloudflare_analytics_api_token`) and keeps
   its own high-water mark in `affiliate_clicks_last_sync`. Because the feature
   is opt-in, the job skips quietly when Cloudflare isn't configured — it does
   not page.

Clicks surface on the **Cost & Analytics** Grafana board under the "Affiliate
Links — /go clicks" section (per-day timeseries, by-link and by-post tables).

## Disclosure banner

The static export sets `has_affiliate_links` on a post's JSON when the rendered
body contains the redirect path (default `/go/`). When true, the post page
renders `AffiliateDisclosure` at the top of the article body (before any link),
showing `affiliate_disclosure_text` or a built-in default. The flag is derived
from the content itself — there is no separate stored boolean to drift.

## Settings (`app_settings`)

| Key                                                 | Default       | Purpose                                                                 |
| --------------------------------------------------- | ------------- | ----------------------------------------------------------------------- |
| `affiliate_injection_enabled`                       | `false`       | Master switch for the injection atom.                                   |
| `affiliate_max_links_per_post`                      | `3`           | Per-post cap on injected links.                                         |
| `affiliate_redirect_base_url`                       | `/go`         | Link base. `/go` for a zone route; a full origin for a `go.` subdomain. |
| `affiliate_disclosure_text`                         | (FTC default) | Banner copy shown on posts that carry a link.                           |
| `plugin.job.sync_affiliate_clicks.enabled`          | `true`        | Whether the click-sync job runs.                                        |
| `plugin.job.sync_affiliate_clicks.interval_seconds` | `300`         | Click-sync cadence.                                                     |
| `affiliate_clicks_last_sync`                        | (unset)       | Job-managed high-water mark — do not hand-edit.                         |

Reused from the page-views ingest: `cloudflare_account_id` (non-secret) and
`cloudflare_analytics_api_token` (secret, scope `Account → Account Analytics →
Read`).

## Deploy note: zone route vs `go.` subdomain

Pick one when you deploy the Worker:

- **Zone route** — route `www.gladlabs.io/go/*` to the Worker and keep
  `affiliate_redirect_base_url = /go`. Links are same-origin root-relative
  (`/go/mercury`). This is the default.
- **`go.` subdomain** — route `go.gladlabs.io/*` to the Worker and set
  `affiliate_redirect_base_url = https://go.gladlabs.io`. The Worker resolves
  the bare `/<code>` form identically.

## Data model

- `affiliate_links` — `id, code (UNIQUE), url, display_text, program,
is_active, clicks, created_at, updated_at, description, category, platform`.
  The published map and the injector both read `WHERE is_active = true`.
- `affiliate_link_keywords` — `id, link_id (FK -> affiliate_links, ON DELETE
CASCADE), keyword, created_at`, `UNIQUE(link_id, keyword)` — one row per
  keyword/alias a link matches on. Uniqueness is scoped per-link, not
  global: different links CAN share a keyword string.
- `affiliate_link_clicks` — `id, code, post_slug, referrer, country, user_agent,
created_at`. One row per synced click; `post_slug` is derived from the
  referrer.

## Bulk import from a spreadsheet

`poindexter affiliate import-csv <path> [--force]` reads a CSV export with
these columns: `Status, Product Name, Category, Platform, Commission Rate,
Affiliate Link, Description, Promo Code` (`Commission Rate` and `Promo Code`
are read but not stored).

For each row:

- `code` is derived deterministically from `Product Name` (never
  LLM-touched, so re-running the import on an unchanged sheet always maps
  to the same code).
- If that `code` already exists, the row is **skipped** unless `--force` is
  passed — this protects hand-curated rows (e.g. a real description you
  wrote) from being blanked out by a re-import.
- `display_text` and a keyword list are proposed by a local LLM from the
  title + description (`task.affiliate_derive_keywords`). On any LLM/JSON
  error the row still gets created, falling back to the product name itself
  as both the display text and sole keyword — nothing is silently dropped.
- `is_active` is set directly from the CSV's own `Status` column
  (`"Active"`, case-insensitive → active; anything else → inactive).
- `Category` maps to `service`/`product` (`"Service"` → `service`,
  everything else, including `"Hardware"` → `product`). `Platform` is
  copied verbatim into the new `platform` column.

Review the printed summary, then approve what you're happy with:

```bash
poindexter affiliate list --all       # review anything that landed inactive
poindexter affiliate enable --all     # approve everything at once, or...
poindexter affiliate enable <code>    # ...one at a time
```

## Rollout checklist

1. Deploy the Worker (`wrangler deploy` in
   `infrastructure/cloudflare/affiliate-redirect/`); confirm the zone route or
   `go.` subdomain resolves. Set `LINKS_URL` to your R2 public host and create
   the `affiliate_clicks` AE dataset.
2. Trigger a full static rebuild so `affiliate-links.json` publishes to R2.
3. Seed real rows (DB-only): `poindexter affiliate add --code mercury --keyword
Mercury --url <referral> --program "Mercury Referral"`; same for the Google
   Workspace link.
4. Confirm `/go/mercury` 302s to the merchant and a row lands in
   `affiliate_link_clicks` within ~5 min.
5. Flip `poindexter settings set affiliate_injection_enabled true`; watch the
   next generated post get a vetted link + disclosure banner, and the Grafana
   panel populate.
