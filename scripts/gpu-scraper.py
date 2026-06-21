"""
GPU Metrics Scraper — reads nvidia-smi exporter and stores in local Postgres.

Runs every 60 seconds, storing GPU utilization, temperature, power, VRAM,
fan speed, and clock speeds for Grafana dashboards.

Usage:
    pythonw scripts/gpu-scraper.py     # windowless background
    python scripts/gpu-scraper.py      # interactive
"""

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import asyncpg
import httpx

# Force IPv4 (127.0.0.1, not "localhost"): on Windows "localhost" resolves to
# ::1 first, so when the gpu-exporter container publishes :9835 on IPv6 its
# Docker proxy answers ::1 and drops the connection ("Server disconnected"),
# starving this scraper. 127.0.0.1 always lands on the host-side exporter.
EXPORTER_URL = os.getenv("GPU_EXPORTER_URL", "http://127.0.0.1:9835/metrics")
INTERVAL = 60  # seconds

LOG_DIR = Path.home() / ".poindexter"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "gpu-scraper.log"),
        *([] if sys.stdout is None else [logging.StreamHandler(sys.stdout)]),
    ],
)
logger = logging.getLogger("gpu-scraper")


def _resolve_db_url() -> str:
    """Resolve the brain DB DSN — bootstrap.toml is canonical (#198).

    Order: ``DATABASE_URL`` env → ``brain.bootstrap.resolve_database_url()``
    (CLI arg → bootstrap.toml → DATABASE_URL → LOCAL_DATABASE_URL → …) → a
    local default. The previous hardcoded ``localhost:15432`` froze
    ``gpu_metrics`` silently when the 2026-06-21 deploy cutover moved the
    Postgres host port to 5433 (15432 was Windows-reserved); resolving from
    bootstrap tracks the port so the writer can't drift off it again.

    The host is forced to IPv4 (127.0.0.1) for the same reason as the
    exporter URL: on Windows "localhost" resolves to ::1 first, and the
    Docker Desktop IPv6 port-proxy is unreliable — it silently drops
    connections (directly observed on :9835).
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        try:
            from brain.bootstrap import resolve_database_url  # type: ignore

            dsn = resolve_database_url()
        except Exception as exc:  # pragma: no cover - host bootstrap best-effort
            logger.warning("bootstrap DSN resolution failed (%s); using default", exc)
    if not dsn:
        dsn = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
    return dsn.replace("@localhost:", "@127.0.0.1:")


LOCAL_DB = _resolve_db_url()


def parse_metric(text, name):
    m = re.search(rf'{name}\{{.*?\}} ([\d.]+)', text)
    return float(m.group(1)) if m else None


async def scrape_and_store():
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(EXPORTER_URL)
        text = resp.text

    vals = {
        "utilization": parse_metric(text, "nvidia_gpu_utilization_percent"),
        "temperature": parse_metric(text, "nvidia_gpu_temperature_celsius"),
        "power_draw": parse_metric(text, "nvidia_gpu_power_draw_watts"),
        "memory_used": parse_metric(text, "nvidia_gpu_memory_used_mib"),
        "memory_total": parse_metric(text, "nvidia_gpu_memory_total_mib"),
        "fan_speed": parse_metric(text, "nvidia_gpu_fan_speed_percent"),
        "clock_graphics": parse_metric(text, "nvidia_gpu_clock_graphics_mhz"),
        "clock_memory": parse_metric(text, "nvidia_gpu_clock_memory_mhz"),
    }

    conn = await asyncpg.connect(LOCAL_DB)
    try:
        await conn.execute(
            """INSERT INTO gpu_metrics (utilization, temperature, power_draw,
               memory_used, memory_total, fan_speed, clock_graphics, clock_memory)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            vals["utilization"], vals["temperature"], vals["power_draw"],
            vals["memory_used"], vals["memory_total"], vals["fan_speed"],
            vals["clock_graphics"], vals["clock_memory"],
        )
    finally:
        await conn.close()
    return vals


async def main():
    logger.info("GPU scraper started (interval: %ds)", INTERVAL)
    while True:
        try:
            vals = await scrape_and_store()
            logger.debug("Scraped: util=%.0f%% temp=%.0f°C power=%.0fW vram=%.0f/%.0fMiB",
                         vals["utilization"] or 0, vals["temperature"] or 0,
                         vals["power_draw"] or 0, vals["memory_used"] or 0,
                         vals["memory_total"] or 0)
        except Exception as e:
            logger.warning("Scrape failed: %s", e)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("GPU scraper stopped")
