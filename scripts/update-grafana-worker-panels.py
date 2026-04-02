"""Add worker status panels to the Grafana ops dashboard."""
import json
import urllib.request
import sys

URL = "http://localhost:3000"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""

DS_PG = {"uid": "gladlabs-postgres"}
DS_PROM = {"uid": "grafanacloud-prom", "type": "prometheus"}

# Get current dashboard
req = urllib.request.Request(f"{URL}/api/dashboards/uid/ops-live", headers={"Authorization": f"Bearer {TOKEN}"})
data = json.loads(urllib.request.urlopen(req).read())
dashboard = data["dashboard"]

next_id = max(p["id"] for p in dashboard["panels"]) + 1

new_panels = [
    {"id": next_id, "type": "row", "title": "Worker & System Status",
     "gridPos": {"h": 1, "w": 24, "x": 0, "y": 48}, "collapsed": False},
    {"id": next_id + 1, "type": "stat", "title": "Tasks Processed",
     "gridPos": {"h": 4, "w": 6, "x": 0, "y": 49}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM content_tasks WHERE status = 'published'", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "yellow", "value": None}, {"color": "green", "value": 10}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},
    {"id": next_id + 2, "type": "stat", "title": "Tasks Last Hour",
     "gridPos": {"h": 4, "w": 6, "x": 6, "y": 49}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM content_tasks WHERE updated_at > NOW() - INTERVAL '1 hour'", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "red", "value": None}, {"color": "yellow", "value": 1}, {"color": "green", "value": 3}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},
    {"id": next_id + 3, "type": "stat", "title": "Failed Tasks",
     "gridPos": {"h": 4, "w": 6, "x": 12, "y": 49}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM content_tasks WHERE status = 'failed'", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 1}, {"color": "red", "value": 3}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},
    {"id": next_id + 4, "type": "stat", "title": "Rejected (QA)",
     "gridPos": {"h": 4, "w": 6, "x": 18, "y": 49}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM content_tasks WHERE status = 'rejected'", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 1}, {"color": "red", "value": 5}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},
    {"id": next_id + 5, "type": "gauge", "title": "GPU Utilization",
     "gridPos": {"h": 6, "w": 8, "x": 0, "y": 53}, "datasource": DS_PROM,
     "targets": [{"expr": 'nvidia_gpu_utilization_percent{gpu="0"}', "refId": "A"}],
     "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 90}]}}}},
    {"id": next_id + 6, "type": "gauge", "title": "GPU Temp",
     "gridPos": {"h": 6, "w": 8, "x": 8, "y": 53}, "datasource": DS_PROM,
     "targets": [{"expr": 'nvidia_gpu_temperature_celsius{gpu="0"}', "refId": "A"}],
     "fieldConfig": {"defaults": {"unit": "celsius", "min": 0, "max": 100, "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 85}]}}}},
    {"id": next_id + 7, "type": "gauge", "title": "CPU Usage",
     "gridPos": {"h": 6, "w": 8, "x": 16, "y": 53}, "datasource": DS_PROM,
     "targets": [{"expr": '100 - (avg(rate(windows_cpu_time_total{mode="idle"}[1m])) * 100)', "refId": "A"}],
     "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 90}]}}}},
    {"id": next_id + 8, "type": "timeseries", "title": "Tasks Completed Over Time",
     "gridPos": {"h": 8, "w": 24, "x": 0, "y": 59}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT date_trunc('hour', updated_at) as time, COUNT(*) as tasks FROM content_tasks WHERE status = 'published' AND updated_at > NOW() - INTERVAL '7 days' GROUP BY 1 ORDER BY 1", "format": "time_series", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "green"}, "custom": {"drawStyle": "bars", "fillOpacity": 80}}},
     "options": {"legend": {"displayMode": "hidden"}}},
]

dashboard["panels"].extend(new_panels)

payload = json.dumps({"dashboard": dashboard, "overwrite": True, "folderId": 0}).encode()
req = urllib.request.Request(f"{URL}/api/dashboards/db", data=payload,
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
print(f"Updated: {URL}{json.loads(resp.read()).get('url', '')}")
