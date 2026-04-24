# Integration handlers

Per-handler operator documentation for the declarative integrations framework.

Every handler registered under `services.integrations` has a dedicated markdown doc in this directory explaining:

- What external system it talks to
- Required config fields (on the integration row)
- Required secret fields (in `app_settings`)
- Expected inbound payload shape / outbound delivery contract
- Operator runbook: how to add, test, rotate, disable

See `docs/architecture/declarative-data-plane-rfc-2026-04-24.md` for the framework itself.

## Index

Handler docs are added alongside each handler implementation. Once Phase 1 lands, expect:

- `webhook.revenue_event_writer` — Lemon Squeezy order/subscription events → `revenue_events`
- `webhook.subscriber_event_writer` — Resend email events → `subscriber_events`
- `webhook.alertmanager_dispatch` — Grafana Alertmanager alerts → `alert_events` + operator notification fan-out
- `webhook.discord_post` — outbound Discord message
- `webhook.telegram_post` — outbound Telegram Bot API message
- `webhook.vercel_isr` — outbound cache revalidation to a Vercel-hosted frontend

Handlers landing in later phases:

- `retention.ttl_prune`
- `retention.downsample`
- `retention.temporal_summarize`
- `tap.*` (Singer-protocol data taps)
- `publishing.*` (Bluesky, LinkedIn, Mastodon, Reddit, YouTube, Dev.to crosspost)
