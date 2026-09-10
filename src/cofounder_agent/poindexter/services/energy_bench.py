"""Energy-measurement helpers — real GPU watts per LLM call (#530).

A small, DI-friendly service: no module singletons, no app_settings reads
of its own — the caller passes ``prometheus_url`` in. Available to any job
that wants to attribute real GPU watts to an LLM call. (The original
``scripts/bench/eval_cost_tiers.py`` consumer was retired with the cost_tier
removal, PR #1907.)

The static-TDP estimate in ``cost_guard.estimate_local_kwh`` (``gpu_power_watts``
× duration) is a conservative constant; for measuring *true*
intelligence-per-watt across models we want the actual draw. nvidia-smi
exports ``nvidia_gpu_power_draw_watts`` at the :9835 exporter, scraped into
Prometheus (:9091). ``measure_gpu_watts`` reads the average draw over the
call window straight from the Prometheus HTTP API.

Per ``feedback_no_dummy_data``: if Prometheus is unreachable or has no data
for the window, ``measure_gpu_watts`` returns ``None`` so the caller records
NULL (and falls back to the estimate) rather than fabricating a number.
"""

from __future__ import annotations

import logging
import math

import httpx

logger = logging.getLogger(__name__)


async def measure_gpu_watts(
    prometheus_url: str,
    start_ts: float,
    end_ts: float,
    *,
    timeout: float = 10.0,
) -> float | None:
    """Average ``nvidia_gpu_power_draw_watts`` over [start_ts, end_ts].

    Queries the Prometheus HTTP API with
    ``avg_over_time(nvidia_gpu_power_draw_watts[{window}s])`` evaluated at
    ``end_ts``, where ``window`` spans the call duration (min 1s so a very
    fast call still captures at least one scrape sample).

    Returns the average watts as a float, or ``None`` when Prometheus is
    unreachable, returns an error, or has no sample for the window — the
    caller treats ``None`` as "no measurement, fall back to estimate".
    """
    if not prometheus_url:
        return None

    window_s = max(1, math.ceil(float(end_ts) - float(start_ts)))
    query = f"avg_over_time(nvidia_gpu_power_draw_watts[{window_s}s])"
    url = f"{prometheus_url.rstrip('/')}/api/v1/query"
    params = {"query": query, "time": f"{end_ts}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[energy_bench] Prometheus query failed (%s) — gpu_watts=None", exc)
        return None

    if payload.get("status") != "success":
        logger.warning(
            "[energy_bench] Prometheus status %r — gpu_watts=None",
            payload.get("status"),
        )
        return None

    result = (payload.get("data") or {}).get("result") or []
    if not result:
        return None

    try:
        raw_value = result[0]["value"][1]
        watts = float(raw_value)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("[energy_bench] could not parse Prometheus value (%s)", exc)
        return None

    if math.isnan(watts):
        return None
    return watts


def joules_per_token(
    gpu_watts_avg: float | None,
    duration_ms: int,
    total_tokens: int,
) -> float | None:
    """Energy per generated token in joules: (watts × seconds) / tokens.

    Returns ``None`` when watts is unknown (no measurement) or when there
    are no tokens to divide by — both are "can't compute" cases, not zero.
    """
    if gpu_watts_avg is None:
        return None
    if not total_tokens or total_tokens <= 0:
        return None
    seconds = float(duration_ms) / 1000.0
    return (float(gpu_watts_avg) * seconds) / float(total_tokens)


def tokens_per_second(total_tokens: int, duration_ms: int) -> float | None:
    """Throughput in tokens/second. ``None`` when duration is non-positive."""
    if not duration_ms or duration_ms <= 0:
        return None
    seconds = float(duration_ms) / 1000.0
    return float(total_tokens) / seconds


__all__ = [
    "joules_per_token",
    "measure_gpu_watts",
    "tokens_per_second",
]
