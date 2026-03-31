"""
Dashboard Composer — dynamically reshapes Grafana dashboards based on system state.

Reads cerebellum observations from brain_knowledge and uses the Grafana API
to adjust panel sizes, order, and visibility. Important metrics get bigger
and brighter. Stable metrics shrink. Anomalies surface to the top.

The dashboard becomes a passive communication channel — showing Matt what
the system thinks matters, without sending a single notification.

Usage:
    python scripts/dashboard_composer.py --once    # Run one cycle
    python scripts/dashboard_composer.py           # Run continuously (15 min)

Runs as part of the cerebellum cycle or standalone.
"""

import asyncio
import json
import logging
import os
import sys
import time

import httpx

# pythonw.exe compatibility
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

LOG_FILE = os.path.join(os.path.expanduser("~"), ".gladlabs", "dashboard_composer.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("dashboard_composer")
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)
if sys.stdout is not None and getattr(sys.stdout, "name", "") != os.devnull:
    logger.addHandler(logging.StreamHandler(sys.stdout))

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("DATABASE_URL="):
                DATABASE_URL = _line.split("=", 1)[1].strip()

GRAFANA_URL = "https://gladlabs.grafana.net"
CYCLE_INTERVAL = 900  # 15 minutes


async def get_grafana_key(pool) -> str:
    """Read Grafana API key from app_settings."""
    row = await pool.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'grafana_api_key'"
    )
    return row["value"] if row else ""


async def get_cerebellum_signals(pool) -> dict:
    """Read latest cerebellum observations and derive dashboard signals.

    Returns a dict of signal_name → priority (0-10, higher = more important).
    """
    signals = {}

    rows = await pool.fetch("""
        SELECT entity, attribute, value, confidence
        FROM brain_knowledge
        WHERE source = 'cerebellum' AND updated_at > NOW() - INTERVAL '30 minutes'
    """)

    for row in rows:
        entity = row["entity"]
        value_str = row["value"]
        confidence = float(row["confidence"])

        # Parse JSON values
        try:
            parsed = json.loads(value_str)
            actual_value = parsed.get("value", value_str) if isinstance(parsed, dict) else value_str
            details = parsed.get("details", {}) if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            actual_value = value_str
            details = {}

        # Cost signals — spending anomalies get high priority
        if "trend.costs" in entity:
            if actual_value == "up":
                pct = abs(details.get("pct_change", 0))
                signals["costs"] = min(10, 5 + pct / 20)  # 50% increase → priority 7.5
            elif actual_value == "down":
                signals["costs"] = 2  # Low priority when costs are down

        # Content quality signals
        if "trend.content.quality_score" in entity:
            if actual_value == "down":
                signals["quality"] = 8  # Quality dropping is important
            elif actual_value == "up":
                signals["quality"] = 3

        if "trend.content.rejection_rate" in entity:
            rate = float(actual_value) if actual_value.replace(".", "").isdigit() else 0
            if rate > 10:
                signals["rejections"] = 9  # High rejection rate is urgent
            elif rate > 5:
                signals["rejections"] = 6

        # Traffic signals
        if "trend.traffic.daily_views" in entity:
            if actual_value == "up":
                signals["traffic"] = 7  # Traffic growth worth highlighting
            elif actual_value == "down":
                signals["traffic"] = 8  # Traffic drop is concerning

        # Infrastructure signals
        if "trend.infra.task_queue" in entity:
            if actual_value == "degraded":
                signals["infra"] = 9
            else:
                signals["infra"] = 2

        # Published total — always show but priority based on growth
        if "trend.content.published_total" in entity:
            signals["content_volume"] = 4

    return signals


def compose_dynamic_row(signals: dict) -> list:
    """Build a dynamic "What Needs Attention" row based on cerebellum signals.

    Returns a list of Grafana panel dicts. Panels are sized proportionally
    to their signal priority — important things are BIGGER.
    """
    panels = []
    panel_id = 100  # Start IDs high to avoid conflicts

    # Sort signals by priority (highest first)
    sorted_signals = sorted(signals.items(), key=lambda x: x[1], reverse=True)

    if not sorted_signals:
        # No signals — show a "all clear" panel
        panels.append({
            "id": panel_id, "title": "System Status", "type": "stat",
            "gridPos": {"h": 4, "w": 24, "x": 0, "y": 42},
            "datasource": {"type": "grafana-postgresql-datasource", "uid": "gladlabs-postgres"},
            "targets": [{"rawSql": "SELECT 'All systems nominal' AS \"Status\"", "format": "table", "refId": "A"}],
            "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "textMode": "auto"}
        })
        return panels

    # Build panels sized by priority
    x_pos = 0
    for signal_name, priority in sorted_signals[:4]:  # Max 4 panels in the row
        # Higher priority = wider panel (6-12 columns out of 24)
        width = max(6, min(12, int(priority * 1.2)))
        if x_pos + width > 24:
            width = 24 - x_pos
        if width <= 0:
            break

        panel = _build_signal_panel(panel_id, signal_name, priority, x_pos, width)
        if panel:
            panels.append(panel)
            x_pos += width
            panel_id += 1

    return panels


