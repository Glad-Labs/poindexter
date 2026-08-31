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

- **Path on the site zone** — route `example.com/go/*` to this Worker. Links in
  posts are root-relative (`/go/mercury`) and resolve on the same origin. This
  is the default the pipeline assumes (`affiliate_redirect_base_url = /go`).
- **`go.` subdomain** — route `go.example.com/*` to this Worker and set
  `affiliate_redirect_base_url = https://go.example.com` in `app_settings`.
  `codeFromPath` handles the bare `/<code>` form identically.

## Config (Worker **secrets**, not `[vars]`)

| Secret      | Purpose                                                                                                         |
| ----------- | --------------------------------------------------------------------------------------------------------------- |
| `LINKS_URL` | Public URL of `static/affiliate-links.json` on R2 (your `storage_public_url` + `/static/affiliate-links.json`). |
| `HOME_URL`  | Fallback redirect for a blank or unknown code, e.g. `https://www.example.com`.                                  |

Both are **secrets** (`wrangler secret put`), and `wrangler.toml` deliberately
declares no `[vars]` block. This is not a privacy choice — `HOME_URL` is a
public hostname printed in every fallback redirect — it is the only
**deploy-proof** channel:

> `wrangler deploy` is _declarative_ about plain-text vars. A var set in the
> Cloudflare dashboard is wiped by the next deploy, and a var committed in
> `wrangler.toml` overwrites a secret of the same name. Secrets survive.

This worker shipped `[vars] LINKS_URL = "https://<r2-public-host>/..."` — a
placeholder — while the deployed Worker carried the real R2 host as a var that
was never committed anywhere. Any `npm run deploy` from a clean checkout would
have replaced a working binding with an unparseable URL and taken every
`/go/<code>` link down. (The page-views beacon ran with origin enforcement
silently off until 2026-08-31 for the same reason.)

`HOME_URL` is a secret for a second reason too: `infrastructure/cloudflare/` is
**not** stripped by `scripts/sync-to-github.sh`, so a committed operator
hostname reaches the public mirror and every fork would inherit it as the
destination for their own unknown codes.

`ANALYTICS_ENGINE` binds the `affiliate_clicks` AE dataset — create it in the CF
dashboard (Workers → Analytics Engine) before the first deploy.

### Failure modes

Each of these used to return the same `302` to `HOME_URL` — the identical
response a healthy Worker gives for a stale link — so a Worker whose
`LINKS_URL` had been clobbered was indistinguishable from a working one, and
affiliate attribution could die silently for weeks. They are now distinct:

| Condition                                                           | Response                                |
| ------------------------------------------------------------------- | --------------------------------------- |
| `LINKS_URL` or `HOME_URL` unset — Worker not wired                  | `503 affiliate-redirect not configured` |
| Link map unloadable — bad `LINKS_URL`, R2 outage, non-2xx, bad JSON | `502 affiliate link map unavailable`    |
| Blank or genuinely unknown code                                     | `302` → `HOME_URL`                      |
| Known code                                                          | `302` → merchant URL, one AE data point |

Only the third row is a normal condition, so it is the only one that still
redirects — the "never a broken link" promise is kept for readers following a
stale link, without hiding a broken Worker behind it.

## Deploy

```bash
cd infrastructure/cloudflare/affiliate-redirect
npm install
npm test            # vitest — resolver + handler failure-mode tests
npx wrangler deploy
```

Then set both secrets. `wrangler secret put` needs the Worker to exist, hence
deploy first on a fresh install:

```bash
echo "https://<your-r2-public-host>/static/affiliate-links.json" \
  | npx wrangler secret put LINKS_URL
echo "https://www.example.com" | npx wrangler secret put HOME_URL
```

> **Upgrading a Worker that already ran with `[vars]`?** The order above is
> mandatory, and it is the opposite of what feels safe. Cloudflare refuses to
> create a secret whose name collides with an existing plain-text var —
> `Binding name 'LINKS_URL' already in use [code: 10053]` — so you cannot
> pre-stage the secrets. Deploy first (that removes the var bindings), then set
> both secrets. Between the two steps the Worker has no config and answers
> `503` on every `/go` click, so run them back to back; the window is seconds
> and `/go` traffic is a few clicks a day, but it is real.

Finally wire the route (dashboard or CLI):

```bash
npx wrangler routes add "example.com/go/*"       # path form
# or:  npx wrangler routes add "go.example.com/*"    # subdomain form
```

## Verification

A blank or unknown code redirects home — but so does a Worker pointed at a
bucket that no longer exists. **Probing an unknown slug therefore proves
nothing.** Always verify with a code that really is in `affiliate_links`
(`poindexter affiliate list`, or read the R2 map directly):

```bash
SITE=https://example.com
CODE=<a-real-code-from-affiliate_links>

# Known code → 302 to the merchant URL (NOT to your homepage):
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "$SITE/go/$CODE"

# Unknown code → 302 to HOME_URL:
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "$SITE/go/no-such-code"
```

If the first curl lands on your homepage, the Worker is not resolving the map —
check the live bindings and confirm `LINKS_URL` shows as a secret, not an
environment variable:

```bash
npx wrangler versions view $(npx wrangler deployments list | grep -oP '(?<=Version\(s\):  \(100%\) )[0-9a-f-]+' | head -1)
npx wrangler secret list
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

`npm test` runs `vitest run` over the pure helpers (`codeFromPath`,
`resolveTarget`) and the `fetch` handler's failure modes — including a test
that pins the exact deploy-clobber scenario: a `LINKS_URL` left as the
`<r2-public-host>` placeholder must return `502`, never a home redirect that
looks like an ordinary unknown code. The remaining edge-runtime glue (real edge
cache, AE write) is verified by a live click after deploy.
