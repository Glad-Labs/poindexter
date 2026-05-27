"""Runner — invoke every registered TopicSource and aggregate results.

Same pattern as ``services/taps/runner.py``: discover sources via
``plugins.registry.get_topic_sources()``, read per-source config from
``plugin.topic_source.<name>`` in app_settings, run them in parallel
with per-source isolation (one source crashing does not kill the
others), aggregate into a single list for the dedup pass.

Called from ``TopicDiscovery.discover()`` — the legacy hardcoded
``_scrape_hackernews`` / ``_scrape_devto`` paths are replaced by a
plugin-runner invocation. Sources that are still inside the dispatcher
(knowledge, codebase, web_search) continue to run from there until
they're migrated.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from plugins.topic_source import DiscoveredTopic

logger = logging.getLogger(__name__)


@dataclass
class SourceStats:
    name: str
    enabled: bool = True
    topics_returned: int = 0
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class RunnerSummary:
    topics: list[DiscoveredTopic] = field(default_factory=list)
    per_source: list[SourceStats] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.topics)


async def _load_source_config(pool: Any, source_name: str) -> tuple[bool, dict[str, Any]]:
    """Return ``(enabled, config_dict)`` for a source.

    Config schema matches ``plugins.config.PluginConfig``:
    ``{enabled: bool, config: {...}}``. Falls back to enabled=True
    with empty config when the row is missing.
    """
    from plugins.config import PluginConfig
    cfg = await PluginConfig.load(pool, "topic_source", source_name)
    return cfg.enabled, cfg.config


async def run_all(pool: Any) -> RunnerSummary:
    """Invoke every registered TopicSource in parallel and aggregate.

    Per-source errors are captured into ``SourceStats.error`` and
    do NOT propagate — one failing source should never starve the
    whole discovery pass. Aggregated topics are passed through to
    the caller for dedup + ranking.
    """
    from plugins.registry import get_core_samples, get_topic_sources

    sources = list(get_topic_sources()) + list(
        get_core_samples().get("topic_sources", [])
    )

    # De-dup by name — an entry_point AND a core-sample entry for the
    # same source would otherwise double-run.
    seen: set[str] = set()
    unique_sources: list[Any] = []
    for src in sources:
        name = getattr(src, "name", type(src).__name__)
        if name not in seen:
            seen.add(name)
            unique_sources.append(src)

    # Per-source timeout — without this, one hanging upstream
    # (HackerNews API stall, DevTo proxy block, web_search 30s
    # connect-timeout × N retries) blocks the entire discovery pass
    # behind ``asyncio.gather`` below. Default 60s is generous enough
    # for slow remote APIs but tight enough that the operator notices
    # within a single run cycle. Operators can tune globally via
    # ``topic_source_per_source_timeout_s`` or per-source via
    # ``plugin.topic_source.<name>.config.timeout_s``.
    global_timeout_s = 60.0
    try:
        from plugins.config import PluginConfig
        runner_cfg = await PluginConfig.load(pool, "topic_source", "_runner")
        global_timeout_s = float(
            runner_cfg.config.get("per_source_timeout_s")
            or runner_cfg.config.get("timeout_s")
            or global_timeout_s
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "TopicSources runner: timeout config lookup failed, using "
            "default %.1fs: %s",
            global_timeout_s, exc,
        )

    async def _run_one(source: Any) -> tuple[SourceStats, list[DiscoveredTopic]]:
        import time
        stats = SourceStats(name=getattr(source, "name", type(source).__name__))
        enabled, source_config = await _load_source_config(pool, stats.name)
        if not enabled:
            stats.enabled = False
            return stats, []

        # Resolve per-source timeout override; fall back to the global
        # value. Cast to float because asyncio.wait_for rejects strings
        # silently with a confusing TypeError.
        try:
            source_timeout_s = float(source_config.get("timeout_s") or global_timeout_s)
        except (TypeError, ValueError):
            source_timeout_s = global_timeout_s

        start = time.monotonic()
        topics: list[DiscoveredTopic] = []
        try:
            result = await asyncio.wait_for(
                source.extract(pool, source_config),
                timeout=source_timeout_s,
            )
            if result:
                topics = list(result)
        except asyncio.TimeoutError:
            stats.error = f"timed out after {source_timeout_s:.0f}s"
            logger.warning(
                "TopicSource %s: extract timed out after %.0fs — other "
                "sources still ran",
                stats.name, source_timeout_s,
            )
        except Exception as e:
            stats.error = str(e)[:200]
            logger.exception(
                "TopicSource %s: extract failed: %s", stats.name, stats.error,
            )
        stats.duration_s = time.monotonic() - start
        stats.topics_returned = len(topics)
        return stats, topics

    results = await asyncio.gather(
        *[_run_one(s) for s in unique_sources],
        return_exceptions=False,  # each _run_one catches internally
    )

    summary = RunnerSummary()
    for stats, topics in results:
        summary.per_source.append(stats)
        summary.topics.extend(topics)

    logger.info(
        "TopicSources runner: %d topics from %d source(s)",
        summary.total, len(summary.per_source),
    )
    return summary
