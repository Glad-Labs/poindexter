# Topic Sourcing — Taps Ingest, Orchestration Selects (b1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a niche-tagged `topic_pool`, make topic taps actually store into it with niche context, and make `web_search` return niche-relevant results — all running in parallel with the live orchestrator so the pool can be validated before any cutover.

**Architecture:** Topic taps become niche-bound `external_taps` rows (`handler_name='builtin_topic_source'`, `tap_type=<source>`, `target_table='topic_pool'`, `niche_id`). The rewritten `tap.builtin_topic_source` handler dispatches a single source with full niche context, dedups, and inserts into `topic_pool`. `web_search` resolves queries from niche context. The orchestrator (`TopicBatchService`) is untouched in b1 — it keeps using `_discover_external`, so the tap path and the live path run side-by-side.

**Tech Stack:** Python 3.12, asyncpg, pytest, the `external_taps` declarative data plane (`tap_runner` + handler registry), the `topic_sources` plugin library, `get_deduplicator` (word-overlap/semantic).

**Spec:** `docs/superpowers/specs/2026-06-14-topic-sourcing-taps-design.md`
**Branch:** `claude/topic-pool-ingestion` (b1 ships as one PR off this branch against `Glad-Labs/glad-labs-stack`).

---

## Scope

This plan covers **b1 only** — the first independently-shippable, testable increment. b2 (orchestrator pool-reader cutover) and b3 (retire `TopicDiscovery`) get their own plans once b1 lands, because their diffs depend on b1's final shapes. A task-level roadmap for both is at the end.

## File Structure (b1)

| File                                                                                  | Responsibility                                                                                                        | Create/Modify    |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ---------------- |
| `src/cofounder_agent/services/migrations/<ts>_create_topic_pool.py`                   | DDL: `topic_pool` table + indexes; add `external_taps.niche_id` column                                                | Create           |
| `src/cofounder_agent/services/migrations/<ts>_promote_niche_sources_to_topic_taps.py` | Data: delete 5 global topic taps, promote each `niche_sources` row → niche-bound tap (derive `web_search` categories) | Create           |
| `src/cofounder_agent/services/topic_pool.py`                                          | `topic_pool` data access: `dedup_key()`, `insert_pooled_topics()`                                                     | Create           |
| `src/cofounder_agent/services/topic_sources/web_search.py`                            | Niche-aware query resolution (§2b)                                                                                    | Modify           |
| `src/cofounder_agent/services/integrations/handlers/tap_builtin_topic_source.py`      | Single-source dispatch + niche context + dedup + insert into pool                                                     | Modify (rewrite) |
| `tests/unit/services/test_topic_pool.py`                                              | `dedup_key` + `insert_pooled_topics` (ON CONFLICT)                                                                    | Create           |
| `tests/unit/services/test_topic_sources_web_search.py`                                | Niche-aware resolution cases                                                                                          | Modify (extend)  |
| `tests/unit/services/integrations/handlers/test_tap_builtin_topic_source.py`          | Handler contract                                                                                                      | Create           |
| `tests/unit/services/migrations/test_promote_niche_sources_to_topic_taps.py`          | `_derive_categories` helper                                                                                           | Create           |

Generate the two migration filenames with `python scripts/new-migration.py "<slug>"` (UTC-timestamped, per Glad-Labs/poindexter#378) — do **not** hand-name them. Wherever this plan writes `<ts>_create_topic_pool.py` etc., substitute the generated filename.

---

## Task 1: Schema — `topic_pool` table + `external_taps.niche_id`

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_create_topic_pool.py` (generate via `scripts/new-migration.py "create topic_pool table and external_taps niche_id"`)
- Test: verified by `scripts/ci/migrations_smoke.py` (DDL has no unit test; the data migration in Task 5 gets the unit test)

- [ ] **Step 1: Generate the migration file**

Run: `python scripts/new-migration.py "create topic_pool table and external_taps niche_id"`
Expected: prints the created path, e.g. `services/migrations/20260614_HHMMSS_create_topic_pool_table_and_external_taps_niche_id.py`. Note the exact name; use it for the rest of this task.

- [ ] **Step 2: Write the migration body**

Replace the generated stub with (keep the generator's real timestamp in the docstring):

```python
"""Migration <ts>_create_topic_pool: niche-tagged candidate pool + external_taps.niche_id

Spec: docs/superpowers/specs/2026-06-14-topic-sourcing-taps-design.md (b1).

topic_pool is the decoupling seam between ingestion (taps) and orchestration
(TopicBatchService). One row per (niche, candidate); status walks
pooled -> batched -> expired. external_taps gains a nullable niche_id so a tap
row can bind to a niche (NULL for non-topic taps like corsair_csv).

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS topic_pool (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id     UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    score        DOUBLE PRECISION NOT NULL DEFAULT 0,
    dedup_key    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pooled',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batched_at   TIMESTAMPTZ
);
"""

# One explicit string per item — no adjacent-literal concatenation (CodeQL
# py/implicit-string-concatenation-in-list hides missing commas).
_CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_topic_pool_niche_dedup ON topic_pool (niche_id, dedup_key);",
    "CREATE INDEX IF NOT EXISTS idx_topic_pool_niche_status ON topic_pool (niche_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_topic_pool_ingested_at ON topic_pool (ingested_at);",
]

