"""UpdateUtilityRatesJob — refresh electricity rate + GPU TDP in app_settings.

Replaces ``IdleWorker._update_utility_rates``. Runs every 24 hours by
default. Two independent refreshes:

1. **Electricity rate** — fetches the latest monthly residential price
   from the EIA (U.S. Energy Information Administration) open API,
   converts from cents/kWh to dollars/kWh, and updates
   ``electricity_rate_kwh`` in app_settings if the value has drifted
   by more than ``drift_threshold`` (default 10%).

2. **GPU TDP** — runs ``nvidia-smi`` to identify the GPU, looks up
   known TDPs, and updates ``gpu_power_watts`` if it changed.
   Silently no-ops when nvidia-smi isn't available (cloud box).

Each confirmed change is logged to ``audit_log`` as
``utility_rates_updated``.

## Config (``plugin.job.update_utility_rates``)

- ``config.eia_api_key`` (default read from site_config
  ``eia_api_key``, else ``"DEMO_KEY"`` for dev) — EIA requires a key
  for production use; DEMO_KEY is rate-limited.
- ``config.drift_threshold`` (default 0.10) — only update the rate
  when the delta exceeds this fraction.
- ``config.skip_electricity`` / ``config.skip_gpu`` (default false)
  — per-part toggles.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from plugins.job import JobResult

logger = logging.getLogger(__name__)


# Known GPU TDPs (watts). Configurable override: set
# ``site_config.gpu_tdp_map`` to a JSON object to extend without a code
# change.
DEFAULT_GPU_TDP_MAP: dict[str, int] = {
    "RTX 5090": 575,
    "RTX 5080": 360,
    "RTX 5070 Ti": 300,
    "RTX 5070": 250,
    "RTX 4090": 450,
    "RTX 4080": 320,
    "RTX 4070 Ti": 285,
    "RTX 4070": 200,
    "RTX 3090": 350,
    "RTX 3080": 320,
}


def _load_gpu_tdp_map(site_config: Any) -> dict[str, int]:
    """Load GPU TDP map from site_config if set, otherwise use defaults."""
    raw = site_config.get("gpu_tdp_map", "")
    if not raw:
        return DEFAULT_GPU_TDP_MAP
    try:
        override = json.loads(raw)
        if isinstance(override, dict):
            return {str(k): int(v) for k, v in override.items()}
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("UpdateUtilityRatesJob: invalid gpu_tdp_map in site_config: %s", e)
    return DEFAULT_GPU_TDP_MAP


async def _refresh_electricity_rate(
    pool: Any,
    *,
    api_key: str,
    drift_threshold: float,
) -> dict[str, Any] | None:
    """Pull latest EIA residential rate; upsert if drift > threshold.

    Returns the change dict when an update happens, else None.
    Exceptions bubble up — caller decides how to report.
    """
    eia_url = (
        "https://api.eia.gov/v2/electricity/retail-sales/data/"
        f"?api_key={api_key}"
        "&frequency=monthly"
        "&data[0]=price"
        "&facets[sectorid][]=RES"
        "&sort[0][column]=period"
        "&sort[0][direction]=desc"
        "&length=1"
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        resp = await client.get(eia_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

    records = data.get("response", {}).get("data", [])
    if not records:
        logger.warning("UpdateUtilityRatesJob: EIA API returned no records")
        return None

    cents_kwh = float(records[0]["price"])
    dollars_kwh = round(cents_kwh / 100, 4)
    period = records[0].get("period", "unknown")

    async with pool.acquire() as conn:
        current_raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'electricity_rate_kwh'"
        )
    current_rate = float(current_raw) if current_raw else 0.0

    drift = 1.0 if current_rate == 0 else abs(dollars_kwh - current_rate) / max(current_rate, 0.001)
    if drift <= drift_threshold:
        logger.info(
            "UpdateUtilityRatesJob: electricity rate unchanged "
            "($%.4f/kWh, EIA %s, <=%.0f%% drift)",
            dollars_kwh, period, drift_threshold * 100,
        )
        return None

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('electricity_rate_kwh', $1, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()
            """,
            str(dollars_kwh),
        )
    logger.info(
        "UpdateUtilityRatesJob: electricity rate $%.4f → $%.4f/kWh (EIA %s)",
        current_rate, dollars_kwh, period,
    )
    return {"old": current_rate, "new": dollars_kwh, "period": period}


