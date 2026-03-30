"""
Glad Labs MCP Server — exposes all system capabilities as MCP tools.

Connects Claude desktop app directly to the Glad Labs platform:
- Content pipeline (create, approve, publish, list tasks)
- Site monitoring (health checks, post counts)
- Cost management (budget status)
- Settings management (read/write app_settings)
- System status (worker, OpenClaw, GPU)

Usage:
    python mcp-server/server.py
    # Or via uvx in Claude desktop config
"""

import json
import logging
import os
import subprocess
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gladlabs-mcp")

API_URL = os.getenv("GLADLABS_API_URL", "https://cofounder-production.up.railway.app")
API_TOKEN = os.getenv("GLADLABS_API_TOKEN", "REDACTED_API_TOKEN")

mcp = FastMCP("Glad Labs", instructions="""
Glad Labs MCP server — your direct interface to the AI content pipeline.
Use these tools to manage content, monitor the system, and control operations.
""")


def _api(method: str, path: str, data: dict | None = None) -> dict:
    """Call the Glad Labs API."""
    url = f"{API_URL}{path}"
    headers = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)[:200]}


# ============================================================================
# CONTENT PIPELINE TOOLS
# ============================================================================

@mcp.tool()
def create_post(topic: str, category: str = "technology", target_audience: str = "developers and founders") -> str:
    """Create a new blog post task in the content pipeline. The worker will generate it via AI."""
    result = _api("POST", "/api/tasks", {
        "task_name": f"Blog post: {topic}",
        "topic": topic,
        "category": category,
        "target_audience": target_audience,
    })
    if "error" in result:
        return f"Failed: {result['error']}"
    return f"Task created: {result.get('task_id', '?')} — status: {result.get('status', '?')}"


@mcp.tool()
def list_tasks(status: str = "all", limit: int = 10) -> str:
    """List content tasks with their status and quality scores."""
    path = f"/api/tasks?limit={limit}"
    if status != "all":
        path += f"&status={status}"
    result = _api("GET", path)
    if "error" in result:
        return f"Error: {result['error']}"
    tasks = result.get("tasks", [])
    if not tasks:
        return "No tasks found."
    lines = [f"Tasks ({len(tasks)}):"]
    for t in tasks:
        lines.append(f"  {t.get('status', '?'):20s} Q:{str(t.get('quality_score', '-')):5s} {t.get('topic', '?')[:55]}")
    return "\n".join(lines)


@mcp.tool()
def approve_post(task_id: str) -> str:
    """Approve a content task for publishing."""
    result = _api("POST", f"/api/tasks/{task_id}/approve")
    return f"Status: {result.get('status', result.get('error', '?'))}"


@mcp.tool()
def publish_post(task_id: str) -> str:
    """Publish an approved content task to gladlabs.io."""
    result = _api("POST", f"/api/tasks/{task_id}/publish")
    return f"Status: {result.get('status', result.get('error', '?'))}"


@mcp.tool()
def get_post_count() -> str:
    """Get the total number of published posts on gladlabs.io."""
    result = _api("GET", "/api/posts?limit=1")
    return f"Published posts: {result.get('total', result.get('error', '?'))}"


# ============================================================================
# MONITORING TOOLS
# ============================================================================

@mcp.tool()
def check_health() -> str:
    """Check the health of all Glad Labs systems (site, API, worker, OpenClaw)."""
    checks = []

    # Site
    try:
        req = urllib.request.Request("https://gladlabs.io")
        resp = urllib.request.urlopen(req, timeout=10)
        checks.append(f"Site: {resp.status} OK")
    except Exception as e:
        checks.append(f"Site: DOWN ({e})")

    # API
    result = _api("GET", "/api/health")
    checks.append(f"API: {result.get('status', result.get('error', '?'))}")

    # Posts
    result = _api("GET", "/api/posts?limit=1")
    checks.append(f"Posts: {result.get('total', '?')}")

    # Worker
    try:
        resp = urllib.request.urlopen("http://localhost:8002/api/health", timeout=5)
        data = json.loads(resp.read())
        te = data.get("components", {}).get("task_executor", {})
        checks.append(f"Worker: running={te.get('running')}, processed={te.get('total_processed')}")
    except Exception:
        checks.append("Worker: offline")

    # OpenClaw
    try:
        urllib.request.urlopen("http://localhost:18789/status", timeout=5)
        checks.append("OpenClaw: running")
    except Exception:
        checks.append("OpenClaw: offline")

    # GPU
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        vals = r.stdout.strip().split(", ")
        if len(vals) >= 4:
            checks.append(f"GPU: {vals[0]}% util, {vals[1]}C, {vals[2]}/{vals[3]} MiB VRAM")
    except Exception:
        checks.append("GPU: unavailable")

    return "\n".join(checks)


@mcp.tool()
def get_budget() -> str:
    """Get current AI spending status (daily and monthly)."""
    result = _api("GET", "/api/metrics/costs/budget")
    if "error" in result:
        return f"Error: {result['error']}"
    return json.dumps(result, indent=2)


# ============================================================================
# SETTINGS TOOLS
# ============================================================================

@mcp.tool()
def get_setting(key: str) -> str:
    """Get a configuration setting value from the database."""
    result = _api("GET", f"/api/settings/{key}")
    if "error" in result:
        return f"Error: {result['error']}"
    return f"{key} = {result.get('value', '?')} (category: {result.get('category', '?')})"


@mcp.tool()
def set_setting(key: str, value: str) -> str:
    """Update a configuration setting in the database."""
    result = _api("PUT", f"/api/settings/{key}", {"value": value})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Updated: {key} = {value}"


@mcp.tool()
def list_settings(category: str = "") -> str:
    """List all configuration settings, optionally filtered by category."""
    path = "/api/settings"
    if category:
        path += f"?category={category}"
    result = _api("GET", path)
    if "error" in result:
        return f"Error: {result['error']}"
    settings = result.get("settings", result.get("data", []))
    if not settings:
        return "No settings found."
    lines = [f"Settings ({len(settings)}):"]
    for s in settings:
        val = s.get("value", "")
        if s.get("is_secret") and val:
            val = "********"
        lines.append(f"  {s.get('category', '?')}/{s.get('key', '?')} = {val}")
    return "\n".join(lines)


# ============================================================================
# COMPOSE TOOLS
# ============================================================================

@mcp.tool()
def compose_plan(intent: str) -> str:
    """Create a process execution plan from natural language intent. Returns a plan for review before execution."""
    result = _api("POST", "/api/compose/plan", {"intent": intent})
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("summary", json.dumps(result, indent=2))


@mcp.tool()
def compose_execute(intent: str) -> str:
    """Execute a business process immediately from natural language intent."""
    result = _api("POST", "/api/compose/execute", {"intent": intent})
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("summary", json.dumps(result, indent=2))


if __name__ == "__main__":
    mcp.run(transport="stdio")
