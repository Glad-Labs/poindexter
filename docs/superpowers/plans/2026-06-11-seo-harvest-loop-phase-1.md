# SEO Harvest Loop — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the read-only "harvest" slice — a scheduled analyzer that classifies every published post into SEO opportunity tiers from data we already collect, writing a ranked `seo_opportunities` list ("fix these 24 posts") with zero content-mutation risk.

**Architecture:** A pure classifier (`classify_opportunity`, unit-tested) decides a post's tier + gap-score from its latest GSC metrics; a thin DB reader (`analyze`) pulls the latest `post_performance` snapshot per published post and upserts results into a new `seo_opportunities` table; a scheduled worker job (`RunSeoOpportunityAnalyzerJob`) wires it to the PluginScheduler and emits a findings summary; a file-provisioned Grafana dashboard surfaces it. All thresholds are `app_settings`. Lives in substrate (`services/seo/`) — Phase 1 is analytics, not content-generation.

**Tech Stack:** Python 3.13 / asyncpg / Poetry, the PluginScheduler job system, `utils.findings.emit_finding`, file-provisioned Grafana (postgres datasource), pytest (`tests/unit/` + `tests/integration_db/`).

**Spec:** `docs/superpowers/specs/2026-06-11-seo-harvest-loop-design.md` (Phase 1 = §3 Phase 1).

**Scope note:** Phase 1 only. No refresh path, no content mutation, no `seo_refresh` graph_def (Phase 2). The analyzer works on the page-level GSC data already in `post_performance`; the `query` dimension (Task 8) only sharpens `target_query` later.

---

## File map

| File                                                                                  | Create/Modify | Responsibility                                                             |
| ------------------------------------------------------------------------------------- | ------------- | -------------------------------------------------------------------------- |
| `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_create_seo_opportunities.py` | Create        | `seo_opportunities` table + indexes + unique constraint + retention policy |
| `src/cofounder_agent/services/seo/__init__.py`                                        | Create        | Package marker                                                             |
| `src/cofounder_agent/services/seo/striking_distance.py`                               | Create        | Pure classifier + gap-score + DB analyzer/upsert                           |
| `src/cofounder_agent/services/jobs/run_seo_opportunity_analyzer.py`                   | Create        | Scheduled job wrapper (reads settings → analyze → upsert → emit_finding)   |
| `src/cofounder_agent/plugins/registry.py`                                             | Modify        | Register the job in `_SAMPLES`                                             |
| `src/cofounder_agent/services/settings_defaults.py`                                   | Modify        | `seo.*` threshold + gate keys                                              |
| `infrastructure/grafana/dashboards/seo-harvest.json`                                  | Create        | Tier counts + top-opportunities table + CTR trend                          |
| `src/cofounder_agent/tests/unit/services/seo/test_striking_distance_classifier.py`    | Create        | Pure classifier + gap-score unit tests                                     |
| `src/cofounder_agent/tests/integration_db/test_seo_opportunities.py`                  | Create        | Schema exists + analyzer roundtrip                                         |
| `docs/architecture/seo-harvest-loop.md`                                               | Create        | Short operator/dev doc, links the spec                                     |

---

## Data shapes (single source of truth for later tasks)

A classified opportunity is a plain dataclass returned by the pure classifier:

```python
# in services/seo/striking_distance.py
from dataclasses import dataclass

OPPORTUNITY_TIERS = ("page1_push", "striking_distance", "low_ctr")

@dataclass(frozen=True)
class Opportunity:
    tier: str            # one of OPPORTUNITY_TIERS
    gap_score: float     # estimated clicks/month left on the table; higher = bigger win
```

Thresholds dict passed into the classifier (resolved from app_settings by the job):

```python
# keys + defaults — see Task 4
{
    "striking_position_min": 5.0,
    "striking_position_max": 20.0,
    "push_position_min": 3.0,
    "push_position_max": 10.0,
    "push_min_impressions": 100,
    "low_ctr_min_impressions": 100,
    "low_ctr_max_ctr": 0.01,
    "target_ctr": 0.05,   # expected CTR once on page 1 — used for gap_score
}
```

Per-post metrics dict the classifier consumes (built by `analyze` from `post_performance`):

```python
{"impressions": int, "clicks": int, "ctr": float, "position": float}
```

---

### Task 1: `seo_opportunities` migration

**Files:**

- Create: `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_create_seo_opportunities.py` (generate the name — do not hand-write the timestamp)
- Test: `src/cofounder_agent/tests/integration_db/test_seo_opportunities.py`

- [ ] **Step 1: Generate the migration skeleton**

Run (from repo root):

```bash
python scripts/new-migration.py "create seo_opportunities table"
```

Expected: prints the created path `src/cofounder_agent/services/migrations/20260611_<HHMMSS>_create_seo_opportunities_table.py`.

- [ ] **Step 2: Write the migration body**

Replace the generated file's body with (keep the generated timestamp in the filename):

