"""
Worker Service — manages worker registration, heartbeat, and capability announcement.

A worker is a compute node (local PC, cloud GPU, etc.) that can process tasks.
Workers register in the capability_registry table, send periodic heartbeats
with health metrics, and claim tasks atomically.
"""

import asyncio
import json
import os
import platform
import socket
from datetime import datetime, timezone
from typing import Any

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
WORKER_TIMEOUT = 120  # seconds — worker considered offline after this


class WorkerService:
    def __init__(self, pool, worker_type: str = "local"):
        self.pool = pool
        self.worker_type = worker_type
        self.worker_id = f"{worker_type}-{socket.gethostname()}-{os.getpid()}"
        self._running = False
        self._capabilities = {}
        self._current_task_id = None
        # Strong ref to the heartbeat task so asyncio doesn't GC it.
        self._heartbeat_task: asyncio.Task | None = None

    @property
    def capabilities(self) -> dict[str, Any]:
        """Discover local capabilities."""
        if not self._capabilities:
            caps = {
                "hostname": socket.gethostname(),
                "platform": platform.system(),
                "python": platform.python_version(),
            }
            # Check for Ollama
            ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
            caps["ollama_url"] = ollama_url
            # Check for SDXL
            caps["sdxl"] = bool(site_config.get("sdxl_api_url"))
            # GPU info (basic — enhance later with nvidia-smi)
            caps["gpu"] = site_config.get("gpu_name", "unknown")
            caps["vram_gb"] = int(site_config.get("gpu_vram_gb", "0"))
            self._capabilities = caps
        return self._capabilities

    async def register(self):
        """Register this worker in the capability_registry."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO capability_registry (id, entity_type, name, capabilities, status, last_heartbeat)
                VALUES ($1, 'worker', $2, $3::jsonb, 'online', NOW())
                ON CONFLICT (id) DO UPDATE SET
                    status = 'online',
                    capabilities = $3::jsonb,
                    last_heartbeat = NOW(),
                    updated_at = NOW()
                """,
                self.worker_id,
                f"Worker {self.worker_id}",
                json.dumps(self.capabilities),
            )
        logger.info("[WORKER] Registered as %s", self.worker_id)

    async def start_heartbeat(self):
        """Start the heartbeat loop."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"heartbeat({self.worker_id})"
        )
        logger.info("[WORKER] Heartbeat started (every %ds)", HEARTBEAT_INTERVAL)

    async def stop(self):
        """Mark worker as offline."""
        self._running = False
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE capability_registry
                    SET status = 'offline', updated_at = NOW()
                    WHERE id = $1
                    """,
                    self.worker_id,
                )
            logger.info("[WORKER] Marked offline: %s", self.worker_id)
        except Exception:
            logger.debug("[WORKER] Failed to mark offline", exc_info=True)

    async def _heartbeat_loop(self):
        """Send periodic heartbeats with health metrics."""
        while self._running:
            try:
                health = await self._collect_health_metrics()
                async with self.pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE capability_registry
                        SET last_heartbeat = NOW(),
                            health = $2::jsonb,
                            status = CASE
                                WHEN $3 IS NOT NULL THEN 'busy'
                                ELSE 'online'
                            END,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        self.worker_id,
                        json.dumps(health),
                        self._current_task_id,
                    )
            except Exception:
                logger.debug("[WORKER] Heartbeat failed", exc_info=True)
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _collect_health_metrics(self) -> dict[str, Any]:
        """Collect current health metrics."""
        # Basic metrics — enhance with nvidia-smi for GPU util later
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_task": self._current_task_id,
        }

    def set_current_task(self, task_id: str | None):
        """Track which task is being processed."""
        self._current_task_id = task_id
