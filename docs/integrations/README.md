# Integration handlers

Per-handler operator documentation for the declarative integrations framework.

Every handler registered under `services.integrations` has a dedicated markdown doc in this directory explaining:

- What external system it talks to
- Required config fields (on the integration row)
- Required secret fields (in `app_settings`)
- Expected inbound payload shape / outbound delivery contract
- Operator runbook: how to add, test, rotate, disable

See `docs/architecture/declarative-data-plane-rfc-2026-04-24.md` for the framework itself.

## Surfaces

The integrations registry namespaces handlers by surface so the same short name can be reused across surfaces. The five surfaces in flight today:

| Surface      | Source-of-truth table                      | Dispatcher                                          | Purpose                                                                               |
| ------------ | ------------------------------------------ | --------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `webhook`    | `webhook_endpoints` (direction=`inbound`)  | `routes/external_webhooks.py`                       | External SaaS sends an event in (Lemon Squeezy, Resend, Alertmanager).                |
| `outbound`   | `webhook_endpoints` (direction=`outbound`) | `services/integrations/outbound_dispatcher.py`      | We send an event out (Discord, Telegram, Vercel ISR purge).                           |
| `tap`        | `external_taps`                            | `services/integrations/tap_runner.py`               | Pull external data on a schedule (HackerNews, Dev.to, GA4, GSC, Singer subprocesses). |
| `retention`  | `retention_policies`                       | `services/integrations/retention_runner.py`         | Sweep aging data — TTL-prune, downsample, summarize-to-table.                         |
| `publishing` | `publishing_adapters`                      | `services/social_poster.py:_distribute_to_adapters` | Push generated content to social platforms (Bluesky, Mastodon).                       |

## Index

### `webhook` surface (inbound)

- [`webhook.revenue_event_writer`](webhook_revenue_event_writer.md) — Lemon Squeezy order/subscription events → `revenue_events`
- [`webhook.subscriber_event_writer`](webhook_subscriber_event_writer.md) — Resend email events → `subscriber_events`
- [`webhook.alertmanager_dispatch`](webhook_alertmanager_dispatch.md) — Grafana Alertmanager alerts → `alert_events` + operator notification fan-out

### `outbound` surface

- [`outbound.discord_post`](outbound_discord_post.md) — POST to a Discord webhook URL
- [`outbound.telegram_post`](outbound_telegram_post.md) — Telegram Bot API send
- [`outbound.vercel_isr`](outbound_vercel_isr.md) — Vercel ISR cache revalidation

### `tap` surface

- [`tap.builtin_topic_source`](tap_builtin_topic_source.md) — first-party TopicSources (HackerNews, Dev.to, codebase, web_search, knowledge, dev_diary)
- [`tap.external_metrics_writer`](tap_external_metrics_writer.md) — write Singer-tap output into `external_metrics`
- [`tap.singer_subprocess`](tap_singer_subprocess.md) — generic Singer-protocol subprocess runner (GA4, GSC)

### `retention` surface

- [`retention.ttl_prune`](retention_ttl_prune.md) — drop rows older than the row's `ttl_days`
- [`retention.downsample`](retention_downsample.md) — aggregate raw rows into a coarser rollup table, then delete the originals
- `retention.summarize_to_table` — LLM-based summarization of aging rows (handler implemented; doc pending)

### `publishing` surface (added 2026-05-09 via `Glad-Labs/poindexter#112`)

- [`publishing.bluesky`](publishing_bluesky.md) — post to a Bluesky account via the AT Protocol
- [`publishing.mastodon`](publishing_mastodon.md) — post to a Mastodon (or any Fediverse) instance

## Adding a new platform

The point of the declarative-data-plane is that adding a new social platform = insert a row + register a `<surface>.<name>` handler. No edit to the dispatcher.

1. Write the handler module under `services/integrations/handlers/` (e.g. `publishing_linkedin.py`):

   ```python
   from services.integrations.registry import register_handler

   @register_handler("publishing", "linkedin")
   async def linkedin_post(payload, *, site_config, row, pool):
       ...
   ```

2. Add it to `services/integrations/handlers/__init__.py:load_all()` so it imports at startup.

3. Add a seed row via a migration (look at `services/migrations/20260509_175447_add_publishing_adapters.py` for the shape).

4. Document the handler here under `docs/integrations/publishing_linkedin.md`. Mirror the existing per-handler doc shape.

5. Done. `poindexter publishers list` will show the new row; `poindexter publishers enable linkedin_main` activates it.