_ADD_NICHE_ID = (
    "ALTER TABLE external_taps "
    "ADD COLUMN IF NOT EXISTS niche_id UUID REFERENCES niches(id) ON DELETE CASCADE;"
)

_CREATE_TAPS_NICHE_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_external_taps_niche_id "
    "ON external_taps (niche_id) WHERE niche_id IS NOT NULL;"
)


async def up(pool) -> None:
    """Create topic_pool + indexes and add external_taps.niche_id."""
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await conn.execute(idx_sql)
        await conn.execute(_ADD_NICHE_ID)
        await conn.execute(_CREATE_TAPS_NICHE_IDX)
    logger.info(
        "Migration create_topic_pool: table + %d indexes + external_taps.niche_id",
        len(_CREATE_INDEXES),
    )


async def down(pool) -> None:
    """Drop topic_pool and the external_taps.niche_id column."""
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE external_taps DROP COLUMN IF EXISTS niche_id")
        await conn.execute("DROP TABLE IF EXISTS topic_pool")
    logger.info("Migration create_topic_pool down: reverted")
```

- [ ] **Step 3: Lint the migration**

Run: `python scripts/ci/migrations_lint.py`
Expected: PASS (no collisions, `up`/`down` present).

- [ ] **Step 4: Smoke-test against a fresh DB**

Run: `python scripts/ci/migrations_smoke.py`
Expected: PASS — migration applies cleanly; `topic_pool` exists and `external_taps.niche_id` is present.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/<ts>_create_topic_pool.py
git commit -m "feat(topic-pool): add topic_pool table + external_taps.niche_id (b1)"
```

---

## Task 2: `topic_pool` data-access module

**Files:**

- Create: `src/cofounder_agent/services/topic_pool.py`
- Test: `src/cofounder_agent/tests/unit/services/test_topic_pool.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for services/topic_pool.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.topic_source import DiscoveredTopic
from services.topic_pool import dedup_key, insert_pooled_topics


def test_dedup_key_normalizes_title():
    # case-insensitive + whitespace-collapsed so trivial variants collide
    assert dedup_key("  Local  LLM   Inference ") == dedup_key("local llm inference")
    assert dedup_key("A") != dedup_key("B")


def _conn_returning(ids):
    """Conn whose fetchval pops successive return values (id or None)."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=list(ids))
    return conn


@pytest.mark.asyncio
async def test_insert_counts_only_new_rows():
    # First insert returns an id (new), second returns None (ON CONFLICT no-op).
    conn = _conn_returning(["11111111-1111-1111-1111-111111111111", None])
    topics = [
        DiscoveredTopic(title="One", category="tech", source="web_search",
                        source_url="https://x/1", relevance_score=2.0, description="d1"),
        DiscoveredTopic(title="Two", category="tech", source="web_search"),
    ]
    n = await insert_pooled_topics(
        conn, niche_id="22222222-2222-2222-2222-222222222222",
        source="web_search", topics=topics,
    )
    assert n == 1
    assert conn.fetchval.await_count == 2
    # First positional after SQL is niche_id; title is mapped from DiscoveredTopic.
    first = conn.fetchval.await_args_list[0]
    assert "INSERT INTO topic_pool" in first.args[0]
    assert "ON CONFLICT (niche_id, dedup_key) DO NOTHING" in first.args[0]


@pytest.mark.asyncio
async def test_insert_rejects_unknown_table():
    conn = _conn_returning([])
    with pytest.raises(ValueError):
        await insert_pooled_topics(
            conn, niche_id="x", source="web_search", topics=[], table="pipeline_tasks",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_pool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.topic_pool'`.

- [ ] **Step 3: Write the implementation**

