# Grafana Monitoring for Poindexter

**Last Updated:** 2026-07-13

Poindexter ships with a self-hosted Grafana instance (Docker container
on port 3000) and a full set of pre-configured dashboards. Dashboards
are not feature-gated — the whole monitoring stack is part of the
free, Apache-2.0 engine.

## Local Setup (ships out of the box)

The `docker-compose.local.yml` / `docker-compose.yml` stack includes
Grafana with auto-provisioned datasource and dashboards. No manual
setup needed — `bash scripts/start-stack.sh` brings it up.

- **URL:** http://localhost:3000
- **Default credentials:** admin / `grafana_password` from `~/.poindexter/bootstrap.toml`
- **Datasource:** `Local Brain DB` (uid: `local-brain-db`) — auto-provisioned from `provisioning/datasources/local-postgres.yml`

## Dashboards

### Free (ships in this repo, auto-provisioned)

All boards under `dashboards/` ship in this repo and are
auto-provisioned by the Docker stack on first boot — no import step
needed.

| File                        | Description                                                                |
| --------------------------- | -------------------------------------------------------------------------- |
| `pipeline-merged.json`      | Pipeline throughput, approval queue, quality/QA rows, media approval queue |
| `cost-analytics.json`       | LLM spend, model costs, electricity tracking                               |
| `qa-rails.json`             | Per-reviewer pass-rate, score distribution, latest QA passes               |
| `observability-merged.json` | Tempo traces, Pyroscope flame graphs, Loki logs, API HTTP RED metrics      |
| `system-health-merged.json` | Service up/down, scheduled-publish queue, approved-queue                   |
| `database.json`             | Postgres internals — size, connections, table stats, cache-hit ratio       |
| `hardware-power.json`       | GPU live metrics, PSU/wall/CPU power sensors + electricity cost            |
| `integrations-admin.json`   | qa_gates / publishing_adapters / external_taps declarative-config tables   |
| `experiments-dryrun.json`   | Auto-publish gate dry-run observability + variant experiments              |
| `findings.json`             | Probe-findings routing — emitted vs. pending-delivery, by kind/severity    |
| `seo-harvest.json`          | SEO harvest metrics                                                        |

One board is intentionally absent from the public `Glad-Labs/poindexter`
mirror: `mission-control.json` is the operator's own top-level glance
and embeds a private Tailscale hostname (see the strip in
`scripts/sync-to-github.sh`) — it ships in this source repo but not the
public one, and isn't a useful template to fork anyway. Build your own
top-level view from the boards above, or start from `pipeline-merged.json`.

A `revenue.json` board also exists, parked in `dashboards-parked/`
until `revenue_events` has real data to show.

### Poindexter Pro

[Poindexter Pro](https://www.gladlabs.ai) is a subscription to Matt's
continuously-tuned system, delivered via a private collaborator-invite
repo (`Glad-Labs/poindexter-pro`) — not a code-level unlock. It bundles
a periodically-refreshed copy of 5 of the boards above (Pipeline, QA
Rails, Cost & Analytics, Revenue, Observability) alongside premium
prompts, a tuned `app_settings` seed, and the operator book. You
already have all of these dashboards for free by running this repo —
Pro buys freshness and curation, not access.

## Alerts

Alert contact points are configured via the Grafana UI after first
boot (Settings > Contact Points). Tokens for Telegram/Discord are
stored in `app_settings`, not in provisioning files.

Reference alert definitions are in `alerts/discord-alerts.yaml` —
these are templates, not auto-provisioned.

## Grafana Cloud (optional)

If you want to use Grafana Cloud instead of the local instance:

1. Add a PostgreSQL datasource pointing at your Poindexter database
2. Set the datasource UID to `local-brain-db` (or update the UID
   in each dashboard JSON)
3. Import the dashboard JSON files via the Grafana UI or API
4. Ensure SSL is enabled if connecting over the public internet

| Field        | Value              |
| ------------ | ------------------ |
| **Database** | `poindexter_brain` |
| **User**     | `poindexter`       |
| **Version**  | 16.x               |

## Datasource UID

All dashboard JSON files reference `"uid": "local-brain-db"`. If
your datasource gets a different UID, either rename it in Grafana
or find-and-replace in the dashboard JSON files.
