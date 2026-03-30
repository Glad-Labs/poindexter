"""Add electricity cost panels to the Grafana ops dashboard."""
import json
import urllib.request
import sys

URL = "https://gladlabs.grafana.net"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""

DS_PROM = {"uid": "grafanacloud-prom", "type": "prometheus"}

# Get current dashboard
req = urllib.request.Request(f"{URL}/api/dashboards/uid/ops-live", headers={"Authorization": f"Bearer {TOKEN}"})
data = json.loads(urllib.request.urlopen(req).read())
dashboard = data["dashboard"]
next_id = max(p["id"] for p in dashboard["panels"]) + 1

# RI Energy rate: $0.29/kWh
# Formula: watts * hours / 1000 * rate = cost
# In PromQL: increase(nvidia_gpu_power_draw_watts over time) * rate / 1000

new_panels = [
    {"id": next_id, "type": "row", "title": "Electricity Cost (RI Energy $0.29/kWh)",
     "gridPos": {"h": 1, "w": 24, "x": 0, "y": 67}, "collapsed": False},

    # Current power draw
    {"id": next_id + 1, "type": "stat", "title": "GPU Power Now",
     "gridPos": {"h": 4, "w": 6, "x": 0, "y": 68}, "datasource": DS_PROM,
     "targets": [{"expr": 'nvidia_gpu_power_draw_watts{gpu="0"}', "refId": "A"}],
     "fieldConfig": {"defaults": {"unit": "watt", "color": {"mode": "thresholds"},
         "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 200}, {"color": "red", "value": 400}]}}},
     "options": {"colorMode": "background", "graphMode": "area", "textMode": "value"}},

    # Cost per hour at current draw
    {"id": next_id + 2, "type": "stat", "title": "Cost/Hour (current)",
     "gridPos": {"h": 4, "w": 6, "x": 6, "y": 68}, "datasource": DS_PROM,
     "targets": [{"expr": 'nvidia_gpu_power_draw_watts{gpu="0"} / 1000 * 0.29', "legendFormat": "$/hr", "refId": "A"}],
     "fieldConfig": {"defaults": {"unit": "currencyUSD", "decimals": 4, "color": {"mode": "thresholds"},
         "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 0.05}, {"color": "red", "value": 0.10}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    # Estimated daily cost
    {"id": next_id + 3, "type": "stat", "title": "Est. Daily Cost",
     "gridPos": {"h": 4, "w": 6, "x": 12, "y": 68}, "datasource": DS_PROM,
     "targets": [{"expr": 'nvidia_gpu_power_draw_watts{gpu="0"} / 1000 * 0.29 * 24', "refId": "A"}],
     "fieldConfig": {"defaults": {"unit": "currencyUSD", "decimals": 2, "color": {"mode": "thresholds"},
         "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 0.50}, {"color": "red", "value": 2.00}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    # Estimated monthly cost
    {"id": next_id + 4, "type": "stat", "title": "Est. Monthly Cost",
     "gridPos": {"h": 4, "w": 6, "x": 18, "y": 68}, "datasource": DS_PROM,
     "targets": [{"expr": 'nvidia_gpu_power_draw_watts{gpu="0"} / 1000 * 0.29 * 24 * 30', "refId": "A"}],
     "fieldConfig": {"defaults": {"unit": "currencyUSD", "decimals": 2, "color": {"mode": "thresholds"},
         "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 15}, {"color": "red", "value": 30}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    # Power draw over time
    {"id": next_id + 5, "type": "timeseries", "title": "GPU Power Draw & Electricity Cost Over Time",
     "gridPos": {"h": 8, "w": 24, "x": 0, "y": 72}, "datasource": DS_PROM,
     "targets": [
         {"expr": 'nvidia_gpu_power_draw_watts{gpu="0"}', "legendFormat": "GPU Watts", "refId": "A"},
         {"expr": 'nvidia_gpu_power_draw_watts{gpu="0"} / 1000 * 0.29', "legendFormat": "$/hour", "refId": "B"},
     ],
     "fieldConfig": {"defaults": {"custom": {"fillOpacity": 20}},
         "overrides": [
             {"matcher": {"id": "byName", "options": "GPU Watts"}, "properties": [
                 {"id": "unit", "value": "watt"}, {"id": "custom.axisPlacement", "value": "left"}]},
             {"matcher": {"id": "byName", "options": "$/hour"}, "properties": [
                 {"id": "unit", "value": "currencyUSD"}, {"id": "custom.axisPlacement", "value": "right"}]},
         ]},
     "options": {"legend": {"displayMode": "table", "placement": "bottom"}}},
]

dashboard["panels"].extend(new_panels)

payload = json.dumps({"dashboard": dashboard, "overwrite": True, "folderId": 0}).encode()
req = urllib.request.Request(f"{URL}/api/dashboards/db", data=payload,
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
print(f"Updated: {URL}{json.loads(resp.read()).get('url', '')}")
