# Setup runbook — Google Search Console + GA4 Singer taps

End-to-end steps to start populating `external_metrics` from real Google data sources. One-time OAuth setup; both taps share the same OAuth client + refresh token.

**Time required:** ~20 minutes the first time. Subsequent runs are zero-touch.

## Prerequisites

- A Google account that owns (or has admin access to) the GSC properties and GA4 properties you want to ingest
- Python on your local machine (any 3.10+) — only needed for the one-time OAuth helper
- Poindexter operator access (DB + CLI)

The seed rows are already in `external_taps` (commit 5f6a4075's follow-up — `gsc_main` and `ga4_main`, both `enabled=false`). The operator runbook is the path from "rows exist" to "data flowing."

---

## Step 1 — Google Cloud Console: enable APIs + create OAuth client

1. Go to https://console.cloud.google.com/. Pick or create a project (any name; "poindexter-analytics" works).

2. Enable the two APIs the taps use:
   - https://console.cloud.google.com/apis/library/searchconsole.googleapis.com → click **Enable**
   - https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com → click **Enable**

3. Create OAuth credentials:
   - Go to https://console.cloud.google.com/apis/credentials
   - **Configure consent screen** (if you haven't already):
     - User type: **External**
     - App name: `Poindexter Analytics` (anything works — only you will see this)
     - User support email + developer email: your email
     - Scopes: leave default; the taps request the scopes themselves
     - Test users: add your own Google email
     - Save and continue all the way through
   - Then **+ CREATE CREDENTIALS → OAuth client ID**:
     - Application type: **Desktop app**
     - Name: `poindexter-singer-tap`
     - Click **Create**
   - Copy the **Client ID** and **Client secret** (the dialog shows them once; you can also re-open later).

You now have:

- `client_id` (looks like `123456789-abc...apps.googleusercontent.com`)
- `client_secret` (looks like `GOCSPX-...`)

---

## Step 2 — Get a refresh_token

From the repo root, in any Python environment:

```
pip install google-auth-oauthlib
python scripts/google-oauth-setup.py \
    --client-id <your_client_id> \
    --client-secret <your_client_secret>
```

What happens:

1. Browser opens to Google's OAuth consent screen.
2. Sign in with the Google account that owns your GSC + GA4 properties.
3. Click **Allow** for both scopes (`webmasters.readonly` + `analytics.readonly`).
4. Browser shows "The authentication flow has completed."
5. Terminal prints a `refresh_token` plus copy-paste-ready `poindexter` commands.

The `refresh_token` is long-lived — Google does not expire these unless the user revokes the grant or the app goes 6+ months without using it. Store it carefully.

> If you don't see a `refresh_token` in the output, Google didn't issue one (this happens when you've already consented). Revoke the app at https://myaccount.google.com/permissions and run the helper again.

---

## Step 3 — Store credentials in Poindexter

Three settings. The first one (client_id) is not encrypted; the other two are.

```
poindexter settings set google_oauth_client_id "<your_client_id>"
poindexter settings set google_oauth_client_secret "<your_client_secret>" --secret
poindexter settings set google_oauth_refresh_token "<your_refresh_token>" --secret
```

Verify with `poindexter settings list --search google_oauth`. The two `--secret` rows will show `enc:v1:...` ciphertext — that's correct.

---

## Step 4 — Update the tap rows with your specifics

The two rows shipped with empty placeholders. Update each with your real values:

### GSC

```sql
UPDATE external_taps
   SET config = jsonb_set(
        jsonb_set(
          jsonb_set(
            jsonb_set(
              config,
              '{tap_config,client_id}',
              to_jsonb('<your_client_id>'::text)
            ),
            '{tap_config,client_secret}',
            to_jsonb('<your_client_secret>'::text)
          ),
          '{tap_config,refresh_token}',
          to_jsonb('<your_refresh_token>'::text)
        ),
        '{tap_config,site_urls}',
        '["https://www.gladlabs.io"]'::jsonb
      )
 WHERE name = 'gsc_main';
```

`site_urls` must match an EXACT property URL registered in your GSC account (typically `https://example.com` for domain properties or `https://www.example.com/` with the trailing slash for URL-prefix properties — check at https://search.google.com/search-console).

### GA4

You'll need your GA4 **property ID** — find it at https://analytics.google.com → Admin → Property → Property details → "Property ID" (a number like `123456789`).

```sql
UPDATE external_taps
   SET config = jsonb_set(
        jsonb_set(
          jsonb_set(
            jsonb_set(
              config,
              '{tap_config,client_id}',
              to_jsonb('<your_client_id>'::text)
            ),
            '{tap_config,client_secret}',
            to_jsonb('<your_client_secret>'::text)
          ),
          '{tap_config,refresh_token}',
          to_jsonb('<your_refresh_token>'::text)
        ),
        '{tap_config,property_id}',
        to_jsonb('<your_ga4_property_id>'::text)
      )
 WHERE name = 'ga4_main';
```

---

## Step 5 — Install the tap binaries

The taps run as subprocesses; the worker needs them on `PATH`. Two options:

### Option A — install in the worker container (recommended for production)

```
docker exec -it poindexter-worker pip install tap-google-search-console tap-ga4
```

(Or add them to your `pyproject.toml` / `requirements.txt` so they're baked into the image on the next build.)

### Option B — install in a sidecar venv that the row's `command` points at

If you want the taps isolated from the worker's Python env, create a venv at e.g. `~/.poindexter/singer-venv`, install both taps there, and set the row's `config.command` to the absolute venv binary path:

```sql
UPDATE external_taps
   SET config = jsonb_set(config, '{command}', to_jsonb('/home/appuser/.poindexter/singer-venv/bin/tap-google-search-console'::text))
 WHERE name = 'gsc_main';
```

Either works — option A is simpler.

---

## Step 6 — Test on demand

Without flipping `enabled` yet, run each tap manually:

```
poindexter taps run gsc_main
poindexter taps run ga4_main
```

> **Note:** the `taps run` CLI command only invokes enabled rows. For the on-demand test BEFORE enabling, either temporarily flip enabled and back, or call the runner inline:
>
> ```
> python -c "
> import asyncio, asyncpg
> from services.integrations import tap_runner
> async def main():
>     pool = await asyncpg.create_pool('$LOCAL_DATABASE_URL', min_size=1, max_size=2)
>     try:
>         await pool.execute(\"UPDATE external_taps SET enabled=TRUE WHERE name='gsc_main'\")
>         summary = await tap_runner.run_all(pool, only_names=['gsc_main'])
>         print(summary.to_dict())
>     finally:
>         await pool.execute(\"UPDATE external_taps SET enabled=FALSE WHERE name='gsc_main'\")
>         await pool.close()
> asyncio.run(main())
> "
> ```

A successful run reports `records: <N>` and you'll see fresh rows in `external_metrics`:

```sql
SELECT source, metric_name, COUNT(*), MAX(date) FROM external_metrics GROUP BY source, metric_name ORDER BY source, metric_name;
```

---

## Step 7 — Flip enabled and let the scheduler take over

```
poindexter taps enable gsc_main
poindexter taps enable ga4_main
```

The schedule is `every 6 hours` for both. Watch the **Integration Health** Grafana dashboard — the `total_records` column on the External taps table will tick up after each run.

---

## Troubleshooting

| Symptom                                               | Cause                                                    | Fix                                                                   |
| ----------------------------------------------------- | -------------------------------------------------------- | --------------------------------------------------------------------- |
| `tap exited 1` with "invalid_grant"                   | refresh_token revoked or stale                           | re-run `google-oauth-setup.py`, update the row                        |
| `tap exited 1` with "Property not found" (GA4)        | wrong property_id                                        | verify in GA Admin → Property details                                 |
| `tap exited 1` with "User does not have access" (GSC) | OAuth account isn't a verified owner                     | grant the account access in https://search.google.com/search-console  |
| 0 records returned                                    | `start_date` is in the future, or data hasn't propagated | back the start_date up a week; GSC has 2-3 day lag                    |
| `tap-google-search-console: command not found`        | tap not on PATH                                          | `docker exec poindexter-worker pip install tap-google-search-console` |

## Where data lands

`external_metrics` table. The mapping config on each row determines which fields become metric rows vs which become dimensions in the jsonb. For GSC each performance row produces 4 `external_metrics` rows (impressions / clicks / ctr / position); for GA4 each page_metrics row produces 4 (sessions / screenPageViews / engagementRate / userEngagementDuration).

Query examples:

```sql
-- Daily click trend for one slug
SELECT date, metric_value AS clicks
  FROM external_metrics
 WHERE source = 'google_search_console'
   AND metric_name = 'clicks'
   AND slug = 'rtx-5090-70b-models'
 ORDER BY date;

-- Top-clicked queries last 7 days (dimensions are jsonb)
SELECT dimensions->>'query' AS query, SUM(metric_value)::int AS clicks
  FROM external_metrics
 WHERE source = 'google_search_console'
   AND metric_name = 'clicks'
   AND date >= now() - INTERVAL '7 days'
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 20;
```

Wire these into Grafana panels once the data starts flowing.

## Related

- `docs/integrations/tap_singer_subprocess.md` — the dispatcher these rows use
- `docs/integrations/tap_external_metrics_writer.md` — the record_handler that writes to `external_metrics`
- GH-103 / GH-27 — feedback-loop tables; `external_metrics` is one of the eight