```python
"""Create seo_opportunities — the harvest analyzer's output table (Phase 1).

One row per (post_id, target_query) holding the current SEO opportunity for a
published post: which tier it's in (page1_push / striking_distance / low_ctr),
its live GSC metrics, a gap_score (estimated clicks left on the table), and
baseline/outcome columns reserved for Phase 2 refresh-outcome tracking.

target_query is '' (empty string, not NULL) when no specific query is known yet
(page-level data), so the UNIQUE(post_id, target_query) constraint behaves.

Light imports only (migrations-smoke): logging + json stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS seo_opportunities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id             UUID,
    slug                TEXT NOT NULL,
    target_query        TEXT NOT NULL DEFAULT '',
    tier                TEXT NOT NULL,
    current_position    NUMERIC(6,2),
    impressions         INTEGER NOT NULL DEFAULT 0,
    ctr                 NUMERIC(8,5),
    gap_score           NUMERIC(12,2) NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'open',
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    baseline_position   NUMERIC(6,2),
    baseline_ctr        NUMERIC(8,5),
    outcome_position    NUMERIC(6,2),
    outcome_ctr         NUMERIC(8,5),
    outcome_measured_at TIMESTAMPTZ
);
"""

_CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_seo_opportunities_post_query "
    "ON seo_opportunities (post_id, target_query);",
    "CREATE INDEX IF NOT EXISTS idx_seo_opportunities_tier "
    "ON seo_opportunities (tier);",
    "CREATE INDEX IF NOT EXISTS idx_seo_opportunities_status "
    "ON seo_opportunities (status);",
    "CREATE INDEX IF NOT EXISTS idx_seo_opportunities_gap_score "
    "ON seo_opportunities (gap_score DESC);",
]

# Retention: opportunities are a recomputed current-state view; prune rows not
# refreshed in 90 days (stale posts that fell out of all tiers). detected_at is
# bumped on every upsert, so a row only ages out if the analyzer stops emitting it.
_RETENTION = """
INSERT INTO retention_policies (
    name, handler_name, table_name, filter_sql,
    age_column, ttl_days, downsample_rule, summarize_handler,
    enabled, config, metadata
) VALUES (
    'seo_opportunities', 'ttl_prune', 'seo_opportunities', NULL,
    'detected_at', 90, NULL, NULL,
    true, '{}'::jsonb,
    '{"description": "Prune SEO opportunity rows not re-detected in 90 days"}'::jsonb
) ON CONFLICT (name) DO NOTHING
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await conn.execute(idx_sql)
        await conn.execute(_RETENTION)
    logger.info(
        "Migration create_seo_opportunities: table + %d indexes + retention policy",
        len(_CREATE_INDEXES),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM retention_policies WHERE name = 'seo_opportunities'")
        await conn.execute("DROP TABLE IF EXISTS seo_opportunities")
```

> NOTE: confirm `retention_policies` has a UNIQUE on `name`; if the ON CONFLICT
> target is the PK `id` instead, switch to `ON CONFLICT DO NOTHING` with an
> explicit `WHERE NOT EXISTS` guard. (Verify against
> `0000_baseline.schema.sql` retention_policies constraints during execution.)

- [ ] **Step 3: Write the schema test**

```python
# src/cofounder_agent/tests/integration_db/test_seo_opportunities.py
import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_seo_opportunities_table_exists_with_expected_columns(test_pool):
    async with test_pool.acquire() as conn:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'seo_opportunities'"
            )
        }
    for expected in (
        "post_id", "slug", "target_query", "tier", "current_position",
        "impressions", "ctr", "gap_score", "status", "detected_at",
        "baseline_position", "baseline_ctr", "outcome_position",
        "outcome_ctr", "outcome_measured_at",
    ):
        assert expected in cols, f"missing column {expected}"
```

- [ ] **Step 4: Verify migration applies + smoke**

Run:

```bash
python scripts/ci/migrations_smoke.py
python scripts/ci/migrations_lint.py
```

Expected: both exit 0; smoke reports the new migration applied with no error.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/ src/cofounder_agent/tests/integration_db/test_seo_opportunities.py
git commit -F - <<'EOF'
feat(seo): add seo_opportunities table + retention (harvest Phase 1)
EOF
```

---

### Task 2: Pure opportunity classifier (the business logic)

**Files:**

- Create: `src/cofounder_agent/services/seo/__init__.py` (empty)
- Create: `src/cofounder_agent/services/seo/striking_distance.py`
- Test: `src/cofounder_agent/tests/unit/services/seo/test_striking_distance_classifier.py`

- [ ] **Step 1: Write failing classifier unit tests**

```python
# src/cofounder_agent/tests/unit/services/seo/test_striking_distance_classifier.py
import pytest

from services.seo.striking_distance import (
    DEFAULT_THRESHOLDS,
    Opportunity,
    classify_opportunity,
    compute_gap_score,
)


def _metrics(impressions, clicks, position):
    ctr = (clicks / impressions) if impressions else 0.0
    return {"impressions": impressions, "clicks": clicks, "ctr": ctr, "position": position}


def test_page1_push_takes_priority_over_striking():
    # pos 6, 500 impressions → qualifies for BOTH push (3-10) and striking (5-20);
    # push wins (highest priority).
    opp = classify_opportunity(_metrics(500, 4, 6.0), DEFAULT_THRESHOLDS)
    assert opp is not None and opp.tier == "page1_push"


def test_striking_distance_when_below_push_band():
    # pos 15 → striking (5-20) but not push (<=10).
    opp = classify_opportunity(_metrics(300, 1, 15.0), DEFAULT_THRESHOLDS)
    assert opp is not None and opp.tier == "striking_distance"


