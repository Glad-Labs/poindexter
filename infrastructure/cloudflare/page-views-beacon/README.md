# page-views-beacon (Cloudflare Worker)

Lightweight Cloudflare Worker that receives page-view beacons from the
public site and writes one data point per view to a Cloudflare Analytics
Engine dataset (`analytics_events`). The backend sync job
(`services/jobs/sync_cloudflare_analytics.py`) pulls aggregated rows out
via the CF AE SQL HTTP API every 5 minutes and inserts them into the
local `page_views` table.

Ingested rows are then swept by `FlagBotPageViewsJob` (a windowed
`(user_agent, path)` flood-cap) which sets an `is_bot` flag on stealth
scrapers that slip the ingest UA filter. Reader-facing surfaces — the
console `/api/analytics/views` KPI, `posts.view_count`, and the
`lab_outcomes_v1.views_*_post_publish` columns — read the `page_views_human`
view (`is_bot = false`), while the Grafana liveness/anomaly panels and the
`COUNT(*)` liveness examples below intentionally stay on raw `page_views`.
See [`docs/architecture/page-views-bot-flag.md`](../../../docs/architecture/page-views-bot-flag.md).

## Why Cloudflare Analytics Engine

The previous beacon broke because the Vercel-hosted Next.js proxy route
forwarded to `${API_BASE}/api/track/view`, but Vercel's serverless
functions cannot reach `poindexter-worker:8002` on the operator's local
Docker network. CF AE was chosen over the alternatives because:

- **Free tier of 25 billion data points/month** (more than enough for any
  small site).
- **SQL HTTP API** for programmatic reads — no SDK lock-in.
- **No cookies / no GDPR banner** — the data point is anonymous.
- **Matches existing CF dependencies** (R2, DNS).

## Operator setup

The Worker code in this directory contains **no operator-specific
identifiers** (per `feedback_no_operator_info_to_public_repo`). You
fill those in at deploy time via the steps below.

1. **Create the Analytics Engine dataset.**
   In the Cloudflare dashboard → Workers & Pages → Analytics Engine,
   create a new dataset named `analytics_events`. (The name is referenced
   in `wrangler.toml`.)

2. **Mint an API token for SQL reads.**
   Cloudflare dashboard → My Profile → API Tokens → Create Token →
   custom token with scope `Account → Account Analytics → Read`.
   Save the token, then on the operator host:

   ```bash
   poindexter settings set cloudflare_analytics_api_token <token> --secret
   ```

3. **Mint an API token for `wrangler deploy`** (separate from the read
   token — least privilege). Scope: `Account → Workers Scripts → Edit`.
   Save it locally as `CLOUDFLARE_API_TOKEN`:

   ```bash
   export CLOUDFLARE_API_TOKEN=<token>
   ```

4. **Deploy the Worker.**

   ```bash
   cd infrastructure/cloudflare/page-views-beacon
   npm install
   npm run deploy
   ```

   `wrangler deploy` will print the workers.dev URL it published to
   (e.g. `https://page-views-beacon.<your-subdomain>.workers.dev`).

5. **Set the origin allowlist (production requirement).**
   Without it, any page on the web can POST beacons from visitors'
   browsers and inflate your view counts — only the 60 req/min/IP rate
   limiter would stand in the way. Set it as a Worker **secret** (a
   secret survives future deploys; a plain-text var set in the dashboard
   would be wiped by the next `wrangler deploy`, and a `[vars]` entry in
   `wrangler.toml` would clobber the secret — which is why the file
   deliberately declares neither):

   ```bash
   echo "https://example.com,https://www.example.com" \
     | npx wrangler secret put ALLOWED_ORIGINS
   ```

   Use your public-site origins (scheme + host, no trailing slash, no
   path). The check only applies to requests that carry an `Origin`
   header — i.e. browsers — so curl smoke tests and uptime monitors
   keep working.

6. **Map your own subdomain (optional but recommended).**
   In the Cloudflare dashboard → your zone → Workers Routes → add a
   route mapping `<your-beacon-hostname>/*` → `page-views-beacon`.
   Avoids leaking the workers.dev origin in browser DevTools.

7. **Wire the public site at the beacon URL.**
   In Vercel project settings → Environment Variables, set
   `NEXT_PUBLIC_BEACON_URL` to the Worker URL from step 4 or 6. Redeploy
   the public site so the new env baked in.

8. **Tell the backend where to read from.**
   On the operator host:

   ```bash
   poindexter settings set cloudflare_beacon_url <https://your-beacon-url>
   ```

   `cloudflare_account_id` is already seeded in `app_settings` (per the
   2026-05-27 operator-leak audit) — confirm it's set:

   ```bash
   poindexter get cloudflare_account_id
   ```

   If it's blank, set it once from your CF dashboard URL
   (`https://dash.cloudflare.com/<account_id>`).

## Verification

Origin enforcement (run after step 5 — all three must hold):

```bash
BEACON=https://<your-beacon-url>

# Browser POST from a foreign origin is refused:
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Origin: https://evil.example' "$BEACON"          # → 403

# Browser POST from your public site passes:
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Origin: https://www.example.com' "$BEACON"       # → 204

# No Origin header (curl, uptime monitors) still passes:
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BEACON"   # → 204
```

After the public site redeploys, hit any post page and watch:

- Cloudflare dashboard → Analytics Engine → `analytics_events` shows
  a non-zero query count within ~30 seconds.
- After the next sync cycle (5 minutes), the local `page_views` table
  picks up new rows:

  ```sql
  SELECT COUNT(*) FROM page_views WHERE created_at > NOW() - INTERVAL '10 minutes';
  ```

- The Grafana **Pipeline → Page views (last 24h)** stat panel flips
  from red (0) to green within minutes of the first real view.

## SQL API examples (for ad-hoc analysis)

```bash
curl -X POST \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/analytics_engine/sql" \
  -H "Authorization: Bearer ${CF_ANALYTICS_TOKEN}" \
  --data "SELECT blob1 AS slug, count() AS views
          FROM analytics_events
          WHERE timestamp > NOW() - INTERVAL '24' HOUR
          GROUP BY slug ORDER BY views DESC LIMIT 20
          FORMAT JSON"
```

## Local development

```bash
npm install
npm run dev    # wrangler dev — serves on http://localhost:8787
```

`wrangler dev` runs against the real CF runtime locally; data points
written in dev still land in the dataset (use a separate `_dev` dataset
if you want isolation).