async def _refresh_gpu_tdp(
    pool: Any, tdp_map: dict[str, int],
) -> dict[str, Any] | None:
    """Detect GPU via nvidia-smi, look up TDP, upsert if changed.

    Returns the change dict when an update happens, else None.
    Missing nvidia-smi (cloud boxes) is expected — callers handle it
    via the OSError/FileNotFoundError it raises.
    """
    proc = await asyncio.create_subprocess_exec(
        "nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    gpu_name = stdout.decode().strip().split("\n")[0].strip()
    if not gpu_name:
        return None

    tdp: int | None = None
    for model, watts in tdp_map.items():
        if model in gpu_name:
            tdp = watts
            break
    if tdp is None:
        logger.info("UpdateUtilityRatesJob: GPU %r not in TDP map — skip", gpu_name)
        return None

    async with pool.acquire() as conn:
        current_raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'gpu_power_watts'"
        )
    current_int = int(current_raw) if current_raw else 0

    if current_int == tdp:
        return None

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('gpu_power_watts', $1, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()
            """,
            str(tdp),
        )
    logger.info(
        "UpdateUtilityRatesJob: GPU TDP %dW → %dW (%s)", current_int, tdp, gpu_name,
    )
    return {"old": current_int, "new": tdp, "gpu": gpu_name}


class UpdateUtilityRatesJob:
    name = "update_utility_rates"
    description = "Refresh electricity rate (EIA) + GPU TDP (nvidia-smi) in app_settings"
    schedule = "every 24 hours"
    idempotent = True

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any,
    ) -> JobResult:
        drift_threshold = float(config.get("drift_threshold", 0.10))
        skip_electricity = bool(config.get("skip_electricity", False))
        skip_gpu = bool(config.get("skip_gpu", False))
        api_key = (
            config.get("eia_api_key")
            or site_config.get("eia_api_key", "")
            or "DEMO_KEY"
        )

        changes: dict[str, Any] = {}

        if not skip_electricity:
            try:
                change = await _refresh_electricity_rate(
                    pool,
                    api_key=api_key,
                    drift_threshold=drift_threshold,
                )
                if change:
                    changes["electricity_rate_kwh"] = change
            except Exception as e:
                logger.warning("UpdateUtilityRatesJob: EIA fetch failed: %s", e)

        if not skip_gpu:
            try:
                tdp_map = _load_gpu_tdp_map(site_config)
                change = await _refresh_gpu_tdp(pool, tdp_map)
                if change:
                    changes["gpu_power_watts"] = change
            except (OSError, FileNotFoundError, asyncio.TimeoutError) as e:
                # nvidia-smi unavailable — expected on cloud boxes.
                logger.debug("UpdateUtilityRatesJob: GPU detect skipped (%s)", e)
            except Exception as e:
                logger.warning("UpdateUtilityRatesJob: GPU detect failed: %s", e)

        # audit_log insert (best-effort)
        if changes:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO audit_log (event_type, source, details, severity) "
                        "VALUES ($1, $2, $3, $4)",
                        "utility_rates_updated",
                        "update_utility_rates_job",
                        json.dumps(changes),
                        "info",
                    )
            except Exception as e:
                logger.debug("UpdateUtilityRatesJob: audit_log insert failed: %s", e)

        detail = (
            f"{len(changes)} setting(s) updated: {list(changes.keys())}"
            if changes else "all utility rates current"
        )
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(changes),
            metrics={"settings_updated": len(changes)},
        )
