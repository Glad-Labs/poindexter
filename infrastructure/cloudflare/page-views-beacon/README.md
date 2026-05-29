# page-views-beacon (Cloudflare Worker)

Lightweight Cloudflare Worker that receives page-view beacons from the
public site and writes one data point per view to a Cloudflare Analytics
Engine dataset (`page_views_ae`). The backend sync job
(`services/jobs/sync_cloudflare_analytics.py`) pulls aggregated rows out
via the CF AE SQL HTTP API every 5 minutes and inserts them into the
local `page_views` table — feeding the existing Grafana panels,
`posts.view_count`, and the `lab_outcomes_v1.views_*_post_publish`
columns.

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
   create a new dataset named `page_views_ae`. (The name is referenced
   in `wrangler.toml`.)

2. **Mint an API token for SQL reads.**
   Cloudflare dashboard → My Profile → API Tokens → Create Token →
   custom token with scope `Account → Account Analytics → Read`.
   Save the token, then on the operator host:

   ```bash
   poindexter set cloudflare_analytics_api_token <token>
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

5. **Map your own subdomain (optional but recommended).**
   In the Cloudflare dashboard → your zone → Workers Routes → add a
   route mapping `<your-beacon-hostname>/*` → `page-views-beacon`.
   Avoids leaking the workers.dev origin in browser DevTools.

6. **Wire the public site at the beacon URL.**
   In Vercel project settings → Environment Variables, set
   `NEXT_PUBLIC_BEACON_URL` to the Worker URL from step 4 or 5. Redeploy
   the public site so the new env baked in.

7. **Tell the backend where to read from.**
   On the operator host:

   ```bash
   poindexter set cloudflare_beacon_url <https://your-beacon-url>
   ```

   `cloudflare_account_id` is already seeded in `app_settings` (per the
   2026-05-27 operator-leak audit) — confirm it's set:

   ```bash
   poindexter get cloudflare_account_id
   ```

   If it's blank, set it once from your CF dashboard URL
   (`https://dash.cloudflare.com/<account_id>`).

## Verification

After the public site redeploys, hit any post page and watch:

- Cloudflare dashboard → Analytics Engine → `page_views_ae` shows
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
          FROM page_views_ae
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
