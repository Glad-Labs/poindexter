"""Sample Probe implementation — basic Postgres connectivity check.

Real migration of the equivalent function from ``brain/health_probes.py``
happens in Phase D. This sample exists in Phase A to prove the Probe
Protocol + entry_points + registry + PluginConfig flow works with a
real Postgres connection.

Config (``plugin.probe.database`` in ``app_settings``):
- ``interval_seconds`` (default 300) — how often to run
- ``timeout_ms`` (default 5000) — query timeout before probe fails
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from plugins.probe import CATEGORY_INFRASTRUCTURE, ProbeResult


class DatabaseProbe:
    """Health probe: ``SELECT 1`` against the local Postgres."""

    name = "database"
    description = "Postgres connectivity + latency"
    interval_seconds = 300
    category = CATEGORY_INFRASTRUCTURE

    async def check(self, pool: Any, config: dict[str, Any]) -> ProbeResult:
        timeout_s = config.get("timeout_ms", 5000) / 1000.0
        start = time.monotonic()
        try:
            async with pool.acquire() as conn:
                result = await asyncio.wait_for(
                    conn.fetchval("SELECT 1"),
                    timeout=timeout_s,
                )
            latency_ms = int((time.monotonic() - start) * 1000)
            if result != 1:
                return ProbeResult(
                    ok=False,
                    detail=f"SELECT 1 returned {result!r}, expected 1",
                    severity="critical",
                )
            return ProbeResult(
                ok=True,
                detail=f"Postgres OK ({latency_ms}ms)",
                metrics={"latency_ms": latency_ms},
            )
        except asyncio.TimeoutError:
            return ProbeResult(
                ok=False,
                detail=f"Postgres query exceeded {timeout_s:.1f}s timeout",
                severity="critical",
            )
        except Exception as e:
            return ProbeResult(
                ok=False,
                detail=f"Postgres connection failed: {e}",
                severity="critical",
            )