def test_low_ctr_when_ranking_well_but_no_clicks():
    # pos 25 (outside push+striking), 2000 impressions, 0 clicks → low_ctr.
    opp = classify_opportunity(_metrics(2000, 0, 25.0), DEFAULT_THRESHOLDS)
    assert opp is not None and opp.tier == "low_ctr"


def test_no_opportunity_when_winning_already():
    # pos 1.5, healthy CTR → nothing to harvest.
    assert classify_opportunity(_metrics(1000, 300, 1.5), DEFAULT_THRESHOLDS) is None


def test_no_opportunity_when_below_volume_floor():
    # pos 25 but only 10 impressions and decent ctr → not worth flagging.
    assert classify_opportunity(_metrics(10, 1, 25.0), DEFAULT_THRESHOLDS) is None


def test_push_requires_min_impressions():
    # pos 6 but only 5 impressions → below push floor, not a push candidate.
    opp = classify_opportunity(_metrics(5, 0, 6.0), DEFAULT_THRESHOLDS)
    assert opp is None or opp.tier != "page1_push"


def test_gap_score_rewards_more_impressions():
    low = compute_gap_score(_metrics(100, 1, 8.0), DEFAULT_THRESHOLDS)
    high = compute_gap_score(_metrics(5000, 5, 8.0), DEFAULT_THRESHOLDS)
    assert high > low > 0


def test_thresholds_are_tunable():
    strict = {**DEFAULT_THRESHOLDS, "striking_position_max": 10.0}
    # pos 15 no longer striking under the tighter band, and not enough impressions
    # for low_ctr → no opportunity.
    assert classify_opportunity(_metrics(50, 0, 15.0), strict) is None
```

- [ ] **Step 2: Run to verify failure**

Run:

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/seo/test_striking_distance_classifier.py -q
```

Expected: FAIL — `ModuleNotFoundError: services.seo.striking_distance`.

- [ ] **Step 3: Implement the classifier (pure)**

Create `src/cofounder_agent/services/seo/__init__.py` (empty file), then `src/cofounder_agent/services/seo/striking_distance.py`:

```python
"""Striking-distance SEO analyzer (harvest Phase 1).

Pure classifier + a thin DB reader. The classifier decides a published post's
SEO opportunity tier and a gap_score from its latest GSC metrics; the analyzer
pulls the latest post_performance snapshot per post and upserts results into
seo_opportunities. Read-only re: content.

Tier priority (a post is assigned its single highest-priority tier):
  1. page1_push      — pos in [push_min, push_max] AND impressions >= push floor.
                       One optimization from page 1; the biggest, fastest wins.
  2. striking_distance — pos in [striking_min, striking_max]. Ranks but on page 2.
  3. low_ctr         — impressions >= floor AND ctr <= ceiling. Ranks somewhere
                       but the title/meta isn't earning the click.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

OPPORTUNITY_TIERS = ("page1_push", "striking_distance", "low_ctr")

DEFAULT_THRESHOLDS: dict[str, float] = {
    "striking_position_min": 5.0,
    "striking_position_max": 20.0,
    "push_position_min": 3.0,
    "push_position_max": 10.0,
    "push_min_impressions": 100.0,
    "low_ctr_min_impressions": 100.0,
    "low_ctr_max_ctr": 0.01,
    "target_ctr": 0.05,
}


@dataclass(frozen=True)
class Opportunity:
    tier: str
    gap_score: float


def compute_gap_score(metrics: dict[str, Any], thresholds: dict[str, float]) -> float:
    """Estimated clicks left on the table if this post reached `target_ctr`.

    Deterministic, not LLM. gap_score = impressions * (target_ctr - current_ctr),
    floored at 0. Bigger impression bases with weak CTR score highest, which is
    exactly the "fix this first" ordering.
    """
    impressions = float(metrics.get("impressions") or 0)
    ctr = float(metrics.get("ctr") or 0.0)
    target = float(thresholds.get("target_ctr", DEFAULT_THRESHOLDS["target_ctr"]))
    return max(0.0, impressions * (target - ctr))


def classify_opportunity(
    metrics: dict[str, Any], thresholds: dict[str, float]
) -> Opportunity | None:
    """Return the single highest-priority Opportunity for a post, or None."""
    position = metrics.get("position")
    impressions = float(metrics.get("impressions") or 0)
    ctr = float(metrics.get("ctr") or 0.0)
    if position is None:
        return None
    position = float(position)

    gap = compute_gap_score(metrics, thresholds)

    push_min = thresholds["push_position_min"]
    push_max = thresholds["push_position_max"]
    push_floor = thresholds["push_min_impressions"]
    if push_min <= position <= push_max and impressions >= push_floor:
        return Opportunity(tier="page1_push", gap_score=gap)

    s_min = thresholds["striking_position_min"]
    s_max = thresholds["striking_position_max"]
    if s_min <= position <= s_max:
        return Opportunity(tier="striking_distance", gap_score=gap)

    low_floor = thresholds["low_ctr_min_impressions"]
    low_ceiling = thresholds["low_ctr_max_ctr"]
    if impressions >= low_floor and ctr <= low_ceiling:
        return Opportunity(tier="low_ctr", gap_score=gap)

    return None
```