```python
"""topic_pool — data access for the niche-tagged candidate pool.

The decoupling seam between ingestion (taps) and orchestration
(TopicBatchService). Taps insert here via insert_pooled_topics(); b2's
orchestrator reads pooled rows and flips them to 'batched'.
"""

from __future__ import annotations

from typing import Any

from plugins.topic_source import DiscoveredTopic

# Tap rows name their destination in external_taps.target_table; we read it
# from the (trusted) row but allowlist it before interpolation — asyncpg can't
# parametrize a table name, and defense-in-depth beats trusting the column.
_ALLOWED_TABLES = frozenset({"topic_pool"})


def dedup_key(title: str) -> str:
    """Canonical per-niche dedup key: lowercased, whitespace-collapsed title.

    Backs the UNIQUE(niche_id, dedup_key) constraint so trivial title
    variants ("Local  LLM " vs "local llm") collapse to one pool row. The
    fuzzy/semantic pass in the tap handler catches near-dupes this exact key
    misses.
    """
    return " ".join((title or "").lower().split())


async def insert_pooled_topics(
    conn: Any,
    *,
    niche_id: Any,
    source: str,
    topics: list[DiscoveredTopic],
    table: str = "topic_pool",
) -> int:
    """Insert candidates into the pool, skipping per-niche dedup_key dupes.

    Returns the count of rows actually inserted (RETURNING id is NULL on an
    ON CONFLICT no-op). ``table`` is read from the tap's target_table and
    must be in the allowlist.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"insert_pooled_topics: refusing unknown table {table!r}")

    sql = (
        f"INSERT INTO {table} "  # noqa: S608 — table is allowlisted above
        "(niche_id, source, title, summary, url, category, score, dedup_key, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pooled') "
        "ON CONFLICT (niche_id, dedup_key) DO NOTHING "
        "RETURNING id"
    )
    inserted = 0
    for t in topics:
        new_id = await conn.fetchval(
            sql,
            niche_id,
            source,
            t.title,
            getattr(t, "description", "") or "",
            getattr(t, "source_url", "") or "",
            getattr(t, "category", "") or "",
            float(getattr(t, "relevance_score", 0.0) or 0.0),
            dedup_key(t.title),
        )
        if new_id is not None:
            inserted += 1
    return inserted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_pool.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_pool.py src/cofounder_agent/tests/unit/services/test_topic_pool.py
git commit -m "feat(topic-pool): add insert_pooled_topics + dedup_key (b1)"
```

---

## Task 3: Niche-aware `web_search` query resolution (§2b)

**Files:**

- Modify: `src/cofounder_agent/services/topic_sources/web_search.py`
- Test: `src/cofounder_agent/tests/unit/services/test_topic_sources_web_search.py` (extend)

