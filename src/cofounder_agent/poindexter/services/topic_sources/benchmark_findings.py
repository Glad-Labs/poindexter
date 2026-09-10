"""BenchmarkFindingsSource — propose topics from our own instrumentation.

The other topic sources read the outside world: HackerNews, Dev.to, RSS, web
search, GSC query gaps. This one reads `cost_logs` — the ~100k instrumented
inference calls the pipeline has generated running itself — and proposes a
post only when those measurements support a claim nobody else can make.

That is the point. A post assembled from what is already on the internet earns
nothing however well it is written; uniqueness is a property of information,
not of authorship. Since 2026-08-26 every Ollama-routed call records both
wall-clock duration and Ollama's true decode duration, so we can state the gap
between what a model decodes at and what the application actually receives.
Every published benchmark reports the first number. The second one takes months
of real multi-model workload on one contended box with per-call
instrumentation.

**The measurements travel with the topic.** ``DiscoveredTopic.description``
becomes ``topic_pool.summary``, which ``create_blog_post_task`` now carries
into the task's ``metadata.research_context`` — layer 1 of
``writer_core._collect_research_context``. So the writer is grounded on the
real numbers, and ``qa.numeric_fidelity`` later checks the finished draft
against that same block. A figure the writer invents will not reconcile.

Freshness is gated explicitly rather than by churning titles to slip past
``topic_pool``'s dedup: a finding is re-proposed only after ``cooldown_days``
without a pool row for the same finding. Gaming the dedup key would make the
source's cadence a lie.

Config (``plugin.topic_source.benchmark_findings``):

- ``enabled`` (default ``false``) — seeded as a row rather than defaulted in
  Python: ``topic_sources/runner.py`` falls back to ``enabled=True`` for a
  MISSING plugin row, so a Python-side default would silently switch this on.
  It proposes real pipeline work off measured data, so an operator should read
  one proposal before it runs unattended.
- ``window_days`` (default 30), ``min_calls`` (default 30) — the sample floor.
- ``min_models`` (default 3), ``min_spread_pct`` (default 25) — the fleet
  finding needs breadth and a real effect. "Everything behaves the same" is
  true, dull, and not a post.
- ``new_model_days`` (default 30) — how recently a model's FIRST call must be
  for it to count as new. Separate from ``window_days``: the measurement window
  and "is this model new" are different questions.
- ``cooldown_days`` (default 30), ``max_topics`` (default 2).
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.topic_source import DiscoveredTopic
from services.benchmark_findings import KIND_NEW_MODEL, build_findings, measure_models

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "window_days": 30,
    "min_calls": 30,
    "min_models": 3,
    "min_spread_pct": 25.0,
    "cooldown_days": 30,
    "new_model_days": 30,
    "max_topics": 2,
    "category": "engineering",
}

# Titles already proposed by this source inside the cooldown window. Keyed on
# the pool's own rows so the gate survives a worker restart (an in-process set
# would re-propose every deploy).
_RECENT_SQL = """
    SELECT title
    FROM topic_pool
    WHERE source = $1
      AND ingested_at >= NOW() - ($2 * INTERVAL '1 day')
"""

# A model counts as NEW when its first call of ANY kind is inside the window.
#
# Deliberately NOT filtered on decode_duration_ms: that column only exists from
# 2026-08-26, so "first decode-instrumented call" makes every model on the
# fleet look brand new. qwen3-vl:30b — running for months — was proposed as a
# new-model finding on the first run for exactly that reason. Newness is a fact
# about the model's history, not about when we started measuring it.
_FIRST_SEEN_SQL = """
    SELECT regexp_replace(model, '^ollama(_chat)?/', '') AS model,
           MIN(created_at) AS first_call
    FROM cost_logs
    WHERE cost_type = 'inference'
    GROUP BY 1
    HAVING MIN(created_at) >= NOW() - ($1 * INTERVAL '1 day')
"""


def _cfg(config: dict[str, Any], key: str) -> Any:
    raw = (config or {}).get(key)
    if raw is None or raw == "":
        return _DEFAULTS[key]
    default = _DEFAULTS[key]
    try:
        if isinstance(default, bool):
            return str(raw).strip().lower() in ("true", "1", "yes")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[benchmark_findings] %s=%r is not a %s — using %r",
            key, raw, type(default).__name__, default,
        )
        return default
    return raw


class BenchmarkFindingsSource:
    """Propose blog topics backed by our own measured telemetry."""

    name = "benchmark_findings"

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        # ``config`` is the INNER dict from
        # ``plugin.topic_source.benchmark_findings.config`` — the runner has
        # already applied the ``enabled`` flag and returns before calling us
        # when it is false. Re-reading it here would look for "enabled" inside
        # the inner dict, find nothing, and disable the source permanently.
        cfg = config or {}
        if pool is None:
            logger.warning("[benchmark_findings] no pool — cannot measure")
            return []

        window_days = _cfg(cfg, "window_days")
        min_calls = _cfg(cfg, "min_calls")

        try:
            measurements = await measure_models(
                pool, window_days=window_days, min_calls=min_calls,
            )
        except Exception as e:  # noqa: BLE001 — one source must not kill the run
            logger.warning("[benchmark_findings] measurement query failed: %s", e)
            return []

        if not measurements:
            logger.info(
                "[benchmark_findings] no model cleared the %d-call floor over "
                "%d days — proposing nothing", min_calls, window_days,
            )
            return []

        try:
            async with pool.acquire() as conn:
                recent = {
                    r["title"] for r in await conn.fetch(
                        _RECENT_SQL, self.name, _cfg(cfg, "cooldown_days"),
                    )
                }
                new_models = {
                    r["model"] for r in await conn.fetch(
                        _FIRST_SEEN_SQL, _cfg(cfg, "new_model_days"),
                    )
                }
        except Exception as e:  # noqa: BLE001
            # Without the cooldown read we cannot tell a fresh finding from one
            # proposed yesterday. Proposing anyway would spam the pool, so this
            # source stands down rather than guessing.
            logger.warning(
                "[benchmark_findings] cooldown/new-model lookup failed (%s) — "
                "standing down rather than risking duplicate proposals", e,
            )
            return []

        findings = build_findings(
            measurements,
            window_days=window_days,
            min_models=_cfg(cfg, "min_models"),
            min_spread_pct=_cfg(cfg, "min_spread_pct"),
            new_model_names=new_models,
        )

        topics: list[DiscoveredTopic] = []
        max_topics = _cfg(cfg, "max_topics")
        category = _cfg(cfg, "category")
        for finding in findings:
            if len(topics) >= max_topics:
                break
            if finding.title in recent:
                logger.info(
                    "[benchmark_findings] %s within cooldown — skipping",
                    finding.kind,
                )
                continue
            topics.append(
                DiscoveredTopic(
                    title=finding.title,
                    category=category,
                    source=self.name,
                    source_url="",  # our own telemetry has no external URL
                    # A finding that cleared every bar is, by construction, a
                    # claim we can make and others cannot. Rank it high enough
                    # to compete without hardcoding it to the top.
                    relevance_score=0.9 if finding.kind == KIND_NEW_MODEL else 0.85,
                    description=finding.fact_block,
                    keywords=list(finding.keywords),
                ),
            )

        logger.info(
            "[benchmark_findings] %d measurement(s) -> %d finding(s) -> "
            "%d topic(s)", len(measurements), len(findings), len(topics),
        )
        return topics


__all__ = ["BenchmarkFindingsSource"]
