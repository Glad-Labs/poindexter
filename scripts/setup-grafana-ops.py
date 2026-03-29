"""Create the Glad Labs Operations dashboard in Grafana Cloud."""
import json
import urllib.request
import sys

GRAFANA_URL = "https://gladlabs.grafana.net"
GRAFANA_TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""

if not GRAFANA_TOKEN:
    print("Usage: python setup-grafana-ops.py <grafana-api-token>")
    sys.exit(1)

DS = {"uid": "gladlabs-postgres"}

def stat_panel(id, title, sql, x, thresholds, unit="none"):
    return {
        "id": id, "type": "stat", "title": title,
        "gridPos": {"h": 4, "w": 6, "x": x, "y": 0},
        "datasource": DS,
        "targets": [{"rawSql": sql, "format": "table", "refId": "A"}],
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "thresholds"}, "thresholds": {"steps": thresholds}}},
        "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}
    }

dashboard = {
    "uid": "ops-live",
    "title": "Glad Labs Operations",
    "tags": ["ops", "live"],
    "timezone": "browser",
    "refresh": "30s",
    "time": {"from": "now-24h", "to": "now"},
    "panels": [
        # Row 1: Key stats
        stat_panel(1, "Published Posts",
            "SELECT COUNT(*) as posts FROM posts WHERE status = 'published'",
            0, [{"color": "red", "value": None}, {"color": "yellow", "value": 5}, {"color": "green", "value": 20}]),
        stat_panel(2, "Queue (Pending)",
            "SELECT COUNT(*) as pending FROM content_tasks WHERE status = 'pending'",
            6, [{"color": "green", "value": None}, {"color": "yellow", "value": 5}, {"color": "orange", "value": 15}]),
        stat_panel(3, "In Progress",
            "SELECT COUNT(*) as active FROM content_tasks WHERE status = 'in_progress'",
            12, [{"color": "green", "value": None}, {"color": "yellow", "value": 3}]),
        stat_panel(4, "Avg Quality",
            "SELECT ROUND(AVG(quality_score)::numeric, 1) as q FROM content_tasks WHERE quality_score IS NOT NULL AND created_at > NOW() - INTERVAL '7 days'",
            18, [{"color": "red", "value": None}, {"color": "yellow", "value": 65}, {"color": "green", "value": 75}]),

        # Row 2: Status donut + recent tasks table
        {
            "id": 5, "type": "piechart", "title": "Task Status",
            "gridPos": {"h": 8, "w": 8, "x": 0, "y": 4},
            "datasource": DS,
            "targets": [{"rawSql": "SELECT status, COUNT(*) as count FROM content_tasks GROUP BY status ORDER BY count DESC", "format": "table", "refId": "A"}],
            "options": {"legend": {"displayMode": "table", "placement": "right"}, "pieType": "donut"},
            "fieldConfig": {"overrides": [
                {"matcher": {"id": "byName", "options": "published"}, "properties": [{"id": "color", "value": {"fixedColor": "green", "mode": "fixed"}}]},
                {"matcher": {"id": "byName", "options": "pending"}, "properties": [{"id": "color", "value": {"fixedColor": "yellow", "mode": "fixed"}}]},
                {"matcher": {"id": "byName", "options": "in_progress"}, "properties": [{"id": "color", "value": {"fixedColor": "blue", "mode": "fixed"}}]},
                {"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "color", "value": {"fixedColor": "red", "mode": "fixed"}}]},
                {"matcher": {"id": "byName", "options": "awaiting_approval"}, "properties": [{"id": "color", "value": {"fixedColor": "orange", "mode": "fixed"}}]},
            ]}
        },
        {
            "id": 6, "type": "table", "title": "Recent Tasks (24h)",
            "gridPos": {"h": 8, "w": 16, "x": 8, "y": 4},
            "datasource": DS,
            "targets": [{"rawSql": "SELECT LEFT(topic, 50) as topic, status, quality_score as quality, LEFT(model_used, 20) as model, created_at::timestamp(0) as created FROM content_tasks WHERE created_at > NOW() - INTERVAL '24 hours' ORDER BY created_at DESC LIMIT 20", "format": "table", "refId": "A"}],
            "options": {"showHeader": True, "footer": {"show": False}},
            "fieldConfig": {"defaults": {}, "overrides": [
                {"matcher": {"id": "byName", "options": "status"}, "properties": [{"id": "custom.width", "value": 130}]},
                {"matcher": {"id": "byName", "options": "quality"}, "properties": [{"id": "custom.width", "value": 70}]},
            ]}
        },

        # Row 3: Posts published daily + cost daily
        {
            "id": 7, "type": "timeseries", "title": "Posts Published (Daily)",
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12},
            "datasource": DS,
            "targets": [{"rawSql": "SELECT date_trunc('day', published_at) as time, COUNT(*) as published FROM posts WHERE published_at IS NOT NULL AND published_at > NOW() - INTERVAL '30 days' GROUP BY 1 ORDER BY 1", "format": "time_series", "refId": "A"}],
            "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "green"}, "custom": {"drawStyle": "bars", "fillOpacity": 80}}},
            "options": {"legend": {"displayMode": "hidden"}}
        },
        {
            "id": 8, "type": "timeseries", "title": "LLM Cost (Daily)",
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 12},
            "datasource": DS,
            "targets": [{"rawSql": "SELECT date_trunc('day', created_at) as time, SUM(cost_usd) as cost FROM cost_logs WHERE created_at > NOW() - INTERVAL '30 days' GROUP BY 1 ORDER BY 1", "format": "time_series", "refId": "A"}],
            "fieldConfig": {"defaults": {"unit": "currencyUSD", "color": {"mode": "fixed", "fixedColor": "orange"}, "custom": {"drawStyle": "bars", "fillOpacity": 80}}},
            "options": {"legend": {"displayMode": "hidden"}}
        },

        # Row 4: Quality scatter + latest posts
        {
            "id": 9, "type": "timeseries", "title": "Quality Score Trend",
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 20},
            "datasource": DS,
            "targets": [{"rawSql": "SELECT created_at as time, quality_score as quality FROM content_tasks WHERE quality_score IS NOT NULL AND created_at > NOW() - INTERVAL '30 days' ORDER BY created_at", "format": "time_series", "refId": "A"}],
            "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "purple"}, "custom": {"drawStyle": "points", "pointSize": 8}}},
            "options": {"legend": {"displayMode": "hidden"}}
        },
        {
            "id": 10, "type": "table", "title": "Latest Published Posts",
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 20},
            "datasource": DS,
            "targets": [{"rawSql": "SELECT LEFT(title, 55) as title, published_at::date as published, view_count as views FROM posts WHERE status = 'published' ORDER BY published_at DESC NULLS LAST LIMIT 10", "format": "table", "refId": "A"}],
            "options": {"showHeader": True, "footer": {"show": False}}
        },
    ]
}

payload = json.dumps({"dashboard": dashboard, "overwrite": True, "folderId": 0}).encode()
req = urllib.request.Request(
    f"{GRAFANA_URL}/api/dashboards/db",
    data=payload,
    headers={"Authorization": f"Bearer {GRAFANA_TOKEN}", "Content-Type": "application/json"}
)
resp = urllib.request.urlopen(req)
result = json.loads(resp.read())
print(f"Dashboard created: {GRAFANA_URL}{result.get('url', '')}")

# Set as home dashboard
home_payload = json.dumps({"homeDashboardUID": "ops-live"}).encode()
home_req = urllib.request.Request(
    f"{GRAFANA_URL}/api/org/preferences",
    data=home_payload,
    method="PATCH",
    headers={"Authorization": f"Bearer {GRAFANA_TOKEN}", "Content-Type": "application/json"}
)
try:
    urllib.request.urlopen(home_req)
    print("Set as home dashboard")
except Exception as e:
    print(f"Could not set as home (may need org admin): {e}")
