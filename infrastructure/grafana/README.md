# Grafana Cloud Setup for Glad Labs

Connect Grafana Cloud to Railway PostgreSQL for production monitoring.

## Prerequisites

- Grafana Cloud account (free tier works): https://grafana.com/products/cloud/
- Railway PostgreSQL connection string (from Railway dashboard)

## 1. Add PostgreSQL Data Source

1. In Grafana Cloud, go to **Connections > Data sources > Add data source**
2. Select **PostgreSQL**
3. Configure the connection using your Railway credentials:

   | Field            | Value                             |
   | ---------------- | --------------------------------- |
   | **Name**         | `gladlabs-postgres`               |
   | **Host**         | `<railway-host>:<port>`           |
   | **Database**     | `railway` (or your DB name)       |
   | **User**         | `postgres`                        |
   | **Password**     | Your Railway PostgreSQL password  |
   | **TLS/SSL Mode** | `require`                         |
   | **Version**      | 15.x (match your Railway version) |

4. Set the data source UID to `gladlabs-postgres` (used by all dashboards).
   - After creating the source, go to **Settings** and update the UID field, or
     note the auto-generated UID and update the dashboard JSON files.
5. Click **Save & Test** to verify the connection.

> **Security note:** Grafana Cloud connects to Railway over the public internet.
> Ensure your Railway PostgreSQL instance has SSL enabled (it does by default).
> Consider restricting access via Railway's networking settings if available.

## 2. Import Dashboards

Three dashboards are provided in `dashboards/`:

| File                     | Description                          |
| ------------------------ | ------------------------------------ |
| `pipeline-overview.json` | Content pipeline status and velocity |
| `cost-control.json`      | LLM spend tracking and budgets       |
| `quality-metrics.json`   | Content quality scores and pass rate |

### Import steps

1. Go to **Dashboards > New > Import**
2. Click **Upload dashboard JSON file**
3. Select the JSON file from `dashboards/`
4. Set the data source to `gladlabs-postgres` if prompted
5. Click **Import**

Repeat for each dashboard file.

### Programmatic import (optional)

```bash
# Using Grafana HTTP API
GRAFANA_URL="https://<your-instance>.grafana.net"
GRAFANA_TOKEN="glsa_..."

for f in dashboards/*.json; do
  curl -X POST "$GRAFANA_URL/api/dashboards/db" \
    -H "Authorization: Bearer $GRAFANA_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"dashboard\": $(cat "$f"), \"overwrite\": true}"
done
```

## 3. Configure Alerts

Alert rules are defined in `alerts/discord-alerts.yaml`. These are reference
definitions -- import them manually in Grafana Cloud:

1. Go to **Alerting > Alert rules > New alert rule**
2. Use the queries and thresholds from the YAML file
3. Set up a **Discord** contact point under **Alerting > Contact points**:
   - Type: Discord
   - Webhook URL: your Discord channel webhook
4. Create a **Notification policy** routing alerts to the Discord contact point

## 4. Railway Connection String

Find your Railway PostgreSQL connection string:

1. Open your project in the Railway dashboard
2. Click the PostgreSQL service
3. Go to the **Connect** tab
4. Copy the **Public URL** (format: `postgresql://postgres:PASSWORD@HOST:PORT/railway`)

Parse it into the Grafana fields:

```
postgresql://postgres:PASSWORD@HOST:PORT/railway
              ^^^^^^^^ ^^^^^^^^  ^^^^ ^^^^ ^^^^^^^
              user     password  host port database
```

## Datasource UID

All dashboard JSON files reference the datasource UID `gladlabs-postgres`.
If your data source gets a different UID, update the `"uid"` field in each
dashboard JSON file, or rename the data source UID in Grafana to match.
