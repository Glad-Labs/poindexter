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

The two rows shipped with empty placeholders. Update each with your real values.

**The two OAuth secrets you just stored in Step 3 are referenced by key, never pasted in.** `external_taps.config` is a plain (unencrypted) jsonb column — readable by any SQL session, pgAdmin browse, or DB dump — so `client_secret`/`refresh_token` go through `config.secret_fields` instead, which `tap.singer_subprocess` resolves via `site_config.get_secret()` immediately before spawning the tap (see `docs/integrations/tap_singer_subprocess.md`). Only the non-secret `client_id` goes in `tap_config` directly.

### GSC

```sql
UPDATE external_taps
   SET config = jsonb_set(
        jsonb_set(
          jsonb_set(
            config,
            '{tap_config,client_id}',
            to_jsonb('<your_client_id>'::text)
          ),
          '{tap_config,site_urls}',
          '["https://www.example.com"]'::jsonb
        ),
        '{secret_fields}',
        '{"client_secret": "google_oauth_client_secret", "refresh_token": "google_oauth_refresh_token"}'::jsonb
      )
 WHERE name = 'gsc_main';
```

`site_urls` must match an EXACT property URL registered in your GSC account (typically `https://example.com` for domain properties or `https://www.example.com/` with the trailing slash for URL-prefix properties — check at https://search.google.com/search-console).

### GA4

You'll need your GA4 **property ID** — find it at https://analytics.google.com → Admin → Property → Property details → "Property ID" (a number like `123456789`). Note GA4's tap expects the client id/secret fields named `oauth_client_id`/`oauth_client_secret` rather than GSC's `client_id`/`client_secret`.

```sql
UPDATE external_taps
   SET config = jsonb_set(
        jsonb_set(
          jsonb_set(
            config,
            '{tap_config,oauth_client_id}',
            to_jsonb('<your_client_id>'::text)
          ),
          '{tap_config,property_id}',
          to_jsonb('<your_ga4_property_id>'::text)
        ),
        '{secret_fields}',
        '{"oauth_client_secret": "google_oauth_client_secret", "refresh_token": "google_oauth_refresh_token"}'::jsonb
      )
 WHERE name = 'ga4_main';
```

---

## Step 5 — Install the tap binaries

The taps run as subprocesses; the worker needs them on `PATH` (or `config.command` needs to name an absolute path where they live). Two options:

### Option A — install in a sidecar venv that the row's `command` points at (recommended)

Isolates the taps' dependencies from the worker's shared Python env. `~/.poindexter` is usually root-owned in the container, so create the venv directory as root first if `appuser` can't write to it:

```
docker exec --user root poindexter-worker sh -c "mkdir -p ~appuser/.poindexter/singer-venv && chown -R appuser:appgroup ~appuser/.poindexter/singer-venv"
docker exec -it poindexter-worker python3 -m venv ~/.poindexter/singer-venv
docker exec -it poindexter-worker ~/.poindexter/singer-venv/bin/pip install tap-google-search-console tap-ga4
```

Then point the row's `config.command` at the absolute venv binary path:

```sql
UPDATE external_taps
   SET config = jsonb_set(config, '{command}', to_jsonb('/home/appuser/.poindexter/singer-venv/bin/tap-google-search-console'::text))
 WHERE name = 'gsc_main';
```

### Option B — install in the worker container's shared env

```
docker exec -it poindexter-worker pip install tap-google-search-console tap-ga4
```

**Known risk, hit in production (2026-07-14):** `tap-google-search-console`'s dependency `singer-python==6.0.1` pins `jsonschema==2.*`. The container installs `--user` by default (its base image's site-packages is read-only), and pip's resolver only checks the package it's installing against _its own_ dependency tree — it doesn't check the shared env's other already-installed packages, so it happily downgrades `jsonschema`, silently breaking `prefect` (needs `>=4.18`), `litellm` (needs `>=4.0`), and `mcp` (needs `>=4.20`). The running process is unaffected immediately (already-imported modules stay in memory), but the _next_ container restart picks up the broken version. **Always run `docker exec poindexter-worker pip check` right after installing** — if it reports a conflict, `pip uninstall` the packages you just added (this restores the base image's own compatible versions from `/usr/local/lib/...`, which `--user` installs only ever shadow, never overwrite) and use Option A instead.

Option A avoids this class of conflict entirely — its venv's site-packages never touches the worker's shared environment.

---

## Step 6 — Test on demand

**Run this inside the worker container, not on a Windows host shell.** Whichever
option you picked in Step 5, `config.command` names a path that only exists
inside `poindexter-worker` (a venv binary, or a bare command resolved via the
container's `PATH`) — invoked from a Windows host, `asyncio.create_subprocess_exec`
fails with `FileNotFoundError: [WinError 2] The system cannot find the file
specified`, because Windows has no `/root/` or `/home/appuser/` filesystem at
all. This isn't a bug to fix; it's the same class of gotcha as running the
Ollama-backed CLI commands on host instead of in-container. Also note
`poindexter` itself isn't on the container's `PATH` — invoke it as a module:

```
docker exec -w /app poindexter-worker python3 -m poindexter taps run gsc_main
docker exec -w /app poindexter-worker python3 -m poindexter taps run ga4_main
```

> **Note:** the `taps run` CLI command only invokes enabled rows. For the on-demand test BEFORE enabling, either temporarily flip enabled and back, or call the runner inline (still inside the container, for the same reason as above):
>
> ```
> docker exec -w /app poindexter-worker python3 -c "
> import asyncio, asyncpg, os
> from services.integrations import tap_runner
> async def main():
>     pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
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

The schedule is `every 6 hours` for both. Watch the **Integrations & Admin** Grafana dashboard — the `total_records` column on the External taps table will tick up after each run.

---

## Troubleshooting

| Symptom                                                                                     | Cause                                                                                     | Fix                                                                                                                                                        |
| ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tap exited 1` with "invalid_grant"                                                         | refresh_token revoked or stale                                                            | re-run `google-oauth-setup.py`, then `poindexter settings set google_oauth_refresh_token "<new_token>" --secret` (both taps share it — no row edit needed) |
| `tap exited 1` with "Property not found" (GA4)                                              | wrong property_id                                                                         | verify in GA Admin → Property details                                                                                                                      |
| `tap exited 1` with "User does not have access" (GSC)                                       | OAuth account isn't a verified owner                                                      | grant the account access in https://search.google.com/search-console                                                                                       |
| 0 records returned                                                                          | `start_date` is in the future, or data hasn't propagated                                  | back the start_date up a week; GSC has 2-3 day lag                                                                                                         |
| `tap-google-search-console: command not found`                                              | tap not on `PATH`, or `config.command`'s venv was never populated                         | `docker exec poindexter-worker ls <venv>/bin/` to check what's actually there, then (re-)run Step 5's install                                              |
| `FileNotFoundError: [WinError 2] The system cannot find the file specified`                 | `taps run` invoked directly on a Windows host — `config.command` is a container-only path | run via `docker exec -w /app poindexter-worker python3 -m poindexter taps run <name>` instead (see Step 6)                                                 |
| `pip`'s installer warns of a `jsonschema` (or other) version conflict after Step 5 Option B | `--user` install downgraded a shared dependency `prefect`/`litellm`/`mcp` need            | `docker exec poindexter-worker pip check`; if it flags a conflict, `pip uninstall` the packages you just added and use Option A instead                    |

## Where data lands

`external_metrics` table. The mapping config on each row determines which fields become metric rows vs which become dimensions in the jsonb. For GSC each performance row produces 4 `external_metrics` rows (impressions / clicks / ctr / position); for GA4 each page_metrics row produces 4 (sessions / screenPageViews / engagementRate / userEngagementDuration).

**Writes are idempotent.** Singer taps re-emit their whole backfill window on every run (every 6h), so the writer upserts on the natural key `(source, metric_name, date, slug, dimensions)` — a re-emitted row updates in place (last-write-wins) rather than appending a copy. This lets a metric that finalises over several days (GSC clicks mature over ~3 days) converge on the latest value, and keeps `SUM(metric_value)` aggregates (e.g. `rollup_post_performance`) accurate. The `ux_external_metrics_natural_key` unique index (`NULLS NOT DISTINCT`, so the site-wide `performance_report_date` rows with NULL slug still dedup) is the arbiter — migration `20260704_033500`, which also one-time-deduped the ~382k rows the pre-upsert writer had accumulated.

Query examples:

```sql
-- Daily click trend for one slug
SELECT date, metric_value AS clicks
  FROM external_metrics
 WHERE source = 'google_search_console'
   AND metric_name = 'clicks'
   AND slug = 'your-post-slug'
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
