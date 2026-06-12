"""AnalyzeTopicGapsJob — flag empty / low-coverage / stale categories.

Replaces ``IdleWorker._analyze_topic_gaps``. Runs every 24h by default.
Produces three classes of findings:

- **Empty categories**: defined in ``categories`` but zero published
  posts. Usually means a category was added for future work that never
  materialized.
- **Low coverage**: categories with 1–(``low_threshold``-1) posts.
  Candidate for targeted topic-discovery cycles.
- **Stale categories**: categories whose latest published post is
  older than ``stale_days`` days. Signals the topic has gone cold in
  the content pipeline.

Any finding triggers a dedup'd Gitea issue so an operator can adjust
the topic-discovery bias.

## Config (``plugin.job.analyze_topic_gaps``)

- ``config.low_threshold`` (default 5) — "low coverage" band upper
  bound
- ``config.stale_days`` (default 14) — category is stale if no post
  newer than this
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


class AnalyzeTopicGapsJob:
    name = "analyze_topic_gaps"
    description = "Flag empty, low-coverage, and stale categories for topic-discovery rebalancing"
    schedule = "every 24 hours"
    idempotent = True  # Read-only analysis

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        low_threshold = int(config.get("low_threshold", 5))
        stale_days = int(config.get("stale_days", 14))
        file_issue = bool(config.get("file_gitea_issue", True))

        try:
            async with pool.acquire() as conn:
                categories = await conn.fetch(
                    """
                    SELECT c.name, COUNT(p.id) as posts
                    FROM categories c
                    LEFT JOIN posts p ON c.id = p.category_id
                      AND p.status = 'published'
                    GROUP BY c.name
                    ORDER BY posts ASC
                    """,
                )
                stale = await conn.fetch(
                    """
                    SELECT c.name, MAX(p.published_at) as latest
                    FROM categories c
                    JOIN posts p ON c.id = p.category_id
                      AND p.status = 'published'
                    GROUP BY c.name
                    HAVING MAX(p.published_at) < NOW() - INTERVAL '1 day' * $1
                    """,
                    stale_days,
                )
        except Exception as e:
            logger.exception("AnalyzeTopicGapsJob: query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {e}", changes_made=0)

        empty = [r["name"] for r in categories if r["posts"] == 0]
        low = [
            f"{r['name']} ({r['posts']})"
            for r in categories
            if 0 < r["posts"] < low_threshold
        ]
        stale_names = [r["name"] for r in stale]

        suggestions: list[str] = []
        if empty:
            suggestions.append(
                f"Empty categories need posts: {', '.join(empty)}",
            )
        if low:
            suggestions.append(f"Low coverage: {', '.join(low)}")
        if stale_names:
            suggestions.append(
                f"Stale categories (no post in {stale_days}d): {', '.join(stale_names)}",
            )

        if suggestions and file_issue:
            body = "## Topic Gap Analysis\n\n" + "\n".join(f"- {s}" for s in suggestions)
            emit_finding(
                source="analyze_topic_gaps",
                kind="topic_gap",
                # 'warn' (not the emit_finding 'info' default) so
                # findings_alert_router's SQL floor
                # (severity in warn/warning/critical) lets the finding
                # through to the seeded findings.topic_gap.delivery='discord'
                # policy — info findings are filtered out before any
                # per-kind routing. Matches utils/findings' severity→channel
                # model: warn → Discord (a routine, actionable ping).
                severity="warn",
                title=(
                    f"content: topic gaps — {len(empty)} empty, "
                    f"{len(low)} low, {len(stale_names)} stale"
                ),
                body=body,
                dedup_key="topic_gaps",
            )

        # poindexter#485 follow-up (Matt 2026-05-22): the
        # ``services.topic_sources.knowledge.KnowledgeSource`` reads
        # ``brain_knowledge`` rows where ``attribute='topic_gap'`` to
        # seed the operator-curated topic queue. Before this PR the
        # writer side only emitted an ``audit_log`` finding, so the
        # source had zero input for 263 consecutive runs (4 months of
        # the ``knowledge`` tap doing nothing). Now we also upsert
        # per-category rows that the source consumes directly. The
        # finding stays — it's the operator-visible alert; these
        # rows are the structured-data feed.
        rows_written = await _upsert_topic_gap_rows(
            pool, empty=empty, low=low, stale_names=stale_names,
        )

        detail = (
            f"{len(empty)} empty, {len(low)} low, {len(stale_names)} stale"
            if suggestions else "all categories healthy"
        )
        logger.info(
            "AnalyzeTopicGapsJob: %s, %d brain_knowledge rows upserted",
            detail, rows_written,
        )
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(suggestions),
            metrics={
                "empty_categories": len(empty),
                "low_coverage_categories": len(low),
                "stale_categories": len(stale_names),
                "topic_gap_rows_written": rows_written,
            },
        )


async def _upsert_topic_gap_rows(
    pool: Any,
    *,
    empty: list[str],
    low: list[str],
    stale_names: list[str],
) -> int:
    """Materialise topic-gap findings as ``brain_knowledge`` rows.

    ``services.topic_sources.knowledge.KnowledgeSource`` reads these
    rows to seed the operator-curated topic queue. One row per gap
    category, keyed on ``(entity='category.<slug>', attribute='topic_gap')``
    so the schema's unique index handles dedup across re-runs (the
    value gets refreshed with the latest signal).

    ``low`` entries arrive shaped as ``"<name> (<count>)"`` from the
    caller — the parenthesised count is stripped before keying so
    repeat analyses don't churn the row when only the count changes.

    Never raises — DB failure logs at warning level and returns the
    rows that DID succeed; the analyse-and-emit_finding path is the
    primary contract and shouldn't be gated by this secondary write.
    """
    if not (empty or low or stale_names):
        return 0

    def _slugify(name: str) -> str:
        slug = name.lower().strip()
        # Strip the "(N)" suffix the low-list carries.
        if "(" in slug:
            slug = slug.split("(", 1)[0].strip()
        # Replace anything non-alnum with a single dot so the entity
        # stays compact (``category.ai_ml``, not ``category.AI%2FML``).
        cleaned: list[str] = []
        prev_dot = False
        for ch in slug:
            if ch.isalnum():
                cleaned.append(ch)
                prev_dot = False
            elif not prev_dot:
                cleaned.append(".")
                prev_dot = True
        return "".join(cleaned).strip(".") or "unknown"

    rows: list[tuple[str, str]] = []
    for name in empty:
        rows.append((
            f"category.{_slugify(name)}",
            f"Write a post in the {name} category — no published posts yet.",
        ))
    for entry in low:
        # entry shape: "<name> (<count>)"
        name = entry.split("(", 1)[0].strip() if "(" in entry else entry
        rows.append((
            f"category.{_slugify(name)}",
            f"Expand coverage of the {name} category — only {entry}.",
        ))
    for name in stale_names:
        rows.append((
            f"category.{_slugify(name)}",
            f"Refresh the {name} category — the latest published post has gone stale.",
        ))

    if not rows:
        return 0

    sql = """
        INSERT INTO brain_knowledge (
            entity, attribute, value, source, confidence, updated_at
        )
        VALUES ($1, 'topic_gap', $2, 'analyze_topic_gaps', 0.8, NOW())
        ON CONFLICT (entity, attribute) DO UPDATE
            SET value      = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at,
                source     = EXCLUDED.source,
                confidence = EXCLUDED.confidence
    """
    written = 0
    try:
        async with pool.acquire() as conn:
            for entity, value in rows:
                try:
                    await conn.execute(sql, entity, value)
                    written += 1
                except Exception as e:  # noqa: BLE001 — best-effort per-row
                    logger.warning(
                        "AnalyzeTopicGapsJob: brain_knowledge upsert "
                        "failed for entity=%s: %s", entity, e,
                    )
    except Exception as e:  # noqa: BLE001 — connection-level failure
        logger.warning(
            "AnalyzeTopicGapsJob: brain_knowledge upsert pool failure: %s", e,
        )
    return written
