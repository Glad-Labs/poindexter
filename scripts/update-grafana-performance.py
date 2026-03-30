"""Add performance metrics panels to Grafana — Railway, Worker, LLM, DB."""
import json
import urllib.request
import sys

URL = "https://gladlabs.grafana.net"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""

DS_PG = {"uid": "gladlabs-postgres"}
DS_PROM = {"uid": "grafanacloud-prom", "type": "prometheus"}

# Create a dedicated Performance dashboard
dashboard = {
    "uid": "performance",
    "title": "System Performance",
    "tags": ["performance", "live"],
    "timezone": "browser",
    "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"},
    "panels": [
        # ========== SYSTEM RESOURCES ==========
        {"id": 1, "type": "row", "title": "System Resources",
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0}, "collapsed": False},

        {"id": 2, "type": "timeseries", "title": "CPU Usage (%)",
         "gridPos": {"h": 8, "w": 12, "x": 0, "y": 1}, "datasource": DS_PROM,
         "targets": [{"expr": '100 - (avg(rate(windows_cpu_time_total{mode="idle"}[1m])) * 100)', "legendFormat": "CPU %", "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "color": {"mode": "fixed", "fixedColor": "blue"}, "custom": {"fillOpacity": 30}}}},

        {"id": 3, "type": "timeseries", "title": "Memory Usage (GB)",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": 1}, "datasource": DS_PROM,
         "targets": [
             {"expr": "(windows_cs_physical_memory_bytes - windows_os_physical_memory_free_bytes) / 1024 / 1024 / 1024", "legendFormat": "Used GB", "refId": "A"},
             {"expr": "windows_cs_physical_memory_bytes / 1024 / 1024 / 1024", "legendFormat": "Total GB", "refId": "B"},
         ],
         "fieldConfig": {"defaults": {"unit": "decgbytes", "custom": {"fillOpacity": 20}}}},

        # ========== GPU PERFORMANCE ==========
        {"id": 4, "type": "row", "title": "GPU Performance (RTX 5090)",
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": 9}, "collapsed": False},

        {"id": 5, "type": "timeseries", "title": "GPU Utilization & Memory",
         "gridPos": {"h": 8, "w": 12, "x": 0, "y": 10}, "datasource": DS_PROM,
         "targets": [
             {"expr": 'nvidia_gpu_utilization_percent{gpu="0"}', "legendFormat": "GPU %", "refId": "A"},
             {"expr": 'nvidia_gpu_memory_utilization_percent{gpu="0"}', "legendFormat": "VRAM %", "refId": "B"},
         ],
         "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "custom": {"fillOpacity": 20}}}},

        {"id": 6, "type": "timeseries", "title": "GPU Temperature & Power",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": 10}, "datasource": DS_PROM,
         "targets": [
             {"expr": 'nvidia_gpu_temperature_celsius{gpu="0"}', "legendFormat": "Temp C", "refId": "A"},
             {"expr": 'nvidia_gpu_power_draw_watts{gpu="0"}', "legendFormat": "Power W", "refId": "B"},
         ],
         "fieldConfig": {"defaults": {"custom": {"fillOpacity": 15}},
             "overrides": [
                 {"matcher": {"id": "byName", "options": "Temp C"}, "properties": [{"id": "unit", "value": "celsius"}, {"id": "custom.axisPlacement", "value": "left"}]},
                 {"matcher": {"id": "byName", "options": "Power W"}, "properties": [{"id": "unit", "value": "watt"}, {"id": "custom.axisPlacement", "value": "right"}]},
             ]}},

        {"id": 7, "type": "timeseries", "title": "VRAM Usage (GB)",
         "gridPos": {"h": 8, "w": 12, "x": 0, "y": 18}, "datasource": DS_PROM,
         "targets": [
             {"expr": 'nvidia_gpu_memory_used_mib{gpu="0"} / 1024', "legendFormat": "Used GB", "refId": "A"},
             {"expr": 'nvidia_gpu_memory_total_mib{gpu="0"} / 1024', "legendFormat": "Total GB", "refId": "B"},
         ],
         "fieldConfig": {"defaults": {"unit": "decgbytes", "custom": {"fillOpacity": 20}}}},

        {"id": 8, "type": "timeseries", "title": "GPU Clock Speeds (MHz)",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": 18}, "datasource": DS_PROM,
         "targets": [
             {"expr": 'nvidia_gpu_clock_graphics_mhz{gpu="0"}', "legendFormat": "Graphics", "refId": "A"},
             {"expr": 'nvidia_gpu_clock_memory_mhz{gpu="0"}', "legendFormat": "Memory", "refId": "B"},
         ],
         "fieldConfig": {"defaults": {"unit": "rotmhz", "custom": {"fillOpacity": 10}}}},

        # ========== CONTENT PIPELINE PERFORMANCE ==========
        {"id": 9, "type": "row", "title": "Content Pipeline Performance",
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": 26}, "collapsed": False},

        {"id": 10, "type": "timeseries", "title": "Tasks Completed Per Hour",
         "gridPos": {"h": 8, "w": 12, "x": 0, "y": 27}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT date_trunc('hour', updated_at) as time, COUNT(*) as tasks FROM content_tasks WHERE status = 'published' AND updated_at > NOW() - INTERVAL '7 days' GROUP BY 1 ORDER BY 1", "format": "time_series", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "green"}, "custom": {"drawStyle": "bars", "fillOpacity": 80}}}},

        {"id": 11, "type": "timeseries", "title": "Quality Scores Over Time",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": 27}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT created_at as time, quality_score FROM content_tasks WHERE quality_score IS NOT NULL AND created_at > NOW() - INTERVAL '7 days' ORDER BY created_at", "format": "time_series", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "purple"}, "custom": {"drawStyle": "points", "pointSize": 8}}}},

        # ========== LLM / TOKEN USAGE ==========
        {"id": 12, "type": "row", "title": "LLM & Token Usage",
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": 35}, "collapsed": False},

        {"id": 13, "type": "stat", "title": "Total API Calls",
         "gridPos": {"h": 4, "w": 6, "x": 0, "y": 36}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT COUNT(*) FROM cost_logs", "format": "table", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "blue", "value": None}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value", "reduceOptions": {"calcs": ["lastNotNull"]}}},

        {"id": 14, "type": "stat", "title": "Total Tokens Used",
         "gridPos": {"h": 4, "w": 6, "x": 6, "y": 36}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM cost_logs", "format": "table", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "blue", "value": None}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

        {"id": 15, "type": "stat", "title": "Total Cloud Spend",
         "gridPos": {"h": 4, "w": 6, "x": 12, "y": 36}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs", "format": "table", "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "currencyUSD", "color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 5}, {"color": "red", "value": 20}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

        {"id": 16, "type": "stat", "title": "Electricity Cost (est/mo)",
         "gridPos": {"h": 4, "w": 6, "x": 18, "y": 36}, "datasource": DS_PROM,
         "targets": [{"expr": 'nvidia_gpu_power_draw_watts{gpu="0"} / 1000 * 0.29 * 24 * 30', "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "currencyUSD", "decimals": 2, "color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 15}, {"color": "red", "value": 30}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

        {"id": 17, "type": "timeseries", "title": "API Cost Over Time",
         "gridPos": {"h": 8, "w": 12, "x": 0, "y": 40}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT date_trunc('day', created_at) as time, SUM(cost_usd) as cost FROM cost_logs WHERE created_at > NOW() - INTERVAL '30 days' GROUP BY 1 ORDER BY 1", "format": "time_series", "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "currencyUSD", "color": {"mode": "fixed", "fixedColor": "orange"}, "custom": {"drawStyle": "bars", "fillOpacity": 80}}},
         "options": {"legend": {"displayMode": "hidden"}}},

        {"id": 18, "type": "piechart", "title": "Cost by Provider",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": 40}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT provider, SUM(cost_usd) as cost FROM cost_logs GROUP BY provider ORDER BY cost DESC", "format": "table", "refId": "A"}],
         "options": {"legend": {"displayMode": "table", "placement": "right"}, "pieType": "donut"}},

        # ========== DATABASE PERFORMANCE ==========
        {"id": 19, "type": "row", "title": "Database",
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": 48}, "collapsed": False},

        {"id": 20, "type": "stat", "title": "DB Tables",
         "gridPos": {"h": 4, "w": 6, "x": 0, "y": 49}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'", "format": "table", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "blue", "value": None}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

        {"id": 21, "type": "stat", "title": "Applied Migrations",
         "gridPos": {"h": 4, "w": 6, "x": 6, "y": 49}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT COUNT(*) FROM schema_migrations", "format": "table", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "green", "value": None}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

        {"id": 22, "type": "stat", "title": "Total Rows (posts)",
         "gridPos": {"h": 4, "w": 6, "x": 12, "y": 49}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT COUNT(*) FROM posts", "format": "table", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "green", "value": None}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

        {"id": 23, "type": "stat", "title": "Total Rows (tasks)",
         "gridPos": {"h": 4, "w": 6, "x": 18, "y": 49}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT COUNT(*) FROM content_tasks", "format": "table", "refId": "A"}],
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "green", "value": None}]}}},
         "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

        {"id": 24, "type": "table", "title": "Table Sizes",
         "gridPos": {"h": 8, "w": 24, "x": 0, "y": 53}, "datasource": DS_PG,
         "targets": [{"rawSql": "SELECT relname as table_name, n_live_tup as row_count, pg_size_pretty(pg_total_relation_size(relid)) as total_size FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 20", "format": "table", "refId": "A"}],
         "options": {"showHeader": True}},

        # ========== NETWORK ==========
        {"id": 25, "type": "row", "title": "Network",
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": 61}, "collapsed": False},

        {"id": 26, "type": "timeseries", "title": "Network Traffic (bytes/sec)",
         "gridPos": {"h": 8, "w": 24, "x": 0, "y": 62}, "datasource": DS_PROM,
         "targets": [
             {"expr": "rate(windows_net_bytes_received_total[1m])", "legendFormat": "RX {{nic}}", "refId": "A"},
             {"expr": "rate(windows_net_bytes_sent_total[1m])", "legendFormat": "TX {{nic}}", "refId": "B"},
         ],
         "fieldConfig": {"defaults": {"unit": "Bps", "custom": {"fillOpacity": 15}}}},
    ]
}

payload = json.dumps({"dashboard": dashboard, "overwrite": True, "folderId": 0}).encode()
req = urllib.request.Request(f"{URL}/api/dashboards/db", data=payload,
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
result = json.loads(resp.read())
print(f"Dashboard: {URL}{result.get('url', '')}")

# Update playlist to include performance dashboard
playlists = json.loads(urllib.request.urlopen(
    urllib.request.Request(f"{URL}/api/playlists", headers={"Authorization": f"Bearer {TOKEN}"})
).read())
if playlists:
    pl = playlists[0]
    pl_uid = pl.get("uid", "")
    items = [
        {"type": "dashboard_by_uid", "value": "ops-live", "order": 1, "title": "Operations"},
        {"type": "dashboard_by_uid", "value": "performance", "order": 2, "title": "Performance"},
        {"type": "dashboard_by_uid", "value": "hw-monitor", "order": 3, "title": "Hardware"},
        {"type": "dashboard_by_uid", "value": "pipeline-overview", "order": 4, "title": "Pipeline"},
        {"type": "dashboard_by_uid", "value": "cost-control", "order": 5, "title": "Cost Control"},
        {"type": "dashboard_by_uid", "value": "quality-metrics", "order": 6, "title": "Quality"},
    ]
    urllib.request.urlopen(urllib.request.Request(
        f"{URL}/api/playlists/{pl_uid}",
        data=json.dumps({"name": "Glad Labs Command Center", "interval": "30s", "items": items}).encode(),
        method="PUT",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    ))
    print(f"Playlist updated with Performance dashboard")
