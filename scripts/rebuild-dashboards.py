#!/usr/bin/env python3
"""Rebuild ALL Grafana dashboards — dense tables, bar gauges, stats. No flaky charts."""
import json, subprocess, os
os.chdir(os.path.join(os.path.dirname(__file__), "..", "infrastructure", "grafana", "dashboards"))

L = {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"}
R = {"type": "grafana-postgresql-datasource", "uid": "local-postgres"}

def mk(uid, title, desc, panels):
    return {"uid": uid, "title": title, "description": desc, "tags": ["glad-labs"],
            "editable": True, "schemaVersion": 39, "refresh": "30s",
            "time": {"from": "now-30d", "to": "now"}, "timezone": "browser", "panels": panels}

def s(i, t, q, d, x, y, w=4, h=3, c="blue"):
    return {"id":i,"type":"stat","title":t,"datasource":d,"gridPos":{"h":h,"w":w,"x":x,"y":y},
        "fieldConfig":{"defaults":{"color":{"mode":"fixed","fixedColor":c}},"overrides":[]},
        "options":{"colorMode":"background","graphMode":"none","reduceOptions":{"calcs":["lastNotNull"],"fields":"","values":False},"textMode":"value_and_name","text":{"valueSize":28}},
        "targets":[{"datasource":d,"format":"table","rawQuery":True,"rawSql":q,"refId":"A"}]}

def t(i, t, q, d, x, y, w=12, h=8):
    return {"id":i,"type":"table","title":t,"datasource":d,"gridPos":{"h":h,"w":w,"x":x,"y":y},
        "fieldConfig":{"defaults":{"custom":{"align":"auto","displayMode":"auto"}},"overrides":[]},
        "options":{"showHeader":True,"cellHeight":"sm"},
        "targets":[{"datasource":d,"format":"table","rawQuery":True,"rawSql":q,"refId":"A"}]}

def b(i, t, q, d, x, y, w=8, h=5):
    return {"id":i,"type":"bargauge","title":t,"datasource":d,"gridPos":{"h":h,"w":w,"x":x,"y":y},
        "fieldConfig":{"defaults":{"color":{"mode":"palette-classic"},"thresholds":{"mode":"absolute","steps":[{"color":"green","value":None}]}},"overrides":[]},
        "options":{"reduceOptions":{"calcs":["lastNotNull"],"fields":"","values":True},"orientation":"horizontal","displayMode":"gradient","showUnfilled":True},
        "targets":[{"datasource":d,"format":"table","rawQuery":True,"rawSql":q,"refId":"A"}]}

def g(i, t, q, d, x, y, w=6, h=5, mx=100, u="percent"):
    return {"id":i,"type":"gauge","title":t,"datasource":d,"gridPos":{"h":h,"w":w,"x":x,"y":y},
        "fieldConfig":{"defaults":{"min":0,"max":mx,"unit":u,"thresholds":{"mode":"absolute","steps":[{"color":"green","value":None},{"color":"yellow","value":mx*0.6},{"color":"red","value":mx*0.85}]}},"overrides":[]},
        "options":{"reduceOptions":{"calcs":["lastNotNull"],"fields":"","values":False}},
        "targets":[{"datasource":d,"format":"table","rawQuery":True,"rawSql":q,"refId":"A"}]}

dashboards = {}

# COMMAND CENTER
dashboards["command-center"] = mk("command-center","Glad Labs Command Center","Home",[
    s(1,"Posts","SELECT COUNT(*) as published FROM posts WHERE status='published'",R,0,0,6,3,"green"),
    s(2,"Tasks Today","SELECT COUNT(*) as today FROM content_tasks WHERE created_at>date_trunc('day',NOW())",R,6,0,6,3,"yellow"),
    s(3,"Quality","SELECT ROUND(AVG(overall_score)::numeric,1) as avg FROM quality_evaluations WHERE evaluation_timestamp>NOW()-INTERVAL '7 days'",R,12,0,6,3,"green"),
    s(4,"Spend $","SELECT ROUND(COALESCE(SUM(cost_usd),0)::numeric,4) as usd FROM cost_logs WHERE created_at>date_trunc('day',NOW())",R,18,0,6,3,"blue"),
    b(5,"Tasks by Status","SELECT status as metric, COUNT(*) as value FROM content_tasks GROUP BY 1 ORDER BY 2 DESC",R,0,3,12,5),
    b(6,"Posts by Category","SELECT COALESCE(c.name,'none') as metric, COUNT(p.id) as value FROM categories c LEFT JOIN posts p ON c.id=p.category_id AND p.status='published' GROUP BY 1 ORDER BY 2 DESC",R,12,3,12,5),
    t(7,"Recent Tasks","SELECT LEFT(topic,50) as topic, status, quality_score as q, created_at::date FROM content_tasks ORDER BY created_at DESC LIMIT 12",R,0,8,12,8),
    t(8,"Cost Summary","SELECT provider, COUNT(*) as calls, ROUND(SUM(cost_usd)::numeric,4) as cost, SUM(input_tokens+output_tokens) as tokens FROM cost_logs GROUP BY 1 ORDER BY 3 DESC",R,12,8,12,8),
    t(9,"Published Posts","SELECT LEFT(p.title,50) as title, COALESCE(c.name,'?') as cat, p.view_count as views, p.published_at::date FROM posts p LEFT JOIN categories c ON p.category_id=c.id WHERE p.status='published' ORDER BY p.published_at DESC LIMIT 12",R,0,16,24,8),
])

# PIPELINE
dashboards["pipeline-overview"] = mk("pipeline-overview","Content Pipeline Overview","Tasks, stages",[
    s(1,"Published","SELECT COUNT(*) FROM content_tasks WHERE status='published'",R,0,0,4,3,"green"),
    s(2,"Failed","SELECT COUNT(*) FROM content_tasks WHERE status='failed'",R,4,0,4,3,"red"),
    s(3,"Pending","SELECT COUNT(*) FROM content_tasks WHERE status IN ('pending','approved','awaiting_approval')",R,8,0,4,3,"yellow"),
    s(4,"Rejected","SELECT COUNT(*) FROM content_tasks WHERE status='rejected'",R,12,0,4,3,"orange"),
    s(5,"Success %","SELECT ROUND(COUNT(*) FILTER (WHERE status='published')*100.0/NULLIF(COUNT(*),0),1) FROM content_tasks WHERE created_at>NOW()-INTERVAL '30 days'",R,16,0,4,3,"green"),
    s(6,"Avg Hours","SELECT ROUND(AVG(EXTRACT(EPOCH FROM (updated_at-created_at))/3600)::numeric,1) FROM content_tasks WHERE status='published' AND created_at>NOW()-INTERVAL '7 days'",R,20,0,4,3,"blue"),
    b(7,"By Status","SELECT status as metric, COUNT(*) as value FROM content_tasks GROUP BY 1 ORDER BY 2 DESC",R,0,3,12,5),
    t(8,"Daily Production","SELECT date_trunc('day',created_at)::date as day, COUNT(*) as tasks, SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) as pub, SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail FROM content_tasks GROUP BY 1 ORDER BY 1 DESC LIMIT 14",R,12,3,12,5),
    t(9,"Pipeline Stages","SELECT stage_order as ord, key, name, enabled, config_json->>'model' as model, config_json->>'role' as role FROM pipeline_stages ORDER BY stage_order",R,0,8,24,8),
    t(10,"Recent Failures","SELECT LEFT(topic,45) as topic, quality_score as q, status, created_at::date FROM content_tasks WHERE status IN ('failed','rejected') ORDER BY created_at DESC LIMIT 10",R,0,16,24,8),
])

# COST
dashboards["cost-control"] = mk("cost-control","Cost Control","Spending, tokens",[
    s(1,"Today","SELECT ROUND(COALESCE(SUM(cost_usd),0)::numeric,4) FROM cost_logs WHERE created_at>=date_trunc('day',NOW())",R,0,0,6,3,"green"),
    s(2,"7-Day","SELECT ROUND(COALESCE(SUM(cost_usd),0)::numeric,4) FROM cost_logs WHERE created_at>NOW()-INTERVAL '7 days'",R,6,0,6,3,"blue"),
    s(3,"30-Day","SELECT ROUND(COALESCE(SUM(cost_usd),0)::numeric,4) FROM cost_logs WHERE created_at>NOW()-INTERVAL '30 days'",R,12,0,6,3,"yellow"),
    s(4,"Tokens","SELECT SUM(input_tokens+output_tokens) FROM cost_logs",R,18,0,6,3,"purple"),
    b(5,"By Provider","SELECT provider as metric, ROUND(SUM(cost_usd)::numeric,4) as value FROM cost_logs GROUP BY 1 ORDER BY 2 DESC",R,0,3,12,5),
    b(6,"By Phase","SELECT COALESCE(phase,'?') as metric, ROUND(SUM(cost_usd)::numeric,4) as value FROM cost_logs GROUP BY 1 ORDER BY 2 DESC",R,12,3,12,5),
    t(7,"Daily","SELECT date_trunc('day',created_at)::date as day, provider, COUNT(*) as calls, ROUND(SUM(cost_usd)::numeric,6) as cost, SUM(input_tokens+output_tokens) as tokens FROM cost_logs GROUP BY 1,2 ORDER BY 1 DESC LIMIT 20",R,0,8,24,8),
    t(8,"By Model","SELECT model, COUNT(*) as calls, ROUND(SUM(cost_usd)::numeric,6) as cost, SUM(input_tokens) as in_tok, SUM(output_tokens) as out_tok FROM cost_logs GROUP BY 1 ORDER BY 3 DESC",R,0,16,24,8),
])

# QUALITY
dashboards["quality-metrics"] = mk("quality-metrics","Quality Metrics","Scores",[
    s(1,"Avg","SELECT ROUND(AVG(overall_score)::numeric,1) FROM quality_evaluations WHERE evaluation_timestamp>NOW()-INTERVAL '30 days'",R,0,0,6,3,"green"),
    s(2,"Pass %","SELECT ROUND(COUNT(*) FILTER (WHERE overall_score>=70)*100.0/NULLIF(COUNT(*),0),1) FROM quality_evaluations WHERE evaluation_timestamp>NOW()-INTERVAL '30 days'",R,6,0,6,3,"green"),
    s(3,"Evals","SELECT COUNT(*) FROM quality_evaluations",R,12,0,6,3,"blue"),
    s(4,"Rejected","SELECT COUNT(*) FROM content_tasks WHERE status='rejected'",R,18,0,6,3,"red"),
    t(5,"Grades","SELECT CASE WHEN overall_score>=80 THEN 'A (80+)' WHEN overall_score>=70 THEN 'B (70-79)' WHEN overall_score>=60 THEN 'C (60-69)' ELSE 'D (<60)' END as grade, COUNT(*) as count, ROUND(AVG(overall_score)::numeric,1) as avg FROM quality_evaluations GROUP BY 1 ORDER BY 1",R,0,3,12,5),
    t(6,"Daily","SELECT date_trunc('day',evaluation_timestamp)::date as day, COUNT(*) as evals, ROUND(AVG(overall_score)::numeric,1) as avg, MIN(overall_score) as min, MAX(overall_score) as max FROM quality_evaluations GROUP BY 1 ORDER BY 1 DESC LIMIT 14",R,12,3,12,5),
    t(7,"Recent","SELECT LEFT(ct.topic,45) as topic, ct.quality_score as score, ct.status, ct.created_at::date FROM content_tasks ct WHERE ct.quality_score IS NOT NULL ORDER BY ct.created_at DESC LIMIT 15",R,0,8,24,8),
])

# GPU
dashboards["gpu-metrics"] = mk("gpu-metrics","GPU Metrics","RTX 5090",[
    g(1,"Util %","SELECT utilization FROM gpu_metrics ORDER BY \"timestamp\" DESC LIMIT 1",L,0,0,6,5,100,"percent"),
    g(2,"Temp","SELECT temperature FROM gpu_metrics ORDER BY \"timestamp\" DESC LIMIT 1",L,6,0,6,5,100,"celsius"),
    g(3,"Power","SELECT power_draw FROM gpu_metrics ORDER BY \"timestamp\" DESC LIMIT 1",L,12,0,6,5,600,"watt"),
    g(4,"VRAM %","SELECT ROUND(((memory_used/NULLIF(memory_total,0))*100)::numeric,1) FROM gpu_metrics ORDER BY \"timestamp\" DESC LIMIT 1",L,18,0,6,5,100,"percent"),
    t(5,"Recent","SELECT to_char(\"timestamp\",'HH24:MI') as time, utilization as util, temperature as temp, ROUND(power_draw::numeric) as watts, memory_used as vram, fan_speed as fan, clock_graphics as clock FROM gpu_metrics WHERE \"timestamp\">NOW()-INTERVAL '2 hours' ORDER BY \"timestamp\" DESC LIMIT 20",L,0,5,24,10),
    t(6,"Daily Avg","SELECT date_trunc('day',\"timestamp\")::date as day, ROUND(AVG(utilization)::numeric,1) as util, ROUND(AVG(temperature)::numeric,1) as temp, ROUND(AVG(power_draw)::numeric) as watts, COUNT(*) as samples FROM gpu_metrics GROUP BY 1 ORDER BY 1 DESC LIMIT 7",L,0,15,24,7),
])

# PAGE VIEWS
dashboards["page-views"] = mk("page-views","Page Views & Traffic","Views, referrers",[
    s(1,"Views","SELECT COALESCE(SUM(view_count),0) FROM posts WHERE status='published'",R,0,0,6,3,"green"),
    s(2,"With Views","SELECT COUNT(*) FROM posts WHERE status='published' AND view_count>0",R,6,0,6,3,"blue"),
    s(3,"Subscribers","SELECT COUNT(*) FROM newsletter_subscribers WHERE unsubscribed_at IS NULL",R,12,0,6,3,"yellow"),
    s(4,"Affiliates","SELECT COUNT(*) FROM affiliate_links WHERE is_active=true",R,18,0,6,3,"purple"),
    t(5,"Top Posts","SELECT LEFT(p.title,50) as title, p.view_count as views, COALESCE(c.name,'?') as cat FROM posts p LEFT JOIN categories c ON p.category_id=c.id WHERE p.status='published' AND p.view_count>0 ORDER BY p.view_count DESC LIMIT 15",R,0,3,12,8),
    t(6,"Categories","SELECT COALESCE(c.name,'none') as cat, COUNT(p.id) as posts, SUM(COALESCE(p.view_count,0)) as views FROM posts p LEFT JOIN categories c ON p.category_id=c.id WHERE p.status='published' GROUP BY 1 ORDER BY 3 DESC",R,12,3,12,8),
    t(7,"Raw Views","SELECT path, slug, referrer, created_at FROM page_views ORDER BY created_at DESC LIMIT 15",R,0,11,24,8),
])

# SYSTEM SETTINGS
dashboards["system-settings"] = mk("system-settings","System Settings","Config, prompts, permissions",[
    s(1,"Settings","SELECT COUNT(*) FROM app_settings",R,0,0,4,3,"purple"),
    s(2,"Prompts","SELECT COUNT(*) FROM prompt_templates WHERE is_active=true",R,4,0,4,3,"blue"),
    s(3,"Stages","SELECT COUNT(*) FROM pipeline_stages WHERE enabled=true",R,8,0,4,3,"green"),
    s(4,"Agents","SELECT COUNT(*) FROM system_agents WHERE is_active=true",R,12,0,4,3,"yellow"),
    s(5,"Perms","SELECT COUNT(*) FROM agent_permissions",R,16,0,4,3,"orange"),
    s(6,"Alerts","SELECT COUNT(*) FROM alert_actions WHERE enabled=true",R,20,0,4,3,"red"),
    t(7,"Settings","SELECT key, value, category FROM app_settings WHERE is_secret=false ORDER BY category, key",R,0,3,12,8),
    t(8,"Prompts","SELECT key, category, LENGTH(template) as chars, version FROM prompt_templates ORDER BY category, key",R,12,3,12,8),
    t(9,"Permissions","SELECT agent_name, resource, action, CASE WHEN allowed AND NOT requires_approval THEN 'ALLOW' WHEN requires_approval THEN 'APPROVAL' ELSE 'DENY' END as access FROM agent_permissions ORDER BY agent_name, resource",R,0,11,12,8),
    t(10,"Agents","SELECT name, agent_type, trust_level, is_active FROM system_agents ORDER BY trust_level DESC",R,12,11,12,8),
])

# Write and push all
total = 0
for name, dash in dashboards.items():
    with open(f"{name}.json", "w") as f:
        json.dump(dash, f, indent=2)
    r = subprocess.run(["curl","-s","-X","POST","http://admin:gladlabs@localhost:3000/api/dashboards/db",
        "-H","Content-Type: application/json","-d",json.dumps({"dashboard":dash,"overwrite":True})],
        capture_output=True, text=True, timeout=10)
    st = json.loads(r.stdout).get("status","?") if r.stdout else "err"
    n = len(dash["panels"])
    total += n
    print(f"{name}: {n} panels -> {st}")

print(f"\nTotal: {total} panels across {len(dashboards)} dashboards")
