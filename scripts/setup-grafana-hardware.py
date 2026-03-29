"""Create hardware monitoring dashboard in Grafana Cloud."""
import json
import urllib.request
import sys

GRAFANA_URL = "https://gladlabs.grafana.net"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
DS = {"uid": "grafanacloud-prom", "type": "prometheus"}

dashboard = {
    "uid": "hw-monitor",
    "title": "Nightrider Hardware Monitor",
    "tags": ["hardware", "gpu", "live"],
    "timezone": "browser",
    "refresh": "15s",
    "time": {"from": "now-1h", "to": "now"},
    "panels": [
        {"id": 1, "type": "gauge", "title": "GPU Utilization",
         "gridPos": {"h": 6, "w": 6, "x": 0, "y": 0}, "datasource": DS,
         "targets": [{"expr": 'nvidia_gpu_utilization_percent{gpu="0"}', "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100,
             "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 90}]}}}},
        {"id": 2, "type": "gauge", "title": "GPU Temp",
         "gridPos": {"h": 6, "w": 6, "x": 6, "y": 0}, "datasource": DS,
         "targets": [{"expr": 'nvidia_gpu_temperature_celsius{gpu="0"}', "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "celsius", "min": 0, "max": 100,
             "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 85}]}}}},
        {"id": 3, "type": "gauge", "title": "GPU Power",
         "gridPos": {"h": 6, "w": 6, "x": 12, "y": 0}, "datasource": DS,
         "targets": [{"expr": 'nvidia_gpu_power_draw_watts{gpu="0"}', "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "watt", "min": 0, "max": 600,
             "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 350}, {"color": "red", "value": 500}]}}}},
        {"id": 4, "type": "gauge", "title": "VRAM Used",
         "gridPos": {"h": 6, "w": 6, "x": 18, "y": 0}, "datasource": DS,
         "targets": [{"expr": 'nvidia_gpu_memory_used_mib{gpu="0"} / nvidia_gpu_memory_total_mib{gpu="0"} * 100', "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100,
             "thresholds": {"steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 90}]}}}},
        {"id": 5, "type": "timeseries", "title": "GPU Utilization Over Time",
         "gridPos": {"h": 8, "w": 12, "x": 0, "y": 6}, "datasource": DS,
         "targets": [{"expr": 'nvidia_gpu_utilization_percent{gpu="0"}', "legendFormat": "GPU %", "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "color": {"mode": "fixed", "fixedColor": "green"}, "custom": {"fillOpacity": 30}}}},
        {"id": 6, "type": "timeseries", "title": "Temperature & Power",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": 6}, "datasource": DS,
         "targets": [
             {"expr": 'nvidia_gpu_temperature_celsius{gpu="0"}', "legendFormat": "Temp C", "refId": "A"},
             {"expr": 'nvidia_gpu_power_draw_watts{gpu="0"}', "legendFormat": "Power W", "refId": "B"}],
         "fieldConfig": {"defaults": {"custom": {"fillOpacity": 20}}}},
        {"id": 7, "type": "timeseries", "title": "CPU Usage",
         "gridPos": {"h": 8, "w": 12, "x": 0, "y": 14}, "datasource": DS,
         "targets": [{"expr": '100 - (avg(rate(windows_cpu_time_total{mode="idle"}[1m])) * 100)', "legendFormat": "CPU %", "refId": "A"}],
         "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "color": {"mode": "fixed", "fixedColor": "blue"}, "custom": {"fillOpacity": 30}}}},
        {"id": 8, "type": "timeseries", "title": "Memory Usage",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": 14}, "datasource": DS,
         "targets": [
             {"expr": "windows_os_physical_memory_free_bytes / 1024 / 1024 / 1024", "legendFormat": "Free GB", "refId": "A"},
             {"expr": "(windows_cs_physical_memory_bytes - windows_os_physical_memory_free_bytes) / 1024 / 1024 / 1024", "legendFormat": "Used GB", "refId": "B"}],
         "fieldConfig": {"defaults": {"unit": "decgbytes", "custom": {"fillOpacity": 40, "stacking": {"mode": "normal"}}}}},
        {"id": 9, "type": "timeseries", "title": "VRAM Usage (GB)",
         "gridPos": {"h": 8, "w": 24, "x": 0, "y": 22}, "datasource": DS,
         "targets": [
             {"expr": 'nvidia_gpu_memory_used_mib{gpu="0"} / 1024', "legendFormat": "Used GB", "refId": "A"},
             {"expr": 'nvidia_gpu_memory_total_mib{gpu="0"} / 1024', "legendFormat": "Total GB", "refId": "B"}],
         "fieldConfig": {"defaults": {"unit": "decgbytes", "custom": {"fillOpacity": 20}}}},
    ]
}

payload = json.dumps({"dashboard": dashboard, "overwrite": True, "folderId": 0}).encode()
req = urllib.request.Request(
    f"{GRAFANA_URL}/api/dashboards/db", data=payload,
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
result = json.loads(resp.read())
print(f"Dashboard: {GRAFANA_URL}{result.get('url', '')}")