- [ ] **Step 4: Run to verify pass**

Run:

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/seo/test_striking_distance_classifier.py -q
```

Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/seo/ src/cofounder_agent/tests/unit/services/seo/
git commit -F - <<'EOF'
feat(seo): pure striking-distance classifier + gap score
EOF
```

---

### Task 3: DB analyzer (read latest snapshot → upsert opportunities)

**Files:**

- Modify: `src/cofounder_agent/services/seo/striking_distance.py` (append `analyze` + `upsert_opportunities`)
- Test: `src/cofounder_agent/tests/integration_db/test_seo_opportunities.py` (append)

- [ ] **Step 1: Write failing integration test**

Append to `tests/integration_db/test_seo_opportunities.py`:

```python
async def test_analyze_classifies_and_upserts(test_pool):
    from uuid import uuid4
    from services.seo.striking_distance import analyze_and_upsert, DEFAULT_THRESHOLDS

    post_id = uuid4()
    slug = f"seo-test-{post_id.hex[:8]}"
    try:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO posts (id, title, slug, status, published_at) "
                "VALUES ($1, 'SEO Test', $2, 'published', NOW())",
                post_id, slug,
            )
            # pos 6, 500 impressions, 4 clicks → page1_push
            await conn.execute(
                "INSERT INTO post_performance "
                "(post_id, slug, google_impressions, google_clicks, "
                " google_avg_position, measured_at) "
                "VALUES ($1, $2, 500, 4, 6.0, NOW())",
                post_id, slug,
            )

        written = await analyze_and_upsert(test_pool, DEFAULT_THRESHOLDS)
        assert written >= 1

        async with test_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tier, impressions, status, gap_score "
                "FROM seo_opportunities WHERE post_id = $1", post_id,
            )
        assert row is not None
        assert row["tier"] == "page1_push"
        assert row["impressions"] == 500
        assert row["status"] == "open"
        assert float(row["gap_score"]) > 0

        # Idempotent: a second run updates in place, no duplicate row.
        await analyze_and_upsert(test_pool, DEFAULT_THRESHOLDS)
        async with test_pool.acquire() as conn:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM seo_opportunities WHERE post_id = $1", post_id,
            )
        assert n == 1
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute("DELETE FROM seo_opportunities WHERE post_id = $1", post_id)
            await conn.execute("DELETE FROM post_performance WHERE post_id = $1", post_id)
            await conn.execute("DELETE FROM posts WHERE id = $1", post_id)
```

- [ ] **Step 2: Run to verify failure**

Run:

```bash
cd src/cofounder_agent && poetry run pytest tests/integration_db/test_seo_opportunities.py::test_analyze_classifies_and_upserts -m integration_db -q
```

Expected: FAIL — `ImportError: cannot import name 'analyze_and_upsert'`. (If no live DB, it SKIPs — run against the local stack DB.)

- [ ] **Step 3: Implement `analyze_and_upsert`**

Append to `services/seo/striking_distance.py`:

```python
_LATEST_SNAPSHOT_SQL = """
SELECT DISTINCT ON (pp.post_id)
       pp.post_id,
       pp.slug,
       pp.google_impressions  AS impressions,
       pp.google_clicks       AS clicks,
       pp.google_avg_position AS position
FROM post_performance pp
JOIN posts p ON p.id = pp.post_id AND p.status = 'published'
WHERE pp.google_impressions > 0
ORDER BY pp.post_id, pp.measured_at DESC
"""

_UPSERT_SQL = """
INSERT INTO seo_opportunities
    (post_id, slug, target_query, tier, current_position,
     impressions, ctr, gap_score, status, detected_at)
VALUES ($1, $2, '', $3, $4, $5, $6, $7, 'open', NOW())
ON CONFLICT (post_id, target_query) DO UPDATE
    SET tier             = EXCLUDED.tier,
        slug             = EXCLUDED.slug,
        current_position = EXCLUDED.current_position,
        impressions      = EXCLUDED.impressions,
        ctr              = EXCLUDED.ctr,
        gap_score        = EXCLUDED.gap_score,
        status           = 'open',
        detected_at      = NOW()
"""


async def analyze(pool: Any, thresholds: dict[str, float]) -> list[dict[str, Any]]:
    """Read the latest GSC snapshot per published post and classify each.

    Returns a list of dicts ready for upsert. Pure-read; no writes.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LATEST_SNAPSHOT_SQL)

    out: list[dict[str, Any]] = []
    for r in rows:
        impressions = int(r["impressions"] or 0)
        clicks = int(r["clicks"] or 0)
        position = float(r["position"]) if r["position"] is not None else None
        ctr = (clicks / impressions) if impressions else 0.0
        metrics = {"impressions": impressions, "clicks": clicks, "ctr": ctr, "position": position}
        opp = classify_opportunity(metrics, thresholds)
        if opp is None:
            continue
        out.append({
            "post_id": r["post_id"],
            "slug": r["slug"],
            "tier": opp.tier,
            "position": position,
            "impressions": impressions,
            "ctr": round(ctr, 5),
            "gap_score": round(opp.gap_score, 2),
        })
    return out


async def upsert_opportunities(pool: Any, opportunities: list[dict[str, Any]]) -> int:
    """Best-effort upsert into seo_opportunities. Returns rows written."""
    written = 0
    async with pool.acquire() as conn:
        for o in opportunities:
            try:
                await conn.execute(
                    _UPSERT_SQL,
                    o["post_id"], o["slug"], o["tier"], o["position"],
                    o["impressions"], o["ctr"], o["gap_score"],
                )
                written += 1
            except Exception as e:  # noqa: BLE001 — one bad row never aborts the run
                logger.warning("seo_opportunities upsert failed for %s: %s", o.get("slug"), e)
    return written


async def analyze_and_upsert(pool: Any, thresholds: dict[str, float]) -> int:
    """Convenience: analyze then upsert. Returns rows written."""
    opportunities = await analyze(pool, thresholds)
    return await upsert_opportunities(pool, opportunities)
```

