# Public `/referrals` page — Design

- **Date:** 2026-07-12
- **Status:** Design approved; ready for implementation plan
- **Branch:** TBD — new worktree at implementation time

## 1. Context & motivation

The affiliate-injection feature (`2026-07-11-affiliate-injection-design.md`)
shipped, is live, and is enabled: four active rows in `affiliate_links`
(Mercury, Google Workspace, and two Amazon Associates bounty programs — Prime
Gaming, Audible), each surfacing only when a generated post happens to mention
its keyword in prose. That's the whole discovery surface today — a reader can
only ever encounter a link opportunistically, and there is no single place
that shows everything Glad Labs recommends.

Matt is gathering more Amazon product links (day-to-day items he actually
uses) to add to the table, and wants a dedicated public page listing all of
them with real names and descriptions — both as reader-facing value (a
"resources"/"uses" page is a well-worn, well-liked pattern on tech/content
blogs) and as additional visible disclosure backing beyond the per-post
banner.

## 2. Decisions (locked)

| Dimension       | Decision                                                | Rationale                                                                                                                                 |
| --------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Grouping        | Two sections: **service** vs **product**                | Matches how the list is actually built (business tools vs reader-facing recommendations); avoids a full tag system for a handful of items |
| Visibility flag | Reuse `is_active`                                       | One switch — page is just "what's live." A second `show_on_page` flag would be unused complexity today                                    |
| Images          | None in v1                                              | Ships faster; an `image_url` column can be added later without disrupting anything already built                                          |
| Data source     | DB-driven export (new JSON), not a hand-maintained file | Zero manual sync — consistent with how every other piece of site content already works (R2-JSON headless CMS)                             |
| Click tracking  | Page links through `/go/<code>`, same as in-post links  | One source of click truth; no separate page-click accounting needed, no raw merchant URL ever reaches the frontend                        |
| Sort order      | Insertion order (`id ASC`)                              | YAGNI — add a `sort_order` column only if it's ever actually needed                                                                       |

## 3. Architecture & data flow

```
OPERATOR (CLI)                                     READER (public site)
───────────────                                    ─────────────────────
poindexter affiliate add/enable/disable/rm          reader visits /referrals
  │                                                      │
  ▼  DB write (affiliate_links row)                      ▼  fetch static/affiliate-referrals.json (R2)
  ▼  republish BOTH exports:                             ▼  render two Card-grid sections
     • affiliate-links.json      (Worker's minimal map)     ("Tools & Services" / "Amazon Picks")
     • affiliate-referrals.json  (name/description/category) ▼  each card CTA → /go/<code>
  ▼  revalidateTag('referrals')                                (same tracked redirect + click analytics
                                                                 as an in-post injected link)
```

The Worker's minimal `code → url` map is untouched — this feature adds a
second, separate export purely for page-display fields, so the redirect hot
path's payload never grows because a description got longer.

## 4. Components

### 4.1 Schema

```sql
ALTER TABLE affiliate_links
  ADD COLUMN description text NOT NULL DEFAULT '',
  ADD COLUMN category varchar(20) NOT NULL DEFAULT 'product'
    CONSTRAINT affiliate_links_category_check CHECK (category IN ('service', 'product'));
```

- `category` is a closed two-value field — `service` | `product` — not a
  general tag system (explicitly rejected in favor of the simpler two-section
  model; §2). The CHECK constraint enforces this at the DB layer, not just in
  the CLI, so a typo'd value can never slip in silently.
- The `DEFAULT` values exist only so the migration doesn't break the 4
  existing rows; they are NOT a signal that new inserts can skip specifying
  real values. `poindexter affiliate add` requires `--category` and
  `--description` explicitly going forward (no silent default at the CLI
  layer, consistent with the no-silent-defaults principle).
- **Immediate follow-up once the migration ships:** the 4 existing rows all
  land on `category='product'` / `description=''` from the DB default and
  need a real backfill — Mercury and Google Workspace specifically need
  `category=service`, and all four need real descriptions written (via
  `affiliate add` upsert, same as any other edit). The backfill _mechanism_
  (running the CLI command) is part of implementation; the actual
  description _copy_ is Matt's call — draft reasonable copy per item and get
  a quick confirm/edit from him rather than inventing final marketing text.

