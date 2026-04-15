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

EXPORTER_URL = "http://localhost:9835/metrics"
LOCAL_DB = "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
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
