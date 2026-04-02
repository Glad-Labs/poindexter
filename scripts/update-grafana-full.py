"""Add comprehensive panels to the Grafana ops dashboard — flood of information."""
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
    # ========== TRAFFIC SECTION ==========
    {"id": next_id, "type": "row", "title": "Traffic & Analytics",
     "gridPos": {"h": 1, "w": 24, "x": 0, "y": 80}, "collapsed": False},

    {"id": next_id + 1, "type": "stat", "title": "Views Today",
     "gridPos": {"h": 4, "w": 6, "x": 0, "y": 81}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM page_views WHERE created_at >= date_trunc('day', NOW())", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "yellow", "value": None}, {"color": "green", "value": 10}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    {"id": next_id + 2, "type": "stat", "title": "Views This Week",
     "gridPos": {"h": 4, "w": 6, "x": 6, "y": 81}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM page_views WHERE created_at >= NOW() - INTERVAL '7 days'", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "yellow", "value": None}, {"color": "green", "value": 50}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    {"id": next_id + 3, "type": "stat", "title": "Unique Referrers",
     "gridPos": {"h": 4, "w": 6, "x": 12, "y": 81}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(DISTINCT referrer) FROM page_views WHERE referrer != '' AND created_at >= NOW() - INTERVAL '7 days'", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "blue", "value": None}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    {"id": next_id + 4, "type": "stat", "title": "Unique Slugs Viewed",
     "gridPos": {"h": 4, "w": 6, "x": 18, "y": 81}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(DISTINCT slug) FROM page_views WHERE slug != '' AND created_at >= NOW() - INTERVAL '7 days'", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "purple", "value": None}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    {"id": next_id + 5, "type": "timeseries", "title": "Page Views Over Time",
     "gridPos": {"h": 8, "w": 12, "x": 0, "y": 85}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT date_trunc('hour', created_at) as time, COUNT(*) as views FROM page_views WHERE created_at > NOW() - INTERVAL '7 days' GROUP BY 1 ORDER BY 1", "format": "time_series", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "cyan"}, "custom": {"drawStyle": "bars", "fillOpacity": 80}}},
     "options": {"legend": {"displayMode": "hidden"}}},

    {"id": next_id + 6, "type": "table", "title": "Top Posts by Views",
     "gridPos": {"h": 8, "w": 12, "x": 12, "y": 85}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT LEFT(p.title, 50) as title, p.view_count as views, p.slug FROM posts p WHERE p.status = 'published' ORDER BY COALESCE(p.view_count, 0) DESC LIMIT 15", "format": "table", "refId": "A"}],
     "options": {"showHeader": True},
     "fieldConfig": {"overrides": [{"matcher": {"id": "byName", "options": "slug"}, "properties": [
         {"id": "links", "value": [{"title": "View", "url": "https://gladlabs.io/posts/${__data.fields.slug}", "targetBlank": True}]}]}]}},

    # ========== GITHUB SECTION ==========
    {"id": next_id + 7, "type": "row", "title": "GitHub & Issues",
     "gridPos": {"h": 1, "w": 24, "x": 0, "y": 93}, "collapsed": False},

    {"id": next_id + 8, "type": "stat", "title": "Total Content Tasks",
     "gridPos": {"h": 4, "w": 6, "x": 0, "y": 94}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM content_tasks", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "blue", "value": None}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    {"id": next_id + 9, "type": "stat", "title": "Avg Quality Score",
     "gridPos": {"h": 4, "w": 6, "x": 6, "y": 94}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT ROUND(AVG(quality_score)::numeric, 1) FROM content_tasks WHERE quality_score IS NOT NULL", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "red", "value": None}, {"color": "yellow", "value": 65}, {"color": "green", "value": 75}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    {"id": next_id + 10, "type": "stat", "title": "Categories",
     "gridPos": {"h": 4, "w": 6, "x": 12, "y": 94}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM categories", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "orange", "value": None}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    {"id": next_id + 11, "type": "stat", "title": "Newsletter Subs",
     "gridPos": {"h": 4, "w": 6, "x": 18, "y": 94}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT COUNT(*) FROM newsletter_subscribers WHERE is_active = true", "format": "table", "refId": "A"}],
     "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "yellow", "value": None}, {"color": "green", "value": 10}]}}},
     "options": {"colorMode": "background", "graphMode": "none", "textMode": "value"}},

    # ========== SETTINGS/CONFIG SECTION ==========
    {"id": next_id + 12, "type": "table", "title": "App Settings Overview",
     "gridPos": {"h": 8, "w": 24, "x": 0, "y": 98}, "datasource": DS_PG,
     "targets": [{"rawSql": "SELECT category, key, CASE WHEN is_secret THEN '********' ELSE value END as value FROM app_settings ORDER BY category, key", "format": "table", "refId": "A"}],
     "options": {"showHeader": True, "footer": {"show": True, "countRows": True}}},
]

dashboard["panels"].extend(new_panels)

payload = json.dumps({"dashboard": dashboard, "overwrite": True, "folderId": 0}).encode()
req = urllib.request.Request(f"{URL}/api/dashboards/db", data=payload,
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
print(f"Updated: {URL}{json.loads(resp.read()).get('url', '')}")
