"""Chart catalog — the allowlist that turns a marker key into real data.

``ChartProvider`` deliberately has no query surface: an image plugin that could
fetch its own data would need one, and a query surface reachable from a
writer-emitted marker is an injection seam. So the writer names a **key**, and
this module — a service, which is allowed to own a query — resolves that key
into a :class:`~services.chart_render.ChartSpec` built from live rows.

Exactly the shape ``ScreenshotProvider`` uses for its target allowlist
(poindexter#1002), applied one layer up: the model chooses *which* chart, never
*what it says*. An unknown key resolves to nothing rather than to something
plausible, so a hallucinated key renders no image instead of a wrong one.

Charts are defined **in code**, not in app_settings. A settings-defined chart
would mean operator-authored SQL reachable from an LLM-chosen key, which is the
seam this design exists to avoid; the per-install lever is the allowlist
(``chart_catalog_enabled_keys``), not the query.

Adding a chart means adding a builder here — a function that runs a read-only
query and returns a fully-populated ``ChartSpec``, including the ``source``
line, because a published chart must always say what produced it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from services.chart_render import ChartSpec, Series

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    description: str          # shown to the writer in the prompt allowlist
    build: Callable[..., Awaitable[ChartSpec | None]]


async def _build_decode_vs_delivered(
    pool: Any, *, window_days: int = 30, min_calls: int = 30,
) -> ChartSpec | None:
    """Per-model decode speed vs what the caller actually received.

    Shares ``services.benchmark_findings.measure_models`` with the topic source
    that proposes these posts, so the chart and the prose are drawn from one
    query rather than two that can drift apart.
    """
    from services.benchmark_findings import measure_models

    measurements = await measure_models(
        pool, window_days=window_days, min_calls=min_calls,
    )
    if len(measurements) < 2:
        # One bar is not a comparison. Rendering it would imply a contrast the
        # data cannot support.
        logger.info(
            "[chart_catalog] decode-vs-delivered needs >=2 models, have %d",
            len(measurements),
        )
        return None

    ordered = sorted(measurements, key=lambda m: m.decode_tps, reverse=True)
    total_calls = sum(m.calls for m in ordered)
    return ChartSpec(
        form="bar",
        title="What local models deliver versus their decode speed",
        subtitle="Median over real pipeline work — same GPU, same workload",
        categories=[m.model for m in ordered],
        series=[
            Series("Raw decode", [m.decode_tps for m in ordered]),
            Series("Delivered to caller", [m.wall_tps for m in ordered]),
        ],
        value_label="output tokens / second",
        source=(
            f"Poindexter cost_logs — {total_calls} instrumented production "
            f"calls over {window_days} days"
        ),
    )


_CATALOG: dict[str, CatalogEntry] = {
    "llm-decode-vs-delivered": CatalogEntry(
        key="llm-decode-vs-delivered",
        description=(
            "per-model bar chart of raw decode speed vs the throughput the "
            "application actually receives, from our own cost_logs"
        ),
        build=_build_decode_vs_delivered,
    ),
}

_ENABLED_KEYS_SETTING = "chart_catalog_enabled_keys"


def enabled_keys(site_config: Any) -> list[str]:
    """Keys this install permits, in catalog order.

    Empty setting = every catalogued chart. A chart is code-defined and
    read-only, so the conservative default here is *available* rather than
    *inert*; the operator lever exists to narrow it, not to switch on
    something that could surprise them.
    """
    raw = ""
    if site_config is not None:
        try:
            raw = str(site_config.get(_ENABLED_KEYS_SETTING, "") or "")
        except Exception:  # noqa: BLE001 — silent-ok: an unreadable optional allowlist falls back to the full catalog, which is the same set a fresh install gets; a marker that then fails to resolve is logged by resolve() itself
            raw = ""
    wanted = [k.strip() for k in raw.split(",") if k.strip()]
    if not wanted:
        return list(_CATALOG)
    return [k for k in _CATALOG if k in wanted]


def describe_for_prompt(site_config: Any) -> str:
    """Render the allowlist for the writer prompt.

    The writer can only name a catalogued key, so the prompt must enumerate
    them — otherwise the model invents plausible keys and every one resolves to
    an empty slot.
    """
    keys = enabled_keys(site_config)
    if not keys:
        return "none available — do not use [CHART: …] markers"
    return "\n".join(f"- {k}: {_CATALOG[k].description}" for k in keys)


async def resolve(
    key: str, *, pool: Any, site_config: Any = None, **kwargs: Any,
) -> ChartSpec | None:
    """Build the ``ChartSpec`` for ``key``, or ``None`` if it cannot be built.

    ``None`` covers every honest failure — unknown key, key not enabled here,
    not enough data, a query that raised — because the caller's response to all
    of them is the same: render no chart. A wrong chart is worse than none.
    """
    normalized = (key or "").strip().lower()
    if normalized not in _CATALOG:
        logger.warning("[chart_catalog] unknown chart key %r", key)
        return None
    if normalized not in enabled_keys(site_config):
        logger.info("[chart_catalog] chart key %r is not enabled here", key)
        return None
    if pool is None:
        logger.warning("[chart_catalog] no pool — cannot build chart %r", key)
        return None
    try:
        return await _CATALOG[normalized].build(pool, **kwargs)
    except Exception as e:  # noqa: BLE001 — a chart must never break the post
        logger.warning("[chart_catalog] building %r failed: %s", key, e)
        return None


def catalog_keys() -> list[str]:
    """Every key the catalog defines, regardless of per-install allowlist."""
    return list(_CATALOG)


__all__ = [
    "CatalogEntry",
    "catalog_keys",
    "describe_for_prompt",
    "enabled_keys",
    "resolve",
]