- [ ] **Step 1: Write the failing tests** (append to the existing test file's `TestWebSearchSource` area)

```python
    @pytest.mark.asyncio
    async def test_explicit_seed_queries_win(self):
        fake = _make_researcher({"my exact query": [
            {"title": "An article about the exact pinned query topic", "url": "https://x/1"},
        ]})
        with patch("services.web_research.WebResearcher", return_value=fake):
            source = WebSearchSource()
            topics = await source.extract(
                pool=None,
                config={"seed_queries": ["my exact query"]},
            )
        assert len(topics) == 1
        fake.search_simple.assert_awaited_with("my exact query", num_results=3)

    @pytest.mark.asyncio
    async def test_niche_tags_derive_queries_when_no_config(self):
        # No seed_queries, no categories — derive from niche name + tags.
        captured: list[str] = []

        async def _search(query, num_results=3):
            captured.append(query)
            return [{"title": f"A readable article about {query} in depth", "url": "https://x/1"}]

        fake = MagicMock()
        fake.search_simple = AsyncMock(side_effect=_search)
        with patch("services.web_research.WebResearcher", return_value=fake):
            source = WebSearchSource()
            topics = await source.extract(
                pool=None,
                config={
                    "niche_name": "PC Gaming",
                    "target_audience_tags": ["esports", "gpu overclocking"],
                },
            )
        # One query per tag, niche name folded in for topical scoping.
        assert captured == ["PC Gaming esports", "PC Gaming gpu overclocking"]
        assert len(topics) == 2

    @pytest.mark.asyncio
    async def test_two_niches_get_different_queries(self):
        seen: list[str] = []

        async def _search(query, num_results=3):
            seen.append(query)
            return []

        fake = MagicMock()
        fake.search_simple = AsyncMock(side_effect=_search)
        with patch("services.web_research.WebResearcher", return_value=fake):
            source = WebSearchSource()
            await source.extract(pool=None, config={"niche_name": "AI/ML", "target_audience_tags": ["llms"]})
            await source.extract(pool=None, config={"niche_name": "PC Gaming", "target_audience_tags": ["esports"]})
        assert seen == ["AI/ML llms", "PC Gaming esports"]

    @pytest.mark.asyncio
    async def test_no_config_and_no_niche_fails_loud(self):
        fake = _make_researcher([])
        with patch("services.web_research.WebResearcher", return_value=fake):
            source = WebSearchSource()
            with pytest.raises(ValueError):
                await source.extract(pool=None, config={})
        fake.search_simple.assert_not_awaited()
```

Note: the existing `test_empty_categories_no_calls` asserts `extract(config={})` returns `[]`. That behavior is **changing** — an empty config with no niche context now fails loud (retiring the silent global-bank fallback). Update that existing test: replace its body with the `pytest.raises(ValueError)` assertion from `test_no_config_and_no_niche_fails_loud` (or delete it, since the new test supersedes it). Leave the category-path tests (`test_yields_topics_from_search_results`, `test_max_categories_cap`, etc.) unchanged — they pass `config={"categories": [...]}` so they still hit the category path.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_sources_web_search.py -v`
Expected: the four new tests FAIL (seed_queries ignored, tag derivation absent, no ValueError raised).

- [ ] **Step 3: Implement niche-aware resolution**

Replace the query-resolution portion of `WebSearchSource.extract`. The new shape — a `_resolve_queries` helper plus a flat search loop over `(query, category_label)` pairs:

```python
    async def extract(
        self,
        pool: Any,  # unused — WebResearcher owns its own HTTP client
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        del pool

        from services.site_config import SiteConfig
        from services.web_research import WebResearcher

        results_per_query = int(config.get("results_per_query", 3) or 3)
        relevance_score = float(config.get("relevance_score", 2.0) or 2.0)
        max_queries = int(config.get("max_categories_per_run", 3) or 3)

        plan = self._resolve_queries(config)[:max_queries]
        if not plan:
            # Niche-aware resolution found nothing AND no explicit config was
            # given. Fail loud rather than silently searching every global
            # category (the retired pre-niche behaviour). feedback_no_silent_defaults.
            raise ValueError(
                "web_search: no seed_queries, no categories, and no niche "
                "target_audience_tags to derive queries from — refusing to "
                "fall back to a global all-category search"
            )

        researcher = WebResearcher(
            site_config=config.get("_site_config") or SiteConfig()
        )

        topics: list[DiscoveredTopic] = []
        for query, category_label in plan:
            results = await researcher.search_simple(query, num_results=results_per_query)
            for r in results or []:
                title = r.get("title", "")
                if not title:
                    continue
                rewritten = rewrite_as_blog_topic(title)
                if not rewritten:
                    continue
                topics.append(
                    DiscoveredTopic(
                        title=rewritten,
                        category=category_label,
                        source="ddg_search",
                        source_url=r.get("url", ""),
                        relevance_score=relevance_score,
                    )
                )

        logger.info(
            "WebSearchSource: %d topics across %d queries", len(topics), len(plan),
        )
        return topics

    @staticmethod
    def _resolve_queries(config: dict[str, Any]) -> list[tuple[str, str]]:
        """Resolve (query, category_label) pairs, first match wins (§2b).

        1. explicit config.seed_queries  -> pinned, label 'custom'
        2. explicit config.categories    -> bank queries, label = category
        3. niche target_audience_tags    -> '{niche_name} {tag}', label = tag
        4. nothing                        -> [] (caller fails loud)
        """
        import random

        from services.topic_sources._filters import CATEGORY_SEARCHES

        seed_queries = config.get("seed_queries")
        if isinstance(seed_queries, list) and seed_queries:
            return [(str(q), "custom") for q in seed_queries]

        categories = config.get("categories")
        if isinstance(categories, list) and categories:
            plan: list[tuple[str, str]] = []
            for cat in categories:
                queries = CATEGORY_SEARCHES.get(cat, [])
                if queries:
                    plan.append((random.choice(queries), cat))
            return plan

        tags = config.get("target_audience_tags")
        niche_name = (config.get("niche_name") or "").strip()
        if isinstance(tags, list) and tags and niche_name:
            return [(f"{niche_name} {tag}", str(tag)) for tag in tags]

        return []
```

(Keep the module's existing imports of `rewrite_as_blog_topic`, `logging`, `DiscoveredTopic`; the top-level `import random` can be dropped since it now lives in `_resolve_queries`, or left — harmless.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_sources_web_search.py -v`
Expected: PASS (new niche-aware tests + the unchanged category-path tests; the updated `test_empty_categories_no_calls`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_sources/web_search.py src/cofounder_agent/tests/unit/services/test_topic_sources_web_search.py
git commit -m "feat(web_search): niche-aware query resolution; retire global-bank fallback (b1)"
```

---

## Task 4: Rewrite `tap.builtin_topic_source` to store into the pool

**Files:**

- Modify (rewrite): `src/cofounder_agent/services/integrations/handlers/tap_builtin_topic_source.py`
- Test: `src/cofounder_agent/tests/unit/services/integrations/handlers/test_tap_builtin_topic_source.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the rewritten tap.builtin_topic_source handler (b1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import DiscoveredTopic
from services.integrations.handlers.tap_builtin_topic_source import builtin_topic_source


def _make_pool():
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _niche():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        slug="pc-gaming",
        name="PC Gaming",
        target_audience_tags=["esports"],
    )


@pytest.mark.asyncio
async def test_requires_niche_id():
    pool, _ = _make_pool()
    with pytest.raises(ValueError):
        await builtin_topic_source(
            None, site_config=MagicMock(),
            row={"tap_type": "web_search", "target_table": "topic_pool"},
            pool=pool,
        )


@pytest.mark.asyncio
async def test_dispatches_single_source_with_niche_context_and_inserts():
    pool, _ = _make_pool()
    src = MagicMock()
    src.name = "web_search"
    src.extract = AsyncMock(return_value=[
        DiscoveredTopic(title="GPU news", category="esports", source="ddg_search"),
    ])

    captured_cfg = {}

    async def _extract(pool_arg, cfg):
        captured_cfg.update(cfg)
        return src.extract.return_value
    src.extract.side_effect = _extract

    with patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_topic_sources",
        return_value=[src],
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.NicheService",
    ) as NS, patch(
        "services.integrations.handlers.tap_builtin_topic_source.PluginConfig.load",
        AsyncMock(return_value=SimpleNamespace(config={})),
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_deduplicator",
    ) as GD, patch(
        "services.integrations.handlers.tap_builtin_topic_source.insert_pooled_topics",
        AsyncMock(return_value=1),
    ) as INS:
        NS.return_value.get_by_id = AsyncMock(return_value=_niche())
        GD.return_value.mark_duplicates = AsyncMock(return_value=None)
        result = await builtin_topic_source(
            None, site_config=MagicMock(),
            row={
                "tap_type": "web_search", "target_table": "topic_pool",
                "niche_id": _niche().id, "config": {"categories": ["gaming"]},
            },
            pool=pool,
        )

    assert result == {"records": 1, "source": "web_search"}
    # Niche context reached the source.
    assert captured_cfg["niche_slug"] == "pc-gaming"
    assert captured_cfg["niche_name"] == "PC Gaming"
    assert captured_cfg["target_audience_tags"] == ["esports"]
    # Tap config (categories) layered in.
    assert captured_cfg["categories"] == ["gaming"]
    INS.assert_awaited_once()


@pytest.mark.asyncio
async def test_unregistered_source_fails_loud():
    pool, _ = _make_pool()
    with patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_topic_sources",
        return_value=[],
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.NicheService",
    ) as NS:
        NS.return_value.get_by_id = AsyncMock(return_value=_niche())
        with pytest.raises(ValueError):
            await builtin_topic_source(
                None, site_config=MagicMock(),
                row={"tap_type": "nope", "target_table": "topic_pool", "niche_id": _niche().id},
                pool=pool,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_tap_builtin_topic_source.py -v`
Expected: FAIL (handler still calls `run_all`; the patched symbols don't exist there yet).

- [ ] **Step 3: Rewrite the handler**

```python
"""Handler: ``tap.builtin_topic_source`` (b1 rewrite).

Dispatches the single ``topic_source`` plugin named in ``row.tap_type``
with full niche context, dedups, and INSERTs the survivors into the tap's
``target_table`` (``topic_pool``). This is the per-source loop body lifted
from ``TopicBatchService._discover_external`` — b2 deletes that method, so
keeping the logic identical makes the deletion a move, not a rewrite.

The pre-b1 version delegated to ``topic_sources.runner.run_all`` and threw
the topics away (returned only a count). That hollow path is gone.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.config import PluginConfig
from plugins.registry import get_topic_sources
from services.integrations.registry import register_handler
from services.niche_service import NicheService
from services.topic_dedup_semantic import get_deduplicator
from services.topic_pool import insert_pooled_topics

logger = logging.getLogger(__name__)


@register_handler("tap", "builtin_topic_source")
async def builtin_topic_source(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Run one niche-bound topic source and store its candidates in the pool."""
    if pool is None:
        raise RuntimeError("tap.builtin_topic_source: pool unavailable")

    niche_id = row.get("niche_id")
    if not niche_id:
        raise ValueError(
            "tap.builtin_topic_source: topic taps require a niche_id "
            "(this tap row has none). feedback_no_silent_defaults."
        )

    source_name = row.get("tap_type")
    if not source_name:
        raise ValueError(
            "tap.builtin_topic_source: row.tap_type must name a registered "
            "topic_source plugin (e.g. 'hackernews', 'web_search')"
        )

    niche = await NicheService(pool).get_by_id(niche_id)
    if niche is None:
        raise ValueError(f"tap.builtin_topic_source: unknown niche_id {niche_id}")

    # Resolve the single source. internal_rag isn't an entry-point plugin —
    # branch to its service class (same as _discover_internal does).
    if source_name == "internal_rag":
        from services.internal_rag_source import InternalRagSource

        source: Any = InternalRagSource(pool, site_config=site_config)
    else:
        registry = {
            getattr(p, "name", type(p).__name__): p for p in get_topic_sources()
        }
        source = registry.get(source_name)
        if source is None:
            raise ValueError(
                f"tap.builtin_topic_source: source {source_name!r} is not a "
                "registered topic_source plugin — check install or rename"
            )

    # Build extract_config exactly as _discover_external does: per-install
    # plugin config, then the tap row's own config (e.g. seeded categories),
    # then the niche context the source needs to scope its output.
    plugin_cfg = await PluginConfig.load(pool, "topic_source", source_name)
    extract_config: dict[str, Any] = dict(plugin_cfg.config)
    extract_config.update(dict(row.get("config") or {}))
    extract_config.update(
        {
            "_site_config": site_config,
            "niche_slug": niche.slug,
            "niche_id": str(niche.id),
            "niche_name": niche.name,
            "target_audience_tags": list(niche.target_audience_tags),
        }
    )

    topics = await source.extract(pool, extract_config)

    # Fuzzy/semantic dedup (honours topic_dedup_engine). DiscoveredTopic
    # already exposes .title + .is_duplicate, so the deduper marks in place.
    if topics:
        deduper = get_deduplicator(pool, site_config=site_config)
        try:
            await deduper.mark_duplicates(topics)
        except Exception:
            logger.warning(
                "tap.builtin_topic_source: dedup pass failed — proceeding "
                "with un-deduped candidates",
                exc_info=True,
            )
    fresh = [t for t in (topics or []) if not getattr(t, "is_duplicate", False)]

    target_table = row.get("target_table") or "topic_pool"
    async with pool.acquire() as conn:
        inserted = await insert_pooled_topics(
            conn,
            niche_id=niche.id,
            source=source_name,
            topics=fresh,
            table=target_table,
        )

    logger.info(
        "[tap.builtin_topic_source] %s/%s: %d pooled (%d fetched, %d after dedup)",
        niche.slug, source_name, inserted, len(topics or []), len(fresh),
    )
    return {"records": inserted, "source": source_name}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_tap_builtin_topic_source.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Check for now-orphaned tests of the old behavior**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_tap_framework.py -v`
Expected: PASS. If a test there asserted the old `run_all`-delegating behavior of `builtin_topic_source` (returning a count without storing), update it to the new contract or move it here. Do not leave a test asserting the deleted behavior.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/integrations/handlers/tap_builtin_topic_source.py src/cofounder_agent/tests/unit/services/integrations/handlers/test_tap_builtin_topic_source.py
git commit -m "feat(taps): builtin_topic_source stores into topic_pool with niche context (b1)"
```

---

## Task 5: Migration — promote `niche_sources` → niche-bound taps

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_promote_niche_sources_to_topic_taps.py` (generate via `scripts/new-migration.py`)
- Test: `src/cofounder_agent/tests/unit/services/migrations/test_promote_niche_sources_to_topic_taps.py`

- [ ] **Step 1: Generate the migration file**

Run: `python scripts/new-migration.py "promote niche_sources to niche-bound topic taps"`
Expected: prints the created path. Note the exact name.

- [ ] **Step 2: Write the failing test for the category-derivation helper**

```python
"""Unit test for the _derive_categories helper in the promote migration."""

from __future__ import annotations

import importlib

# Import by the generated module name — adjust to the actual filename (drop .py).
_mod = importlib.import_module(
    "services.migrations.<ts>_promote_niche_sources_to_topic_taps"
)


def test_web_search_matches_bank_keys_by_slug_and_tags():
    # 'gaming' slug token matches bank key 'gaming'; 'hardware' tag matches 'hardware'.
    cats = _mod._derive_categories("web_search", "gaming", ["pc hardware"])
    assert cats == ["gaming", "hardware"]


def test_ai_ml_niche_has_no_bank_match_falls_through_to_tags():
    # No bank key for ai/ml -> empty -> handler/web_search uses tag-derived path.
    assert _mod._derive_categories("web_search", "ai-ml", ["llms", "agents"]) == []


def test_non_web_search_sources_get_no_categories():
    assert _mod._derive_categories("hackernews", "gaming", ["esports"]) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/migrations/test_promote_niche_sources_to_topic_taps.py -v`
Expected: FAIL (`_derive_categories` not defined / module body still the stub).

- [ ] **Step 4: Write the migration body**

```python
"""Migration <ts>_promote_niche_sources_to_topic_taps: niche-bound topic taps

Spec: docs/superpowers/specs/2026-06-14-topic-sourcing-taps-design.md (b1).

Two moves, lossless:
  1. delete the pre-existing GLOBAL builtin_topic_source rows (niche_id IS NULL,
     target_table='content_tasks') — they store nothing today, so nothing is lost.
  2. promote each niche_sources row -> one niche-bound external_taps tap
     (target_table='topic_pool', niche_id set, weight_pct + derived web_search
     categories in config).

_BANK_KEYS is a one-time snapshot of services.topic_sources._filters.CATEGORY_SEARCHES
keys at migration-authoring time — inlined to keep this migration light-import
(migrations-smoke) and self-contained. web_search's runtime resolution is the
live source of truth; this only seeds a starting config.categories.

Light imports only: json, logging, re stdlib.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Snapshot of CATEGORY_SEARCHES.keys() at authoring time (2026-06-14).
_BANK_KEYS = frozenset(
    {"technology", "startup", "security", "engineering", "insights",
     "business", "hardware", "gaming"}
)


def _derive_categories(source_name: str, slug: str, tags) -> list[str]:
    """web_search only: bank categories whose key matches a slug/tag token.

    Generic (works for any operator's niches): tokenize slug + tags on
    -/_/whitespace, intersect with the bank keys. Empty -> web_search falls
    through to its tag-derived path (§2b).
    """
    if source_name != "web_search":
        return []
    tokens: set[str] = set(re.split(r"[-_\s]+", (slug or "").lower()))
    for tag in tags or []:
        tokens |= set(re.split(r"[-_\s]+", str(tag).lower()))
    return sorted(tokens & _BANK_KEYS)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps "
            "WHERE handler_name = 'builtin_topic_source' AND niche_id IS NULL"
        )
        rows = await conn.fetch(
            """
            SELECT ns.niche_id, ns.source_name, ns.enabled, ns.weight_pct,
                   n.slug, n.name, n.target_audience_tags,
                   n.discovery_cadence_minute_floor AS floor
              FROM niche_sources ns
              JOIN niches n ON n.id = ns.niche_id
            """
        )
        promoted = 0
        for r in rows:
            cfg: dict = {"weight_pct": r["weight_pct"]}
            cats = _derive_categories(r["source_name"], r["slug"], r["target_audience_tags"])
            if cats:
                cfg["categories"] = cats
            await conn.execute(
                """
                INSERT INTO external_taps
                    (name, handler_name, tap_type, target_table, niche_id,
                     schedule, config, enabled, metadata)
                VALUES ($1, 'builtin_topic_source', $2, 'topic_pool', $3,
                        $4, $5::jsonb, $6, $7::jsonb)
                """,
                f"{r['slug']}_{r['source_name']}",
                r["source_name"],
                r["niche_id"],
                f"every {r['floor']} minutes",
                json.dumps(cfg),
                r["enabled"],
                json.dumps({"description": f"{r['source_name']} topics for the {r['name']} niche"}),
            )
            promoted += 1
    logger.info("Migration promote_niche_sources_to_topic_taps: promoted %d tap(s)", promoted)


async def down(pool) -> None:
    """Remove the niche-bound topic taps (leaves niche_sources intact)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps "
            "WHERE handler_name = 'builtin_topic_source' AND niche_id IS NOT NULL"
        )
    logger.info("Migration promote_niche_sources_to_topic_taps down: reverted")
```

- [ ] **Step 5: Run the helper test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/migrations/test_promote_niche_sources_to_topic_taps.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + smoke the migration**

Run: `python scripts/ci/migrations_lint.py && python scripts/ci/migrations_smoke.py`
Expected: PASS — both migrations apply in order against a fresh DB (the fresh DB's baseline-seeded `niche_sources` rows get promoted; the 5 global rows are deleted).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/migrations/<ts>_promote_niche_sources_to_topic_taps.py src/cofounder_agent/tests/unit/services/migrations/test_promote_niche_sources_to_topic_taps.py
git commit -m "feat(taps): promote niche_sources to niche-bound topic_pool taps (b1)"
```

---

## Task 6: Full-suite gate + manual pool-fill validation

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/ -q`
Expected: PASS — no regressions. Pay attention to `test_topic_batch_service.py` (the orchestrator still uses `_discover_external` in b1 — it must be untouched and green) and anything importing the old tap handler.

- [ ] **Step 2: Apply migrations to the dev DB**

Run: `poindexter migrate` (or the project's migration-apply command per `docs/operations/migrations.md`).
Expected: both b1 migrations apply; `topic_pool` exists; `external_taps` now has niche-bound rows (`SELECT name, tap_type, niche_id, target_table FROM external_taps WHERE handler_name='builtin_topic_source'`) and **no** `niche_id IS NULL` topic taps.

- [ ] **Step 3: Trigger the tap runner and confirm the pool fills**

Run the tap runner once (via the scheduled job or a one-shot REPL calling `services.integrations.tap_runner.run_all(pool)`), then:
Run: `SELECT niche_id, source, count(*) FROM topic_pool GROUP BY 1,2 ORDER BY 1,2;`
Expected: rows present, partitioned by niche; `web_search` candidates differ across niches (spot-check two niches' titles are not identical). No single niche dominates; dedup_key collisions are skipped (re-run the runner — counts should be stable, not doubling).

- [ ] **Step 4: Confirm the live path still works (parallel safety)**

Expected: a normal niche sweep still produces a batch via `_discover_external` (orchestrator unchanged). The pool is filling but nothing reads it yet — batches look exactly as before b1. This is the parallel-validation state the spec calls for.

- [ ] **Step 5: Open the b1 PR**

```bash
git push -u origin claude/topic-pool-ingestion
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "feat(topic-sourcing): niche-aware topic_pool + real tap ingestion (b1)" \
  --body "Implements b1 of docs/superpowers/specs/2026-06-14-topic-sourcing-taps-design.md. Pool fills in parallel with the live orchestrator; no batch behavior change yet. b2 cuts run_sweep over to read the pool."
```

Expected: CI green (required: test-backend, migrations-smoke). Merge per `feedback_ci_is_the_review_gate` once green.

---

## Subsequent phases (own plans after b1 lands)

These are intentionally task-level — their exact diffs depend on b1's merged shapes, so each gets a full bite-sized plan when it starts.

### b2 — orchestrator pool-reader cutover

- **Add `topic_pool.read_pooled(pool, niche_id) -> list`** returning rows in the `{"kind", "data": {...}}` shape `_embed_and_pre_rank` consumes (mirror `_external_title`/`_internal_title` keys). Test: shape parity with a fixture pool row.
- **Add `topic_pool.mark_batched(conn, ids)`** — flip `status='batched'`, set `batched_at`. Test: only the named ids flip.
- **Replace `_discover_external` + `_discover_internal` in `TopicBatchService.run_sweep`** with a single `_read_pool(niche)` call; on `_write_batch`, flip the chosen pool rows to `batched`. Delete both `_discover_*` methods and their now-dead helpers. Keep the #1561 dedup pass as the vs-published safety net. Tests: `run_sweep` reads pool → ranks → writes batch → flips status; empty pool → no batch (wedge guard).
- **Add the `retention_policies` row** for `topic_pool` (`handler_name='ttl_prune'`, `filter_sql="status = 'pooled'"`, `ttl_days=N`) via migration, mirroring the `seo_opportunities` retention seed. Test: stale `pooled` pruned, `batched` untouched.
- **Migration smoke + full suite + manual:** confirm batches now come from the pool and the direct path is gone.

### b3 — retire `TopicDiscovery`

- **Re-point `topic="auto"` / `POST /api/tasks/discover-topics`** (in `routes/task_routes.py`) through the niche gate: auto-topic triggers/serves a niche batch and `topic_auto_resolve` drains it — not a fire-and-forget direct queue. `scripts/daemon.py::generate_content()` becomes async.
- **Delete `services/topic_discovery.py`** (`TopicDiscovery` class, `queue_topics`, `_scrape_*`, `_deduplicate`, `_BRAND_KEYWORDS`) — ~450 LOC — plus reconcile ~800 LOC of tests (`test_topic_discovery`, `test_topic_queue_cap`, `test_topic_discovery_exception_logging`).
- **Leave `topic_sources/runner.py` in place** — it's an independent tap-framework surface, not part of the `TopicDiscovery` class.
- **Deprecate `niche_sources`** documentation (already read-by-nothing after b2).

---

## Self-Review (against the spec)

- **Spec §1 schema** → Task 1 (`topic_pool` + `niche_id`) + Task 2 (data access). `(niche_id, dedup_key)` unique → `uq_topic_pool_niche_dedup`. ✓
- **Spec §1 migration (two moves)** → Task 5 (delete 5 global rows; promote per `niche_sources`; seed `weight_pct` + derived `categories`). ✓
- **Spec §2 tap handler** → Task 4 (fail-loud on missing `niche_id`; single-source dispatch; `PluginConfig` + niche context incl. `target_audience_tags`; `get_deduplicator`; insert into `row['target_table']`; return `{"records": N}`). ✓
- **Spec §2b niche-aware `web_search`** → Task 3 (resolution order seed_queries → categories → tag-derived → fail loud; global-bank fallback retired). Both prod paths exercised: bank-categories seeded by Task 5 for matching niches, tag-derived for the rest. ✓
- **Spec §3 orchestrator** → deferred to b2 (correct — b1 keeps `_discover_external` for parallel validation). ✓
- **Spec §4 internal_rag as tap** → Task 4 branches `tap_type=internal_rag` → `InternalRagSource`. ✓
- **Spec §5 retention** → deferred to b2 (matches spec's PR placement). ✓
- **Spec testing** → Tasks 2–5 carry the per-layer contract tests the spec lists; Task 6 is the manual pool-fill + parallel-safety check. ✓
- **Type consistency:** `insert_pooled_topics(conn, *, niche_id, source, topics, table="topic_pool")` used identically in Task 2 (def) and Task 4 (call); `dedup_key(title)` single-arg in both; `_resolve_queries`/`_derive_categories` names match their tests. ✓
- **Placeholder scan:** every code step has complete code; migration filenames are generator-produced (`<ts>` substitution is an instruction, not a code placeholder). ✓

**Open refinement to confirm with Matt (non-blocking):** Task 5 seeds `config.categories` via a slug/tag∩bank-keys match using an **inlined `_BANK_KEYS` snapshot** in the migration (keeps it light-import + generic). The alternative is importing `CATEGORY_SEARCHES` keys directly; both produce the same seed. Flagged because it's the one spot the plan picks a mechanism the spec left open.
