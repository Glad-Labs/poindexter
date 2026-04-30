# Grafana Monitoring for Poindexter

**Last Updated:** 2026-04-18

Poindexter ships with a self-hosted Grafana instance (Docker container
on port 3000) and one pre-configured dashboard. Premium dashboards
are available with the Seed Package.

## Local Setup (ships out of the box)

The `docker-compose.local.yml` / `docker-compose.yml` stack includes
Grafana with auto-provisioned datasource and dashboards. No manual
setup needed — `bash scripts/start-stack.sh` brings it up.

- **URL:** http://localhost:3000
- **Default credentials:** admin / `grafana_password` from `~/.poindexter/bootstrap.toml`
- **Datasource:** `Local Brain DB` (uid: `local-brain-db`) — auto-provisioned from `provisioning/datasources/local-postgres.yml`

## Dashboards

### Free (ships in this repo)

| File                                  | Description                                   |
| ------------------------------------- | --------------------------------------------- |
| `dashboards/pipeline-operations.json` | Pipeline status, queue depth, recent activity |

### Premium (Seed Package — $29)

Available in the `glad-labs-prompts` repo after purchase:

| File                       | Description                                  |
| -------------------------- | -------------------------------------------- |
| `approval-queue.json`      | Approval workflow + quality distribution     |
| `cost-analytics.json`      | LLM spend, model costs, electricity tracking |
| `quality-content.json`     | QA scores, rejection trends, top posts       |
| `infrastructure-data.json` | GPU, DB, audit logs, hardware monitoring     |
| `link-registry.json`       | Internal/external link tracking              |

To import premium dashboards: **Dashboards > New > Import > Upload JSON**.
Set the datasource to `Local Brain DB` if prompted.

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