- [ ] **Step 4: Run to verify pass**

Run:

```bash
cd src/cofounder_agent && poetry run pytest tests/integration_db/test_seo_opportunities.py -m integration_db -q
```

Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/seo/striking_distance.py src/cofounder_agent/tests/integration_db/test_seo_opportunities.py
git commit -F - <<'EOF'
feat(seo): analyzer reads latest GSC snapshot and upserts opportunities
EOF
```

---

### Task 4: Settings defaults

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (add to `DEFAULTS`)

- [ ] **Step 1: Add the keys**

Add a new block inside the `DEFAULTS` dict (place near other feature blocks; all values are strings — `''` is the unset sentinel, never `NULL`):

```python
    # ----- SEO Harvest Loop (Phase 1) -----
    # The read-only analyzer is safe-on so the opportunity list populates day one.
    # Content-mutating refresh (Phase 2) gates separately on seo.refresh.enabled.
    'seo.harvest.analyzer_enabled': 'true',
    'seo.refresh.enabled': 'false',
    'seo.striking_distance.position_min': '5',
    'seo.striking_distance.position_max': '20',
    'seo.push_candidate.position_min': '3',
    'seo.push_candidate.position_max': '10',
    'seo.push_candidate.min_impressions': '100',
    'seo.low_ctr.min_impressions': '100',
    'seo.low_ctr.max_ctr': '0.01',
    'seo.opportunity.target_ctr': '0.05',
    # Phase-2 / Task-8 forward-decls (unused in Phase 1; seeded for completeness):
    'seo.query_ingestion.enabled': 'false',
    'seo.refresh.outcome_measure_after_days': '14',
```

- [ ] **Step 2: Verify it loads**

Run:

```bash
cd src/cofounder_agent && poetry run python -c "from services.settings_defaults import DEFAULTS; print(DEFAULTS['seo.harvest.analyzer_enabled'], DEFAULTS['seo.striking_distance.position_max'])"
```

Expected: prints `true 20`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py
git commit -F - <<'EOF'
feat(seo): seed harvest threshold + gate settings defaults
EOF
```

---

### Task 5: Scheduled job + registry

**Files:**

- Create: `src/cofounder_agent/services/jobs/run_seo_opportunity_analyzer.py`
- Modify: `src/cofounder_agent/plugins/registry.py` (add tuple to `_SAMPLES`)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_run_seo_opportunity_analyzer.py`

- [ ] **Step 1: Write failing job tests**

```python
# src/cofounder_agent/tests/unit/services/jobs/test_run_seo_opportunity_analyzer.py
import pytest

from services.jobs.run_seo_opportunity_analyzer import RunSeoOpportunityAnalyzerJob


def test_job_has_required_attrs():
    job = RunSeoOpportunityAnalyzerJob()
    assert job.name == "run_seo_opportunity_analyzer"
    assert isinstance(job.schedule, str) and job.schedule
    assert job.idempotent is True


def test_job_registered_in_samples():
    from plugins.registry import _SAMPLES
    assert any(
        m == "services.jobs.run_seo_opportunity_analyzer"
        and c == "RunSeoOpportunityAnalyzerJob"
        for (_t, m, c) in _SAMPLES
    )


def test_thresholds_from_site_config_reads_keys():
    # _thresholds(sc) maps app_settings keys → the classifier thresholds dict.
    from services.jobs.run_seo_opportunity_analyzer import _thresholds

    class _SC:
        def get_float(self, key, default):
            return {"seo.striking_distance.position_max": 18.0}.get(key, default)

    th = _thresholds(_SC())
    assert th["striking_position_max"] == 18.0
    assert th["push_position_max"] == 10.0  # falls back to default