### 4.2 CLI changes — `poindexter/cli/affiliate.py`

- `affiliate add` gains required `--category {service,product}` and
  `--description TEXT` flags.
- `affiliate add` / `enable` / `disable` / `rm` all trigger a republish of
  both exports (§4.3) immediately after their DB write. This closes a real
  gap hit twice during the original rollout — nothing currently republishes
  automatically when a row changes outside of a post-publish cycle, so a
  manual step was required each time.

### 4.3 Referrals export — `static/affiliate-referrals.json`

- New function alongside `_export_affiliate_links` in
  `services/static_export_service.py` (e.g. `_export_affiliate_referrals`),
  querying active rows: `code, display_text AS name, description, category`.
- Deliberately excludes the real merchant `url` — the page always links
  through `/go/<code>`, so raw URLs never need to reach the frontend for this
  purpose, and every click (page or in-post) lands in the same
  `affiliate_link_clicks` analytics.
- A separate R2 object from `affiliate-links.json`, not a shared/extended one
  — keeps the Worker's redirect hot path lean regardless of how much
  page-display content grows.

### 4.4 Frontend — `/referrals`

- New route `web/public-site/app/referrals/page.js`, matching the existing
  `app/about/page.js` house pattern: `Eyebrow` + `Display` hero,
  `gl-atmosphere` wrapper, `@glad-labs/brand` components throughout.
- Fetches `affiliate-referrals.json` server-side. `export const revalidate =
3600` self-healing backstop, same pattern as the index/posts/archive
  /sitemap/feed routes.
- On-demand freshness: the CLI's republish step (§4.2) also fires
  `revalidateTag('referrals')` via the existing `/api/revalidate` route
  (mirrors how a post publish already fires `revalidateTag('posts')`), so a
  CLI change reaches the live page within seconds rather than waiting up to
  an hour on the ISR backstop alone.
- Two `Card`-grid sections grouped by `category`: "Tools & Services" (service)
  and "Amazon Picks" (product) — section copy is a starting proposal, easily
  tweaked at implementation or review.
- Each card: `name` (bold title), `description` (body copy), a CTA button
  linking to `<affiliate_redirect_base_url>/go/<code>` with
  `rel="sponsored noopener" target="_blank"`.
- Renders the existing `AffiliateDisclosure` component once at the top of the
  page, unconditionally — this whole page is affiliate content, so it isn't
  gated on a per-post `has_affiliate_links` flag the way the in-post banner
  is.
- Nav: add a "Referrals" link into `TopNav.js` (desktop + mobile menus) and
  `Footer.js`'s "Explore" column, alongside Home / Articles / About.

## 5. Non-goals (explicit)

- No images/thumbnails in v1 (Matt's own choice — ship text-only first).
- No general tag/category system beyond the closed `service`/`product` split
  (explicitly rejected in favor of the simpler two-section model).
- No manual sort-order column — insertion order is enough until it isn't.
- No changes to the existing in-post injection mechanism — this is purely
  additive on top of it (new columns, new export, new page, new CLI flags
  alongside the existing ones).
- No new analytics surface — page clicks flow through the exact same
  `/go/<code>` → `affiliate_link_clicks` → Grafana pipeline already built.

## 6. Rollout plan

1. Migration (schema) + CLI flag changes; immediately backfill
   category/description for the 4 existing rows.
2. New export function + CLI auto-republish wiring; confirm both JSON objects
   land on R2 correctly.
3. Frontend page + nav/footer links.
4. Verify: seed one new product via CLI, confirm it appears on the live page
   within the on-demand revalidate window with no manual republish step.

## 7. Implementation notes / to-confirm

- Confirm the exact R2-JSON fetch convention already used elsewhere on the
  frontend (e.g. how `lib/posts.ts` fetches its data) so this page's fetch
  matches house style exactly, rather than inventing a new pattern.
- Confirm `/api/revalidate`'s current tag handling accepts an arbitrary new
  tag (`referrals`) or needs a small addition.
- Section-header and hero copy are placeholders ("Tools & Services" / "Amazon
  Picks") — fine to bikeshed at implementation or final review, not a design
  blocker.
