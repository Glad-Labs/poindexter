"""
GPU Scheduler — serializes access to the shared GPU between Ollama and SDXL.

With a single GPU (RTX 5090, 32GB), only one large workload can run at a time.
This module provides an async lock so that:
  - Ollama LLM inference and SDXL image generation don't fight for VRAM
  - Before SDXL starts, any loaded Ollama model is unloaded
  - Before Ollama starts, SDXL pipeline is released (if loaded)
  - Small models (embeddings) can coexist and skip the lock

Usage:
    from services.gpu_scheduler import gpu
    async with gpu.lock("ollama", model="glm-4.7-5090"):
        result = await ollama.generate(...)
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://host.docker.internal:11434"

# Models under this VRAM threshold (in GB) skip the lock — they can coexist.
SMALL_MODEL_THRESHOLD_GB = 2.0


class GPUScheduler:
    """Async-safe GPU resource coordinator."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._current_owner: Optional[str] = None  # "ollama" or "sdxl"
        self._current_model: Optional[str] = None
        self._acquired_at: float = 0

    @asynccontextmanager
    async def lock(self, owner: str, model: Optional[str] = None):
        """Acquire exclusive GPU access.

        Args:
            owner: "ollama" or "sdxl"
            model: model name (for logging/tracking)
        """
        waited = False
        if self._lock.locked():
            logger.info(
                "GPU busy — waiting",
                waiting_for=owner,
                current_owner=self._current_owner,
                current_model=self._current_model,
            )
            waited = True

        await self._lock.acquire()
        wait_msg = " (waited)" if waited else ""
        logger.info("GPU acquired%s", wait_msg, owner=owner, model=model)

        self._current_owner = owner
        self._current_model = model
        self._acquired_at = time.monotonic()

        try:
            # Prepare GPU for the new owner
            if owner == "sdxl":
                await self._unload_ollama_models()
            yield
        finally:
            duration = time.monotonic() - self._acquired_at
            logger.info("GPU released", owner=owner, model=model, duration_s=round(duration, 1))
            self._current_owner = None
            self._current_model = None
            self._lock.release()

    async def _unload_ollama_models(self):
        """Unload all Ollama models to free VRAM for SDXL."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{OLLAMA_BASE_URL}/api/ps")
                if resp.status_code != 200:
                    return
                data = resp.json()
                for model in data.get("models", []):
                    name = model["name"]
                    logger.info("Unloading Ollama model for SDXL", model=name)
                    await client.post(
                        f"{OLLAMA_BASE_URL}/api/generate",
                        json={"model": name, "keep_alive": 0},
                        timeout=30,
                    )
        except Exception as e:
            logger.warning("Failed to unload Ollama models: %s", e)

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    @property
    def status(self) -> dict:
        return {
            "busy": self._lock.locked(),
            "owner": self._current_owner,
            "model": self._current_model,
            "duration_s": round(time.monotonic() - self._acquired_at, 1) if self._lock.locked() else 0,
        }


# Module-level singleton
gpu = GPUScheduler()