```

- [ ] **Step 2: Run to verify failure**

Run:

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_run_seo_opportunity_analyzer.py -q
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the job**

Create `src/cofounder_agent/services/jobs/run_seo_opportunity_analyzer.py`:

```python
"""Scheduled job: classify published posts into SEO opportunity tiers.

Read-only over post_performance — produces the "fix these N posts" list in
seo_opportunities and a findings summary. Gated by seo.harvest.analyzer_enabled
(default true; the analyzer mutates no content). The content-mutating refresh
path (Phase 2) gates separately on seo.refresh.enabled.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.seo.striking_distance import (
    DEFAULT_THRESHOLDS,
    analyze,
    upsert_opportunities,
)
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# app_settings key -> classifier threshold key
_SETTING_MAP = {
    "seo.striking_distance.position_min": "striking_position_min",
    "seo.striking_distance.position_max": "striking_position_max",
    "seo.push_candidate.position_min": "push_position_min",
    "seo.push_candidate.position_max": "push_position_max",
    "seo.push_candidate.min_impressions": "push_min_impressions",
    "seo.low_ctr.min_impressions": "low_ctr_min_impressions",
    "seo.low_ctr.max_ctr": "low_ctr_max_ctr",
    "seo.opportunity.target_ctr": "target_ctr",
}


def _thresholds(sc: Any) -> dict[str, float]:
    """Resolve classifier thresholds from site_config, defaulting per key."""
    th = dict(DEFAULT_THRESHOLDS)
    if sc is None:
        return th
    for setting_key, th_key in _SETTING_MAP.items():
        th[th_key] = float(sc.get_float(setting_key, DEFAULT_THRESHOLDS[th_key]))
    return th


class RunSeoOpportunityAnalyzerJob:
    name = "run_seo_opportunity_analyzer"
    description = (
        "Classify published posts into SEO opportunity tiers "
        "(page1_push / striking_distance / low_ctr) from latest GSC snapshot"
    )
    schedule = "every 24 hours"
    idempotent = True  # read + upsert is safe to re-run

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        enabled = True
        if sc is not None:
            enabled = sc.get("seo.harvest.analyzer_enabled", "true") not in ("false", "", "0")
        if not enabled:
            return JobResult(ok=True, detail="seo.harvest.analyzer_enabled is off; skipped")

        thresholds = _thresholds(sc)
        try:
            opportunities = await analyze(pool, thresholds)
            written = await upsert_opportunities(pool, opportunities)

            by_tier: dict[str, int] = {}
            for o in opportunities:
                by_tier[o["tier"]] = by_tier.get(o["tier"], 0) + 1
            push = by_tier.get("page1_push", 0)

            if push:
                top = sorted(opportunities, key=lambda o: o["gap_score"], reverse=True)[:10]
                body = "## SEO harvest — page-1-push candidates\n\n" + "\n".join(
                    f"- **{o['slug']}** — pos {o['position']:.1f}, "
                    f"{o['impressions']} impr, gap≈{o['gap_score']:.0f} clicks/mo"
                    for o in top
                )
                emit_finding(
                    source="run_seo_opportunity_analyzer",
                    kind="seo_opportunity",
                    title=(
                        f"SEO: {push} page-1-push, "
                        f"{by_tier.get('striking_distance', 0)} striking, "
                        f"{by_tier.get('low_ctr', 0)} low-CTR posts"
                    ),
                    body=body,
                    dedup_key="seo_opportunities",
                    extra=by_tier,
                )

            logger.info(
                "[run_seo_opportunity_analyzer] wrote %d opportunities %s",
                written, by_tier,
            )
            return JobResult(
                ok=True,
                detail=f"{written} opportunities ({by_tier})",
                changes_made=written,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[run_seo_opportunity_analyzer] failed (non-fatal): %s", e, exc_info=True,
            )
            return JobResult(ok=False, detail=f"analyzer failed: {type(e).__name__}: {e}")
```

> NOTE during execution: confirm `JobResult` field names (`ok`/`detail`/`changes_made`)
> and the `SiteConfig` accessor names (`get`, `get_float`) against
> `plugins/job.py` and `services/site_config.py`; adjust if the real signatures
> differ (e.g. `get_float` may be `get` + cast).

- [ ] **Step 4: Register in `_SAMPLES`**

In `src/cofounder_agent/plugins/registry.py`, find the `_SAMPLES` list near the
`analyze_topic_gaps` registration and add directly after it:

```python
        ("jobs", "services.jobs.run_seo_opportunity_analyzer", "RunSeoOpportunityAnalyzerJob"),
```

- [ ] **Step 5: Run to verify pass**

Run:

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_run_seo_opportunity_analyzer.py -q
```

Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/jobs/run_seo_opportunity_analyzer.py src/cofounder_agent/plugins/registry.py src/cofounder_agent/tests/unit/services/jobs/test_run_seo_opportunity_analyzer.py
git commit -F - <<'EOF'
feat(seo): scheduled analyzer job + registry registration
EOF
```

---

### Task 6: Grafana dashboard

**Files:**

- Create: `infrastructure/grafana/dashboards/seo-harvest.json`

- [ ] **Step 1: Author the dashboard**

Create `infrastructure/grafana/dashboards/seo-harvest.json` (datasource uid
`local-brain-db`; macros avoided so the panels lint cleanly):

```json
{
  "uid": "seo-harvest",
  "title": "SEO Harvest",
  "tags": ["seo", "harvest"],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "1h",
  "time": { "from": "now-30d", "to": "now" },
  "panels": [
    {
      "id": 1,
      "title": "Page-1-Push Candidates",
      "type": "stat",
      "gridPos": { "h": 5, "w": 6, "x": 0, "y": 0 },
      "datasource": {
        "type": "grafana-postgresql-datasource",
        "uid": "local-brain-db"
      },
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"] },
        "colorMode": "background"
      },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "editorMode": "code",
          "datasource": {
            "type": "grafana-postgresql-datasource",
            "uid": "local-brain-db"
          },
          "rawSql": "SELECT COUNT(*) AS value FROM seo_opportunities WHERE tier = 'page1_push' AND status = 'open'"
        }
      ]
    },
    {
      "id": 2,
      "title": "Striking Distance",
      "type": "stat",
      "gridPos": { "h": 5, "w": 6, "x": 6, "y": 0 },
      "datasource": {
        "type": "grafana-postgresql-datasource",
        "uid": "local-brain-db"
      },
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"] },
        "colorMode": "background"
      },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "editorMode": "code",
          "datasource": {
            "type": "grafana-postgresql-datasource",
            "uid": "local-brain-db"
          },
          "rawSql": "SELECT COUNT(*) AS value FROM seo_opportunities WHERE tier = 'striking_distance' AND status = 'open'"
        }
      ]
    },
    {
      "id": 3,
      "title": "Low-CTR Posts",
      "type": "stat",
      "gridPos": { "h": 5, "w": 6, "x": 12, "y": 0 },
      "datasource": {
        "type": "grafana-postgresql-datasource",
        "uid": "local-brain-db"
      },
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"] },
        "colorMode": "background"
      },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "editorMode": "code",
          "datasource": {
            "type": "grafana-postgresql-datasource",
            "uid": "local-brain-db"
          },
          "rawSql": "SELECT COUNT(*) AS value FROM seo_opportunities WHERE tier = 'low_ctr' AND status = 'open'"
        }
      ]
    },
    {
      "id": 4,
      "title": "Top Opportunities — fix these first",
      "type": "table",
      "gridPos": { "h": 12, "w": 24, "x": 0, "y": 5 },
      "datasource": {
        "type": "grafana-postgresql-datasource",
        "uid": "local-brain-db"
      },
      "options": {
        "showHeader": true,
        "sortBy": [{ "displayName": "gap_score", "desc": true }]
      },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "editorMode": "code",
          "datasource": {
            "type": "grafana-postgresql-datasource",
            "uid": "local-brain-db"
          },
          "rawSql": "SELECT slug, tier, current_position, impressions, ctr, gap_score FROM seo_opportunities WHERE status = 'open' ORDER BY gap_score DESC LIMIT 50"
        }
      ]
    },
    {
      "id": 5,
      "title": "Sitewide CTR Trend",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 24, "x": 0, "y": 17 },
      "datasource": {
        "type": "grafana-postgresql-datasource",
        "uid": "local-brain-db"
      },
      "fieldConfig": { "defaults": { "unit": "percentunit" }, "overrides": [] },
      "targets": [
        {
          "refId": "A",
          "format": "time_series",
          "editorMode": "code",
          "datasource": {
            "type": "grafana-postgresql-datasource",
            "uid": "local-brain-db"
          },
          "rawSql": "SELECT date_trunc('day', measured_at) AS time, SUM(google_clicks)::float / NULLIF(SUM(google_impressions), 0) AS ctr FROM post_performance WHERE measured_at > NOW() - INTERVAL '30 days' GROUP BY 1 ORDER BY 1"
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Lint the panels**

Run (against the local stack DB — adjust DSN/port to match your stack, e.g. 15432):

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:15432/poindexter_brain python scripts/ci/grafana_panels_lint.py infrastructure/grafana/dashboards/seo-harvest.json
```

Expected: `0 fail` for the seo-harvest panels (the table `seo_opportunities` must exist — apply the Task 1 migration to the target DB first). Warnings on macros are acceptable (CI is soft-fail).

- [ ] **Step 3: Commit**

```bash
git add infrastructure/grafana/dashboards/seo-harvest.json
git commit -F - <<'EOF'
feat(seo): SEO Harvest Grafana dashboard (tiers + top opportunities + CTR)
EOF
```

---

### Task 7: Docs

**Files:**

- Create: `docs/architecture/seo-harvest-loop.md`

- [ ] **Step 1: Write a short operator/dev doc**

```markdown
# SEO Harvest Loop

The harvest loop turns Search Console data we already collect into a ranked
"fix these posts" list. **Phase 1 (this doc) is read-only** — it classifies
published posts into opportunity tiers and writes them to `seo_opportunities`.
No content is modified. See the design spec:
`docs/superpowers/specs/2026-06-11-seo-harvest-loop-design.md`.

## Tiers

- **page1_push** — ranks position 3–10 with real impressions; one optimization
  from page 1. The fastest wins.
- **striking_distance** — ranks position 5–20; on page 2, close.
- **low_ctr** — ranks but the title/meta isn't earning the click.

## How it runs

`RunSeoOpportunityAnalyzerJob` (`services/jobs/run_seo_opportunity_analyzer.py`)
runs daily, reads the latest `post_performance` snapshot per published post,
classifies via `services/seo/striking_distance.py`, and upserts
`seo_opportunities` (one row per post, recomputed each run). A findings summary
fires when page-1-push candidates exist.

## Tuning (all `app_settings`)

`seo.harvest.analyzer_enabled` (default `true`, read-only/safe),
`seo.striking_distance.position_min/max`, `seo.push_candidate.*`,
`seo.low_ctr.*`, `seo.opportunity.target_ctr` (gap-score target CTR).

## Where to look

Grafana → **SEO Harvest** dashboard (`/d/seo-harvest`): tier counts, the
ranked top-opportunities table, and the sitewide CTR trend.
```

- [ ] **Step 2: Commit**

```bash
git add docs/architecture/seo-harvest-loop.md
git commit -F - <<'EOF'
docs(seo): add SEO Harvest Loop Phase 1 operator/dev doc
EOF
```

---

### Task 8 (optional / splittable): GSC query-dimension ingestion (1a)

> This task is **independent** of the value above — the analyzer ships without
> it. It carries operational risk (row-volume, possibly re-auth) and its
> implementation depends on HOW GSC is actually ingested, which must be
> verified first. If it turns out to need scope/auth changes, split it into its
> own PR and ship Tasks 1–7 first.

- [ ] **Step 1: Verify the GSC ingestion path**

```bash
# Is GSC a tap row, or the backfill job?
```

Run (against the stack DB):

```bash
DATABASE_URL=... python -c "import asyncio,asyncpg,os; \
print(asyncio.run((lambda d: asyncpg.connect(d))(os.environ['DATABASE_URL'])))" 2>/dev/null || true
```

Better: inspect via the read-only query path —
`SELECT name, handler_name, record_handler, config FROM external_taps WHERE name ILIKE '%search%console%' OR config::text ILIKE '%search-console%';`
and read `services/jobs/backfill_post_performance_gsc.py` to see whether it
requests dimensions. **Determine: tap-config change vs job-code change.**

- [ ] **Step 2: Add the `query` (and `page`) dimension**

- If a **tap row** drives it: write a migration that `UPDATE external_taps SET config = jsonb_set(...)` to add `query`,`page` to `tap_config` dimensions AND to `metrics_mapping.<stream>.dimension_fields` (the writer at `tap_external_metrics_writer.py` already lands arbitrary `dimension_fields`).
- If the **backfill job** drives it: modify the GSC API request in `backfill_post_performance_gsc.py` to include `query` in its `dimensions`, and ensure the row write carries `dimensions.query`.

- [ ] **Step 3: Add a retention policy for the higher-volume query rows**

Query-grain rows are page×query×day (much larger than page×day). Add a
`retention_policies` row (`ttl_prune`, `age_column='date'`, e.g. `ttl_days=180`)
scoped via `filter_sql` to GSC query rows, mirroring the Task 1 retention seed.

- [ ] **Step 4: Verify + commit** (migrations_smoke; confirm new rows carry `dimensions->>'query'` after a tap/job run).

---

### Task 9: Full verification + PR

- [ ] **Step 1: Run the full backend suite**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/ -q
cd src/cofounder_agent && poetry run pytest tests/integration_db/ -m integration_db -q
```

Expected: all green (integration_db SKIPs if no live DB — run against the stack DB).

- [ ] **Step 2: Lint / type / migration gates**

```bash
npm run lint
cd src/cofounder_agent && poetry run mypy services/seo/ services/jobs/run_seo_opportunity_analyzer.py
python scripts/ci/migrations_smoke.py && python scripts/ci/migrations_lint.py
```

Expected: clean (fix anything that isn't).

- [ ] **Step 3: Push the branch**

```bash
git push -u origin claude/objective-dirac-aa0567
```

- [ ] **Step 4: Open the PR against `origin` (Glad-Labs/glad-labs-stack)**

```bash
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "feat(seo): SEO Harvest Loop — Phase 1 (read-only opportunity analyzer)" \
  --body "Implements Phase 1 of docs/superpowers/specs/2026-06-11-seo-harvest-loop-design.md. Read-only: classifies published posts into page1_push / striking_distance / low_ctr from the latest GSC snapshot, writes seo_opportunities, surfaces them on the SEO Harvest Grafana dashboard, and fires a findings summary. Zero content mutation. Prod context: ~72 striking-distance / 24 page-1-push posts today.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Watch CI; merge when green** (per the CI-is-the-gate convention).

---

## Self-review against the spec

- **§3 1a (query ingestion)** → Task 8 (split-able; verify-first). ✅ covered, flagged as independent.
- **§3 1b (analyzer + seo_opportunities + findings + panel)** → Tasks 1,2,3,5,6. ✅
- **§5 data model (`seo_opportunities`)** → Task 1 (all columns present). ✅ The `task_type`/`pipeline_templates` items are Phase 2 — out of scope here. ✅
- **§6 config keys** → Task 4. (Renamed master to `seo.harvest.analyzer_enabled` for the read-only gate + kept `seo.refresh.enabled` for Phase 2 — deliberate; documented.) ✅
- **§7 observability** → Task 6 dashboard. ✅
- **§8 testing** → unit classifier (Task 2) + integration_db analyzer (Task 3) + job/registry (Task 5). Outcome-tracking delta math is Phase 2c — not in Phase 1. ✅
- **Placement deviation**: analyzer in `services/seo/` (substrate), not `modules/content/`, to avoid a new substrate→`modules.content` import tripping the line-keyed kernel-purity lint. Phase-2 content-mutating atoms still target `modules/content/atoms/`. ✅ documented.

No placeholders. Types consistent (`Opportunity`, `DEFAULT_THRESHOLDS`, `analyze`/`upsert_opportunities`/`analyze_and_upsert`, `_thresholds`, `RunSeoOpportunityAnalyzerJob`) across tasks.

```

```