def _build_signal_panel(panel_id: int, signal: str, priority: float, x: int, w: int) -> dict:
    """Build a Grafana panel for a specific signal."""
    base = {
        "id": panel_id, "type": "stat",
        "gridPos": {"h": 5, "w": w, "x": x, "y": 42},
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "gladlabs-postgres"},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "textMode": "auto"},
    }

    # Color based on priority: high = red/yellow (needs attention), low = green/blue (fine)
    if priority >= 8:
        color_steps = [{"color": "red", "value": None}]
    elif priority >= 6:
        color_steps = [{"color": "yellow", "value": None}]
    elif priority >= 4:
        color_steps = [{"color": "blue", "value": None}]
    else:
        color_steps = [{"color": "green", "value": None}]

    base["fieldConfig"] = {"defaults": {"thresholds": {"mode": "absolute", "steps": color_steps}}, "overrides": []}

    if signal == "costs":
        base["title"] = "Cloud Spend Trend"
        base["targets"] = [{"rawSql": "SELECT value AS \"Trend\" FROM brain_knowledge WHERE entity = 'trend.costs.daily_spend' AND attribute = 'vs_7d_avg' LIMIT 1", "format": "table", "refId": "A"}]
    elif signal == "quality":
        base["title"] = "Quality Trend"
        base["targets"] = [{"rawSql": "SELECT value AS \"Trend\" FROM brain_knowledge WHERE entity = 'trend.content.quality_score' AND attribute = '7d_vs_prev_7d' LIMIT 1", "format": "table", "refId": "A"}]
    elif signal == "rejections":
        base["title"] = "Rejection Rate"
        base["targets"] = [{"rawSql": "SELECT value AS \"Rate\" FROM brain_knowledge WHERE entity = 'trend.content.rejection_rate' AND attribute = 'last_24h' LIMIT 1", "format": "table", "refId": "A"}]
    elif signal == "traffic":
        base["title"] = "Traffic Trend"
        base["targets"] = [{"rawSql": "SELECT value AS \"Trend\" FROM brain_knowledge WHERE entity = 'trend.traffic.daily_views' AND attribute = 'vs_7d_avg' LIMIT 1", "format": "table", "refId": "A"}]
    elif signal == "infra":
        base["title"] = "Infrastructure Health"
        base["targets"] = [{"rawSql": "SELECT value AS \"Status\" FROM brain_knowledge WHERE entity = 'trend.infra.task_queue' AND attribute = 'last_24h' LIMIT 1", "format": "table", "refId": "A"}]
    elif signal == "content_volume":
        base["title"] = "Content Volume"
        base["targets"] = [{"rawSql": "SELECT value AS \"Total\" FROM brain_knowledge WHERE entity = 'trend.content.published_total' AND attribute = 'count' LIMIT 1", "format": "table", "refId": "A"}]
    else:
        return None

    return base


async def update_dashboard(grafana_key: str, dynamic_panels: list):
    """Fetch the Command Center dashboard, update the dynamic row, and save."""
    headers = {"Authorization": f"Bearer {grafana_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        # Fetch current dashboard
        resp = await client.get(f"{GRAFANA_URL}/api/dashboards/uid/command-center", headers=headers)
        if resp.status_code != 200:
            logger.error("Failed to fetch dashboard: %d", resp.status_code)
            return False

        data = resp.json()
        dashboard = data["dashboard"]

        # Remove old dynamic panels (id >= 100)
        dashboard["panels"] = [p for p in dashboard["panels"] if p.get("id", 0) < 100]

        # Add new dynamic panels
        dashboard["panels"].extend(dynamic_panels)

        # Increment version
        dashboard["version"] = dashboard.get("version", 1) + 1

        # Save
        save_resp = await client.post(
            f"{GRAFANA_URL}/api/dashboards/db",
            headers=headers,
            json={"dashboard": dashboard, "overwrite": True, "message": "Dynamic update by cerebellum"},
        )

        if save_resp.status_code == 200:
            logger.info("Dashboard updated: %d dynamic panels", len(dynamic_panels))
            return True
        else:
            logger.error("Failed to save dashboard: %d %s", save_resp.status_code, save_resp.text[:200])
            return False


async def run_cycle(pool):
    """Run one compose cycle: read signals → build panels → update dashboard."""
    grafana_key = await get_grafana_key(pool)
    if not grafana_key:
        logger.warning("No Grafana API key in app_settings")
        return

    signals = await get_cerebellum_signals(pool)
    logger.info("Signals: %s", {k: round(v, 1) for k, v in signals.items()})

    dynamic_panels = compose_dynamic_row(signals)
    await update_dashboard(grafana_key, dynamic_panels)


async def main_async():
    """Main entry point."""
    import asyncpg

    if not DATABASE_URL:
        logger.error("No DATABASE_URL configured")
        return

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    logger.info("Dashboard composer connected")

    one_shot = "--once" in sys.argv

    while True:
        try:
            await run_cycle(pool)
        except Exception as e:
            logger.error("Compose cycle error: %s", e)

        for h in logger.handlers:
            h.flush()

        if one_shot:
            break

        await asyncio.sleep(CYCLE_INTERVAL)

    await pool.close()


if __name__ == "__main__":
    logger.info("Dashboard composer starting")
    asyncio.run(main_async())
