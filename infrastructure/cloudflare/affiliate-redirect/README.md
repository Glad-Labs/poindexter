# affiliate-redirect Worker

A Cloudflare Worker that turns `/go/<code>` links into tracked 302 redirects to
merchant/referral URLs. It is the click-tracking half of the affiliate-injection
feature: the content pipeline rewrites the first prose mention of a seeded
keyword into `[text](/go/<code>)`, and this Worker resolves `<code>` → real URL
at click time while logging the click to Analytics Engine.

Real referral URLs live only in the database (`affiliate_links` table, managed
via `poindexter affiliate`). They reach the edge as a static map published by
the site export — never committed to source.

## How it works

1. Reader clicks `/go/mercury` on a post.
2. The Worker reads `static/affiliate-links.json` from R2 (edge-cached 5 min),
   a `{ "<code>": { "url": "<merchant-url>" } }` map produced by
   `services/static_export_service.py::_export_affiliate_links`.
3. Known code → **302** to the merchant URL. Blank/unknown code → **302** to
   `HOME_URL` (never a broken link).
4. Every resolved click writes one Analytics Engine data point to the
   `affiliate_clicks` dataset: `blob1=code`, `blob2=referer`, `blob3=country`,
   `blob4=user-agent`, `index1=code`.
5. `SyncAffiliateClicksJob` (worker, every 5 min) pulls those rows into the
   `affiliate_link_clicks` table via the AE SQL HTTP API and rolls per-code
   totals into `affiliate_links.clicks`.

## Routing — path vs subdomain

Two supported deploy shapes; pick one per operator:

- **Path on the site zone** — route `gladlabs.io/go/*` to this Worker. Links in
  posts are root-relative (`/go/mercury`) and resolve on the same origin. This
  is the default the pipeline assumes (`affiliate_redirect_base_url = /go`).
- **`go.` subdomain** — route `go.gladlabs.io/*` to this Worker and set
  `affiliate_redirect_base_url = https://go.gladlabs.io` in `app_settings`.
  `codeFromPath` handles the bare `/<code>` form identically.

## Config (`wrangler.toml` `[vars]`)

| Var         | Purpose                                                                         |
| ----------- | ------------------------------------------------------------------------------- |
| `LINKS_URL` | Public URL of `static/affiliate-links.json` on R2. Set the real R2 host.        |
| `HOME_URL`  | Fallback redirect for a blank/unknown code (default `https://www.gladlabs.io`). |

`ANALYTICS_ENGINE` binds the `affiliate_clicks` AE dataset — create it in the CF
dashboard (Workers → Analytics Engine) before the first deploy.

## Deploy

```bash
cd infrastructure/cloudflare/affiliate-redirect
npm install
npm test            # vitest — pure resolver tests
npx wrangler deploy
# then wire the route (dashboard or):
npx wrangler routes add "gladlabs.io/go/*"      # path form
# or:  npx wrangler routes add "go.gladlabs.io/*"   # subdomain form
```

## Read clicks back (AE SQL)

```bash
curl "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/analytics_engine/sql" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -d "SELECT blob1 AS code, count() AS clicks
      FROM affiliate_clicks
      WHERE timestamp > now() - INTERVAL '7' DAY
      GROUP BY code ORDER BY clicks DESC"
```

The worker's sync job does this automatically; the curl is for manual spot-checks.

## Tests

`npm test` runs `vitest run` against the pure helpers (`codeFromPath`,
`resolveTarget`). The edge-runtime glue (cache, AE write, 302) is verified by a
live click after deploy.
