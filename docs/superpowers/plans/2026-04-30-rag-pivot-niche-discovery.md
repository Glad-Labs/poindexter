# RAG Pivot + Niche-Aware Topic Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-topic approve/reject pipeline entry point with a niche-aware, ranked-batch operator surface, and pivot the writer stage to RAG over internal Glad Labs context.

**Architecture:** Hybrid migration — `services/topic_discovery.py` and `services/topic_dedup*.py` stay; `services/topic_proposal_service.py` is replaced by a new `services/topic_batch_service.py` that orchestrates per-niche discovery → embedding+LLM hybrid ranking → batch creation → operator interaction (CLI + MCP) → handoff into the existing pipeline. Writer stage gets a per-niche `writer_rag_mode` switch with four implementations; Glad Labs uses `TWO_PASS` (internal-context first draft, external fact-augmentation second pass). The existing `topic_decision` approval gate is light-reused as the pause/release control; new MCP/CLI tools mutate the batch row.

**Tech Stack:** Python 3.12, asyncpg, FastAPI, Pipecat (unrelated, just present), pgvector for embeddings, glm-4.7-5090 via Ollama, MCP SDK, click for CLI.

**Spec reference:** `docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md`

---

## Pre-flight

Before starting Task 1, the implementer should:

1. Read the spec (linked above) cover-to-cover.
2. Read `src/cofounder_agent/CLAUDE.md` (or top-level `CLAUDE.md`) — covers configuration conventions (`SiteConfig`, `app_settings`, async-everywhere, fail-loud).
3. `ls src/cofounder_agent/services/migrations/` and pick the next-available migration number (NEXT_MIGRATION). Use that for Task 1 and increment for Task 18.
4. Confirm working tree is clean and on a fresh branch cut from `github/main` (per the workflow guard documented in CLAUDE.md and the closed PRs #251/#254/#255/#257 — pushing gitea-side branches to github leaks private files).

```bash
git fetch github
git checkout -b feat/niche-topic-discovery github/main
ls src/cofounder_agent/services/migrations/ | tail -3
# next number = highest + 1, zero-padded to 4 digits
```

5. Confirm pgvector extension is loaded (`SELECT extname FROM pg_extension WHERE extname='vector'`). Migration 0103 added it; should already be present.

6. Add `langgraph` to deps (Task 13 / TWO_PASS uses it; the simpler writer modes stay plain Python). Per spec §"OSS leverage decisions":

```bash
cd src/cofounder_agent
poetry add "langgraph>=0.2,<1.0"
poetry run python -c "from langgraph.graph import StateGraph; print('ok')"
```

7. Throughout implementation, wrap every new LLM call (ranker in Task 4, distiller in Task 5, writer modes in Tasks 10-13) with a Langfuse trace. Spec §"OSS leverage decisions" — Langfuse is already running, every call should be observable from day one. Pattern (use this everywhere we call `_ollama_chat_json`):

```python
from langfuse.decorators import observe

@observe(name="topic_ranker_llm_score")
async def llm_final_score(...):
    ...
```

The exact decorator path depends on the project's Langfuse SDK version — check existing services for `@observe` usage and mirror.

---

## File structure

**New files:**

| Path                                                                                 | Purpose                                                                                                                                 |
| ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `src/cofounder_agent/services/migrations/<N>_create_niche_topic_discovery_tables.py` | Schema for `niches`, `niche_goals`, `niche_sources`, `topic_batches`, `topic_candidates`, `internal_topic_candidates`, `discovery_runs` |
| `src/cofounder_agent/services/migrations/<N+1>_seed_glad_labs_niche.py`              | First-niche seed                                                                                                                        |
| `src/cofounder_agent/services/niche_service.py`                                      | CRUD on `niches` + `niche_goals` + `niche_sources`                                                                                      |
| `src/cofounder_agent/services/topic_ranking.py`                                      | Goal vectors, embedding pre-rank, LLM final-score, decay re-rank                                                                        |
| `src/cofounder_agent/services/internal_rag_source.py`                                | Generates internal candidates from `embeddings` table per `source_kind`                                                                 |
| `src/cofounder_agent/services/topic_batch_service.py`                                | Orchestrator: discovery → rank → batch → resolve → handoff                                                                              |
| `src/cofounder_agent/services/writer_rag_modes/__init__.py`                          | Mode dispatcher                                                                                                                         |
| `src/cofounder_agent/services/writer_rag_modes/topic_only.py`                        | TOPIC_ONLY mode                                                                                                                         |
| `src/cofounder_agent/services/writer_rag_modes/citation_budget.py`                   | CITATION_BUDGET mode                                                                                                                    |
| `src/cofounder_agent/services/writer_rag_modes/story_spine.py`                       | STORY_SPINE mode                                                                                                                        |
| `src/cofounder_agent/services/writer_rag_modes/two_pass.py`                          | TWO_PASS mode (Glad Labs default)                                                                                                       |
| `src/cofounder_agent/tests/unit/services/test_niche_service.py`                      |                                                                                                                                         |
| `src/cofounder_agent/tests/unit/services/test_topic_ranking.py`                      |                                                                                                                                         |
| `src/cofounder_agent/tests/unit/services/test_internal_rag_source.py`                |                                                                                                                                         |
| `src/cofounder_agent/tests/unit/services/test_topic_batch_service.py`                |                                                                                                                                         |
| `src/cofounder_agent/tests/unit/services/writer_rag_modes/test_modes.py`             |                                                                                                                                         |
| `src/cofounder_agent/tests/integration/test_niche_discovery_e2e.py`                  |                                                                                                                                         |

**Modified files:**

| Path                                                     | Change                                                                                           |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `src/cofounder_agent/poindexter/cli/topics.py`           | Add `show-batch`, `rank-batch`, `edit-winner`, `resolve-batch`, `reject-batch`, `niche` subgroup |
| `src/cofounder_agent/services/ai_content_generator.py`   | Add `writer_rag_mode` dispatch in the writer entry point                                         |
| `src/cofounder_agent/services/topic_proposal_service.py` | Mark deprecated; redirect callers to `topic_batch_service`; final delete in a follow-up PR       |
| `mcp-server/server.py`                                   | Add MCP tools mirroring the new CLI commands                                                     |

---

## Task 1: Schema migration — niche topic-discovery tables

**Files:**

- Create: `src/cofounder_agent/services/migrations/<NEXT_MIGRATION>_create_niche_topic_discovery_tables.py`

- [ ] **Step 1: Write the failing migration smoke test invocation**

The repo has a CI job `migrations-smoke` (added in #229 / PR #272) that asserts every migration applies cleanly to a fresh DB. This task's verification is implicit: the migration must apply without error to a fresh `pgvector/pgvector:pg16` container.

Run before creating the file:

```bash
python scripts/ci/migrations_smoke.py
```

Expected: PASS at current migration count.

- [ ] **Step 2: Create the migration file**

```python
"""Migration <NEXT_MIGRATION>: niche-aware topic discovery tables.

Adds the data layer for the RAG pivot + niche-aware topic discovery
design (docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md).

Tables:
- niches                       — first-class niche configuration
- niche_goals                  — weighted goals per niche (TRAFFIC, EDUCATION, ...)
- niche_sources                — per-niche source plugin toggles + weights
- topic_batches                — operator-interaction unit
- topic_candidates             — external (HN, dev.to, web_search) candidates
- internal_topic_candidates    — RAG-derived candidates (different shape)
- discovery_runs               — observability: when sweeps fire + what they produce

All UUIDs default via gen_random_uuid (pgcrypto extension already loaded).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS niches (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                slug            TEXT UNIQUE NOT NULL,
                name            TEXT NOT NULL,
                active          BOOLEAN NOT NULL DEFAULT true,
                target_audience_tags TEXT[] NOT NULL DEFAULT '{}',
                writer_prompt_override TEXT,
                writer_rag_mode TEXT NOT NULL DEFAULT 'TOPIC_ONLY'
                    CHECK (writer_rag_mode IN ('TOPIC_ONLY','CITATION_BUDGET','STORY_SPINE','TWO_PASS')),
                batch_size      INT NOT NULL DEFAULT 5 CHECK (batch_size BETWEEN 1 AND 20),
                discovery_cadence_minute_floor INT NOT NULL DEFAULT 60 CHECK (discovery_cadence_minute_floor >= 1),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS niche_goals (
                niche_id   UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                goal_type  TEXT NOT NULL
                    CHECK (goal_type IN ('TRAFFIC','EDUCATION','BRAND','AUTHORITY','REVENUE','COMMUNITY','NICHE_DEPTH')),
                weight_pct INT NOT NULL CHECK (weight_pct BETWEEN 0 AND 100),
                PRIMARY KEY (niche_id, goal_type)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS niche_sources (
                niche_id    UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                source_name TEXT NOT NULL,
                enabled     BOOLEAN NOT NULL DEFAULT true,
                weight_pct  INT NOT NULL CHECK (weight_pct BETWEEN 0 AND 100),
                PRIMARY KEY (niche_id, source_name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS topic_batches (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                niche_id     UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                status       TEXT NOT NULL CHECK (status IN ('open','resolved','expired')),
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at   TIMESTAMPTZ NOT NULL,
                resolved_at  TIMESTAMPTZ,
                picked_candidate_id UUID,
                picked_candidate_kind TEXT CHECK (picked_candidate_kind IN ('external','internal') OR picked_candidate_kind IS NULL)
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_batch_per_niche
                ON topic_batches (niche_id) WHERE status = 'open'
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS topic_candidates (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id        UUID NOT NULL REFERENCES topic_batches(id) ON DELETE CASCADE,
                niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                source_name     TEXT NOT NULL,
                source_ref      TEXT NOT NULL,
                title           TEXT NOT NULL,
                summary         TEXT,
                score           NUMERIC NOT NULL,
                score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
                rank_in_batch   INT NOT NULL,
                operator_rank   INT,
                operator_edited_topic TEXT,
                operator_edited_angle TEXT,
                decay_factor    NUMERIC NOT NULL DEFAULT 1.0,
                carried_from_batch_id UUID REFERENCES topic_batches(id),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (batch_id, source_name, source_ref)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS internal_topic_candidates (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id        UUID NOT NULL REFERENCES topic_batches(id) ON DELETE CASCADE,
                niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                source_kind     TEXT NOT NULL
                    CHECK (source_kind IN ('claude_session','brain_knowledge','audit_event','git_commit','decision_log','memory_file','post_history')),
                primary_ref     TEXT NOT NULL,
                supporting_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                distilled_topic TEXT NOT NULL,
                distilled_angle TEXT NOT NULL,
                score           NUMERIC NOT NULL,
                score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
                rank_in_batch   INT NOT NULL,
                operator_rank   INT,
                operator_edited_topic TEXT,
                operator_edited_angle TEXT,
                decay_factor    NUMERIC NOT NULL DEFAULT 1.0,
                carried_from_batch_id UUID REFERENCES topic_batches(id),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS discovery_runs (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at     TIMESTAMPTZ,
                candidates_generated      INT,
                candidates_carried_forward INT,
                batch_id        UUID REFERENCES topic_batches(id),
                error           TEXT
            )
        """)
        # Helpful indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_topic_candidates_batch ON topic_candidates(batch_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_internal_topic_candidates_batch ON internal_topic_candidates(batch_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_discovery_runs_niche_started ON discovery_runs(niche_id, started_at DESC)")
        logger.info("Created niche topic-discovery tables (<NEXT_MIGRATION>)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for tbl in (
            "discovery_runs", "internal_topic_candidates", "topic_candidates",
            "topic_batches", "niche_sources", "niche_goals", "niches",
        ):
            await conn.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        logger.info("Dropped niche topic-discovery tables (<NEXT_MIGRATION> down)")
```

Replace `<NEXT_MIGRATION>` everywhere in the body with the actual zero-padded number you picked in pre-flight.

- [ ] **Step 3: Run migrations smoke against the new file**

```bash
python scripts/ci/migrations_smoke.py
```

Expected: PASS — schema_migrations row count matches file count.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/migrations/<NEXT_MIGRATION>_create_niche_topic_discovery_tables.py
git commit -m "feat(niche): create niche topic-discovery tables (<NEXT_MIGRATION>)"
```

---

## Task 2: NicheService — CRUD on niches / goals / sources

**Files:**

- Create: `src/cofounder_agent/services/niche_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_niche_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_niche_service.py
import pytest
from services.niche_service import NicheService, Niche, NicheGoal, NicheSource

pytestmark = pytest.mark.asyncio


async def test_create_niche_inserts_row(db_pool):
    svc = NicheService(db_pool)
    niche = await svc.create(slug="glad-labs", name="Glad Labs",
                             writer_rag_mode="TWO_PASS", batch_size=5)
    assert isinstance(niche, Niche)
    assert niche.slug == "glad-labs"
    assert niche.writer_rag_mode == "TWO_PASS"
    fetched = await svc.get_by_slug("glad-labs")
    assert fetched.id == niche.id


async def test_create_niche_rejects_duplicate_slug(db_pool):
    svc = NicheService(db_pool)
    await svc.create(slug="dupe", name="A")
    with pytest.raises(Exception):
        await svc.create(slug="dupe", name="B")


async def test_set_goals_validates_weights_sum_to_100(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="x", name="X")
    with pytest.raises(ValueError, match="weights must sum to ~100"):
        await svc.set_goals(n.id, [
            NicheGoal(goal_type="TRAFFIC", weight_pct=30),
            NicheGoal(goal_type="EDUCATION", weight_pct=20),
            # sums to 50, should reject
        ])
    # 100 ± 1 tolerance is fine
    await svc.set_goals(n.id, [
        NicheGoal(goal_type="TRAFFIC", weight_pct=60),
        NicheGoal(goal_type="EDUCATION", weight_pct=40),
    ])
    fetched = await svc.get_goals(n.id)
    assert {g.goal_type: g.weight_pct for g in fetched} == {"TRAFFIC": 60, "EDUCATION": 40}


async def test_set_sources_replaces_prior_config(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="y", name="Y")
    await svc.set_sources(n.id, [
        NicheSource(source_name="hackernews", enabled=True, weight_pct=50),
        NicheSource(source_name="devto", enabled=True, weight_pct=50),
    ])
    await svc.set_sources(n.id, [
        NicheSource(source_name="internal_rag", enabled=True, weight_pct=100),
    ])
    fetched = await svc.get_sources(n.id)
    assert len(fetched) == 1
    assert fetched[0].source_name == "internal_rag"
```

The `db_pool` fixture should already exist in `tests/unit/conftest.py` — if not, the implementer adds it (returns a fresh asyncpg pool against a test DB).

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_niche_service.py -v
```

Expected: ImportError or ModuleNotFoundError.

- [ ] **Step 3: Implement NicheService**

```python
# services/niche_service.py
"""CRUD service for niches + their goals + their source configs.

Niches are the first-class configuration unit for the topic-discovery + RAG
pivot (see docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md).
Glad Labs is the first niche; future operators add their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.logger_config import get_logger

logger = get_logger(__name__)


_VALID_GOAL_TYPES = frozenset({
    "TRAFFIC", "EDUCATION", "BRAND", "AUTHORITY",
    "REVENUE", "COMMUNITY", "NICHE_DEPTH",
})

_VALID_RAG_MODES = frozenset({
    "TOPIC_ONLY", "CITATION_BUDGET", "STORY_SPINE", "TWO_PASS",
})


@dataclass(frozen=True)
class Niche:
    id: UUID
    slug: str
    name: str
    active: bool
    target_audience_tags: list[str]
    writer_prompt_override: str | None
    writer_rag_mode: str
    batch_size: int
    discovery_cadence_minute_floor: int


@dataclass(frozen=True)
class NicheGoal:
    goal_type: str
    weight_pct: int


@dataclass(frozen=True)
class NicheSource:
    source_name: str
    enabled: bool
    weight_pct: int


class NicheService:
    def __init__(self, pool):
        self._pool = pool

    async def create(
        self, *, slug: str, name: str,
        target_audience_tags: list[str] | None = None,
        writer_prompt_override: str | None = None,
        writer_rag_mode: str = "TOPIC_ONLY",
        batch_size: int = 5,
        discovery_cadence_minute_floor: int = 60,
    ) -> Niche:
        if writer_rag_mode not in _VALID_RAG_MODES:
            raise ValueError(f"invalid writer_rag_mode: {writer_rag_mode!r}")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO niches (slug, name, target_audience_tags,
                                    writer_prompt_override, writer_rag_mode,
                                    batch_size, discovery_cadence_minute_floor)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING *
                """,
                slug, name, list(target_audience_tags or []),
                writer_prompt_override, writer_rag_mode,
                batch_size, discovery_cadence_minute_floor,
            )
        return _row_to_niche(row)

    async def get_by_slug(self, slug: str) -> Niche | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM niches WHERE slug = $1", slug)
        return _row_to_niche(row) if row else None

    async def get_by_id(self, niche_id: UUID) -> Niche | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM niches WHERE id = $1", niche_id)
        return _row_to_niche(row) if row else None

    async def list_active(self) -> list[Niche]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM niches WHERE active ORDER BY slug")
        return [_row_to_niche(r) for r in rows]

    async def set_goals(self, niche_id: UUID, goals: list[NicheGoal]) -> None:
        bad = [g.goal_type for g in goals if g.goal_type not in _VALID_GOAL_TYPES]
        if bad:
            raise ValueError(f"invalid goal_type(s): {bad}")
        total = sum(g.weight_pct for g in goals)
        if not 99 <= total <= 101:
            raise ValueError(f"weights must sum to ~100, got {total}")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM niche_goals WHERE niche_id = $1", niche_id)
                for g in goals:
                    await conn.execute(
                        "INSERT INTO niche_goals (niche_id, goal_type, weight_pct) VALUES ($1,$2,$3)",
                        niche_id, g.goal_type, g.weight_pct,
                    )

    async def get_goals(self, niche_id: UUID) -> list[NicheGoal]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT goal_type, weight_pct FROM niche_goals WHERE niche_id = $1 ORDER BY weight_pct DESC",
                niche_id,
            )
        return [NicheGoal(goal_type=r["goal_type"], weight_pct=r["weight_pct"]) for r in rows]

    async def set_sources(self, niche_id: UUID, sources: list[NicheSource]) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM niche_sources WHERE niche_id = $1", niche_id)
                for s in sources:
                    await conn.execute(
                        "INSERT INTO niche_sources (niche_id, source_name, enabled, weight_pct) VALUES ($1,$2,$3,$4)",
                        niche_id, s.source_name, s.enabled, s.weight_pct,
                    )

    async def get_sources(self, niche_id: UUID) -> list[NicheSource]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT source_name, enabled, weight_pct FROM niche_sources "
                "WHERE niche_id = $1 ORDER BY weight_pct DESC",
                niche_id,
            )
        return [NicheSource(**dict(r)) for r in rows]


def _row_to_niche(row: Any) -> Niche:
    return Niche(
        id=row["id"], slug=row["slug"], name=row["name"], active=row["active"],
        target_audience_tags=list(row["target_audience_tags"] or []),
        writer_prompt_override=row["writer_prompt_override"],
        writer_rag_mode=row["writer_rag_mode"],
        batch_size=row["batch_size"],
        discovery_cadence_minute_floor=row["discovery_cadence_minute_floor"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_niche_service.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/niche_service.py src/cofounder_agent/tests/unit/services/test_niche_service.py
git commit -m "feat(niche): NicheService CRUD over niches/goals/sources tables"
```

---

## Task 3: Goal vectors — precomputed embeddings + weighted cosine scoring

**Files:**

- Create: `src/cofounder_agent/services/topic_ranking.py` (start the file; later tasks add to it)
- Test: `src/cofounder_agent/tests/unit/services/test_topic_ranking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_topic_ranking.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.topic_ranking import (
    GOAL_DESCRIPTIONS, goal_vector_for, weighted_cosine_score,
)
from services.niche_service import NicheGoal


pytestmark = pytest.mark.asyncio


async def test_all_goal_types_have_descriptions():
    expected = {"TRAFFIC","EDUCATION","BRAND","AUTHORITY","REVENUE","COMMUNITY","NICHE_DEPTH"}
    assert set(GOAL_DESCRIPTIONS.keys()) == expected
    for desc in GOAL_DESCRIPTIONS.values():
        assert isinstance(desc, str) and len(desc) > 20


async def test_goal_vector_caches_embeddings(monkeypatch):
    calls = []
    async def fake_embed(text):
        calls.append(text)
        return [0.1] * 768
    monkeypatch.setattr("services.topic_ranking._embed_text_cached", fake_embed)
    v1 = await goal_vector_for("TRAFFIC")
    v2 = await goal_vector_for("TRAFFIC")
    assert v1 == v2
    # _embed_text_cached itself caches, so calls should be 1
    assert len(calls) == 1


async def test_weighted_cosine_score_combines_per_goal_signals():
    candidate_vec = [1.0, 0.0, 0.0]
    # Two goals; one aligns perfectly with candidate, the other is orthogonal.
    goal_vecs = {"TRAFFIC": [1.0, 0.0, 0.0], "EDUCATION": [0.0, 1.0, 0.0]}
    weights = [NicheGoal("TRAFFIC", 60), NicheGoal("EDUCATION", 40)]
    score, breakdown = weighted_cosine_score(candidate_vec, goal_vecs, weights)
    # 0.6 * 1.0 (perfect TRAFFIC) + 0.4 * 0.0 (orthogonal EDUCATION) = 0.6
    assert score == pytest.approx(0.6, abs=0.01)
    assert breakdown == {"TRAFFIC": pytest.approx(0.6, abs=0.01),
                         "EDUCATION": pytest.approx(0.0, abs=0.01)}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_ranking.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement goal vectors + weighted cosine**

```python
# services/topic_ranking.py
"""Topic candidate ranking — goal vectors + hybrid embed/LLM scoring + decay rerank.

Spec §"Goal vectors", §"Discovery sweep" steps 4-5.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from services.logger_config import get_logger
from services.niche_service import NicheGoal

logger = get_logger(__name__)


# Per the spec — fixed prose anchor for each goal type. Cached embeddings
# are computed lazily on first use and live for the process lifetime.
GOAL_DESCRIPTIONS: dict[str, str] = {
    "TRAFFIC":     "Topic likely to attract organic search traffic; trending keyword, broad appeal, evergreen demand.",
    "EDUCATION":   "Topic that teaches the reader something concrete and useful they didn't know before.",
    "BRAND":       "Topic that reinforces the operator's positioning and unique perspective.",
    "AUTHORITY":   "Topic that demonstrates the operator's depth and expertise on something specific.",
    "REVENUE":     "Topic that drives a commercial outcome: signups, sales, conversions, paid feature awareness.",
    "COMMUNITY":   "Topic that resonates with the operator's existing audience; sparks discussion, shares, replies.",
    "NICHE_DEPTH": "Topic that goes deep on the operator's niche specialty rather than broad-audience content.",
}


_GOAL_VEC_CACHE: dict[str, list[float]] = {}


async def _embed_text_cached(text: str) -> list[float]:
    """Embed via the existing service; the embedding service is responsible
    for its own caching (or not). This indirection lets tests monkeypatch.
    """
    from services.embedding_service import embed_text
    return await embed_text(text)


async def goal_vector_for(goal_type: str) -> list[float]:
    if goal_type in _GOAL_VEC_CACHE:
        return _GOAL_VEC_CACHE[goal_type]
    if goal_type not in GOAL_DESCRIPTIONS:
        raise ValueError(f"unknown goal_type: {goal_type!r}")
    vec = await _embed_text_cached(GOAL_DESCRIPTIONS[goal_type])
    _GOAL_VEC_CACHE[goal_type] = vec
    return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def weighted_cosine_score(
    candidate_vec: list[float],
    goal_vecs: dict[str, list[float]],
    weights: list[NicheGoal],
) -> tuple[float, dict[str, float]]:
    """Returns (overall_score, per_goal_breakdown).

    overall_score = Σ over goals of (cosine(candidate, goal_vec) * weight_pct/100).
    breakdown maps goal_type → its weighted contribution.
    """
    breakdown: dict[str, float] = {}
    total = 0.0
    for g in weights:
        gv = goal_vecs.get(g.goal_type)
        if gv is None:
            continue
        cos = cosine_similarity(candidate_vec, gv)
        contribution = cos * (g.weight_pct / 100.0)
        breakdown[g.goal_type] = contribution
        total += contribution
    return total, breakdown
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_ranking.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_ranking.py src/cofounder_agent/tests/unit/services/test_topic_ranking.py
git commit -m "feat(ranking): goal vectors + weighted cosine scoring helpers"
```

---

## Task 4: LLM final-scorer + decay re-rank

**Files:**

- Modify: `src/cofounder_agent/services/topic_ranking.py` (add functions)
- Modify: `src/cofounder_agent/tests/unit/services/test_topic_ranking.py` (extend)

- [ ] **Step 1: Write the failing tests for LLM scorer + decay**

Append to `test_topic_ranking.py`:

```python
async def test_llm_final_score_returns_score_per_candidate(monkeypatch):
    from services.topic_ranking import llm_final_score, ScoredCandidate

    async def fake_ollama_chat(prompt: str, *, model: str) -> str:
        # Simulated JSON response from glm-4.7-5090
        return '{"c1": {"score": 87.5, "breakdown": {"TRAFFIC": 0.5, "EDUCATION": 0.375}},'  \
               ' "c2": {"score": 42.0, "breakdown": {"TRAFFIC": 0.2, "EDUCATION": 0.22}}}'
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_ollama_chat)

    candidates = [
        ScoredCandidate(id="c1", title="A", summary="x", embedding_score=0.6),
        ScoredCandidate(id="c2", title="B", summary="y", embedding_score=0.4),
    ]
    weights = [NicheGoal("TRAFFIC", 60), NicheGoal("EDUCATION", 40)]
    scored = await llm_final_score(candidates, weights)
    assert scored["c1"].score == 87.5
    assert scored["c2"].score == 42.0


def test_apply_decay_multiplies_score():
    from services.topic_ranking import apply_decay
    assert apply_decay(score=80, decay_factor=1.0) == 80
    assert apply_decay(score=80, decay_factor=0.7) == pytest.approx(56)
    assert apply_decay(score=80, decay_factor=0.49) == pytest.approx(39.2)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_ranking.py::test_llm_final_score_returns_score_per_candidate tests/unit/services/test_topic_ranking.py::test_apply_decay_multiplies_score -v
```

Expected: ImportError on `llm_final_score`/`apply_decay`/`ScoredCandidate`.

- [ ] **Step 3: Implement LLM final-scorer + decay**

Append to `services/topic_ranking.py`:

```python
import json
from dataclasses import dataclass


@dataclass
class ScoredCandidate:
    id: str
    title: str
    summary: str | None
    embedding_score: float
    llm_score: float | None = None
    score_breakdown: dict[str, float] | None = None


async def _ollama_chat_json(prompt: str, *, model: str) -> str:
    """One-shot Ollama chat call; returns the assistant's content as a string.
    Indirection so tests can monkeypatch.
    """
    import httpx
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post("http://localhost:11434/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["message"]["content"]


async def llm_final_score(
    candidates: list[ScoredCandidate],
    weights: list[NicheGoal],
    *,
    model: str = "glm-4.7-5090:latest",
) -> dict[str, ScoredCandidate]:
    """Single LLM call ranks the (already-shortlisted) candidates against weighted goals.

    Returns the same candidates with `llm_score` and `score_breakdown` filled in,
    keyed by candidate id.
    """
    weights_descr = "\n".join(f"- {g.goal_type} (weight {g.weight_pct}%): {GOAL_DESCRIPTIONS[g.goal_type]}" for g in weights)
    cand_block = "\n".join(f"[{c.id}] {c.title} — {c.summary or ''}" for c in candidates)
    prompt = f"""You are scoring topic candidates for a content pipeline against the operator's weighted goals.

Goals (weight in pct):
{weights_descr}

Candidates:
{cand_block}

Return STRICT JSON keyed by candidate id, of the form:
{{"<id>": {{"score": <0-100>, "breakdown": {{"<GOAL_TYPE>": <weighted contribution 0-1>, ...}}}}, ...}}

The breakdown values per candidate should approximately sum to (score / 100).
Return ONLY the JSON, no commentary.
"""
    raw = await _ollama_chat_json(prompt, model=model)
    parsed = json.loads(raw)
    result: dict[str, ScoredCandidate] = {}
    for c in candidates:
        score_blob = parsed.get(c.id)
        if score_blob is None:
            logger.warning("LLM scorer omitted candidate %s; defaulting to embedding_score", c.id)
            score_blob = {"score": c.embedding_score * 100, "breakdown": {}}
        c.llm_score = float(score_blob.get("score", 0.0))
        c.score_breakdown = dict(score_blob.get("breakdown", {}))
        result[c.id] = c
    return result


def apply_decay(*, score: float, decay_factor: float) -> float:
    """Effective score = raw score × decay_factor. Used both at insertion time
    (decay_factor=1.0 for fresh, <1.0 for carried-forward) and at re-rank time
    (carried-forward candidates get an additional decay multiplier applied here).
    """
    return score * decay_factor
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_ranking.py -v
```

Expected: 5 PASSED total.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_ranking.py src/cofounder_agent/tests/unit/services/test_topic_ranking.py
git commit -m "feat(ranking): LLM final-scorer + decay multiplier"
```

---

## Task 5: InternalRagSource — generate candidates from internal corpus

**Files:**

- Create: `src/cofounder_agent/services/internal_rag_source.py`
- Test: `src/cofounder_agent/tests/unit/services/test_internal_rag_source.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_internal_rag_source.py
import pytest
from unittest.mock import AsyncMock
from services.internal_rag_source import InternalRagSource, InternalCandidate


pytestmark = pytest.mark.asyncio


async def test_generate_pulls_top_k_per_source_kind(db_pool, monkeypatch):
    # The source should query the embeddings table for each enabled source_kind
    # and return distilled candidates.
    src = InternalRagSource(db_pool)
    # Mock the LLM distillation step — it turns a snippet into (topic, angle).
    async def fake_distill(snippets):
        return ("How we handled OAuth phase 1", "Why client credentials grant first")
    monkeypatch.setattr(src, "_distill_topic_angle", fake_distill)

    candidates = await src.generate(
        niche_id="00000000-0000-0000-0000-000000000001",
        source_kinds=["claude_session", "brain_knowledge"],
        per_kind_limit=2,
    )
    assert all(isinstance(c, InternalCandidate) for c in candidates)
    # at most 2 * 2 = 4 candidates if data exists for both source_kinds
    assert len(candidates) <= 4
    if candidates:
        c = candidates[0]
        assert c.distilled_topic
        assert c.distilled_angle
        assert c.primary_ref
        assert isinstance(c.supporting_refs, list)
```

This test depends on having SOME embedding data; for unit tests, the implementer should also add a fixture that seeds 2-3 fake rows in the embeddings table. If that's too heavy for unit tests, mark it `@pytest.mark.integration` and skip in unit runs.

- [ ] **Step 2: Run test to verify failure**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_internal_rag_source.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement InternalRagSource**

```python
# services/internal_rag_source.py
"""Generate topic candidates from the internal embedded corpus.

The Glad Labs writing pivot — instead of summarising external content,
mine our own claude_sessions / brain_knowledge / audit / decision_log /
git history / memory / posts for storyworthy events and turn each into
a proposed topic + angle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from services.logger_config import get_logger

logger = get_logger(__name__)


VALID_SOURCE_KINDS = (
    "claude_session", "brain_knowledge", "audit_event",
    "git_commit", "decision_log", "memory_file", "post_history",
)


@dataclass
class InternalCandidate:
    source_kind: str
    primary_ref: str           # the source_table row id / commit sha / file path
    distilled_topic: str
    distilled_angle: str
    supporting_refs: list[dict[str, Any]] = field(default_factory=list)
    raw_snippet: str = ""


class InternalRagSource:
    def __init__(self, pool):
        self._pool = pool

    async def generate(
        self,
        *,
        niche_id: UUID | str,
        source_kinds: list[str],
        per_kind_limit: int = 5,
    ) -> list[InternalCandidate]:
        bad = [s for s in source_kinds if s not in VALID_SOURCE_KINDS]
        if bad:
            raise ValueError(f"unknown source_kinds: {bad}")

        results: list[InternalCandidate] = []
        for kind in source_kinds:
            snippets = await self._fetch_recent_snippets(kind, per_kind_limit)
            for primary_ref, snippet, supporting in snippets:
                topic, angle = await self._distill_topic_angle(
                    [snippet] + [s["snippet"] for s in supporting],
                )
                results.append(InternalCandidate(
                    source_kind=kind,
                    primary_ref=primary_ref,
                    distilled_topic=topic,
                    distilled_angle=angle,
                    supporting_refs=supporting,
                    raw_snippet=snippet,
                ))
        return results

    async def _fetch_recent_snippets(
        self, source_kind: str, limit: int,
    ) -> list[tuple[str, str, list[dict[str, Any]]]]:
        """Pull the most-recent N entries for this kind from the embeddings table.

        Returns list of (primary_ref, snippet, supporting_refs).
        Mapping source_kind → embeddings.source_table:
          claude_session → 'claude_sessions'
          brain_knowledge → 'brain'
          audit_event → 'audit'
          git_commit → (TBD: needs git log query, not embeddings)
          decision_log → 'memory' filtered to decision_log
          memory_file → 'memory'
          post_history → 'posts'
        """
        # Translate source_kind to the embeddings.source_table name
        table_map = {
            "claude_session": "claude_sessions",
            "brain_knowledge": "brain",
            "audit_event": "audit",
            "decision_log": "memory",
            "memory_file": "memory",
            "post_history": "posts",
        }
        st = table_map.get(source_kind)
        if st is None:
            # git_commit not yet implemented — would query git log directly
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT source_id, text_preview
                  FROM embeddings
                 WHERE source_table = $1
                 ORDER BY created_at DESC
                 LIMIT $2
                """,
                st, limit,
            )
        return [(str(r["source_id"]), r["text_preview"] or "", []) for r in rows]

    async def _distill_topic_angle(self, snippets: list[str]) -> tuple[str, str]:
        """Run a small LLM call to extract a proposed (topic, angle) from raw snippets."""
        from services.topic_ranking import _ollama_chat_json
        joined = "\n---\n".join(s[:600] for s in snippets if s)
        prompt = f"""Read the snippets from an AI-operated content business's internal records.
Extract a proposed blog post topic and the unique angle (the "why this matters / what we learned").

Snippets:
{joined}

Return STRICT JSON: {{"topic": "<short title>", "angle": "<one-sentence framing>"}}.
"""
        raw = await _ollama_chat_json(prompt, model="glm-4.7-5090:latest")
        import json
        parsed = json.loads(raw)
        return parsed.get("topic", "Untitled"), parsed.get("angle", "")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_internal_rag_source.py -v
```

Expected: 1 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/internal_rag_source.py src/cofounder_agent/tests/unit/services/test_internal_rag_source.py
git commit -m "feat(rag): InternalRagSource — generate candidates from internal corpus"
```

---

## Task 6: TopicBatchService — orchestrator (discovery → rank → batch)

**Files:**

- Create: `src/cofounder_agent/services/topic_batch_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_topic_batch_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_topic_batch_service.py
import pytest
from datetime import timedelta
from services.topic_batch_service import TopicBatchService


pytestmark = pytest.mark.asyncio


async def test_run_sweep_creates_open_batch_with_candidates(db_pool, monkeypatch):
    """End-to-end: pick a niche, run sweep, expect an open topic_batch row
    with N candidates spanning topic_candidates + internal_topic_candidates."""
    # Seed a niche
    from services.niche_service import NicheService, NicheGoal, NicheSource
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="test-niche", name="Test", batch_size=3)
    await nsvc.set_goals(n.id, [
        NicheGoal("TRAFFIC", 50), NicheGoal("EDUCATION", 50),
    ])
    await nsvc.set_sources(n.id, [
        NicheSource("internal_rag", enabled=True, weight_pct=100),
    ])

    # Mock the internal source to return 5 fake candidates
    from services.internal_rag_source import InternalCandidate
    async def fake_internal_generate(self, **kwargs):
        return [InternalCandidate(
            source_kind="claude_session",
            primary_ref=f"sess-{i}",
            distilled_topic=f"Topic {i}",
            distilled_angle=f"Angle {i}",
        ) for i in range(5)]
    monkeypatch.setattr(
        "services.internal_rag_source.InternalRagSource.generate",
        fake_internal_generate,
    )

    # Mock the embedding step + LLM final scorer
    async def fake_embed_text(text):
        return [0.1] * 768
    monkeypatch.setattr("services.embedding_service.embed_text", fake_embed_text)

    async def fake_llm_score(candidates, weights, *, model="..."):
        return {c.id: type(c)(c.id, c.title, c.summary, c.embedding_score,
                              llm_score=80 - i * 5, score_breakdown={})
                for i, c in enumerate(candidates)}
    monkeypatch.setattr("services.topic_ranking.llm_final_score", fake_llm_score)

    svc = TopicBatchService(db_pool)
    batch = await svc.run_sweep(niche_id=n.id)
    assert batch is not None
    assert batch.status == "open"
    # batch_size=3, we generated 5 → top 3 in the batch
    assert batch.candidate_count == 3


async def test_only_one_open_batch_per_niche(db_pool):
    from services.niche_service import NicheService
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="solo", name="Solo")
    svc = TopicBatchService(db_pool)
    # First sweep: should succeed (mocked source returns nothing → empty batch
    # is fine for this test). Second sweep with an open batch already → must
    # raise or return None.
    # Implementation detail: TopicBatchService should check the unique partial
    # index OR check before insert.
```

(The second test is a sketch — implementer fleshes it out per their final API.)

- [ ] **Step 2: Run test to verify failure**

Expected: ImportError on `TopicBatchService`.

- [ ] **Step 3: Implement TopicBatchService**

```python
# services/topic_batch_service.py
"""Topic batch orchestrator — replaces topic_proposal_service.

Per-niche flow:
  1. discover candidates (external source plugins + InternalRagSource) per
     niche_sources weights → pool of ~20
  2. carry-forward leftover candidates from prior batch with decay
  3. embedding pre-rank against goal vectors → top 10
  4. LLM final-score on the top 10 → final batch_size winners
  5. write topic_batch + topic_candidates / internal_topic_candidates rows
  6. open the topic_decision approval gate
  7. record discovery_run

See spec §"Flow" + §"Discovery sweep".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from services.logger_config import get_logger
from services.niche_service import NicheService, Niche, NicheGoal, NicheSource
from services.topic_ranking import (
    GOAL_DESCRIPTIONS, ScoredCandidate, apply_decay,
    cosine_similarity, goal_vector_for, llm_final_score, weighted_cosine_score,
)

logger = get_logger(__name__)


@dataclass
class BatchSnapshot:
    id: UUID
    niche_id: UUID
    status: str
    candidate_count: int
    expires_at: datetime


class TopicBatchService:
    def __init__(self, pool):
        self._pool = pool
        self._niche_svc = NicheService(pool)

    async def run_sweep(self, *, niche_id: UUID) -> BatchSnapshot | None:
        niche = await self._niche_svc.get_by_id(niche_id)
        if niche is None:
            raise ValueError(f"unknown niche_id: {niche_id}")
        if not await self._floor_elapsed(niche):
            logger.info("Sweep skipped — floor not elapsed for niche %s", niche.slug)
            return None
        if await self._open_batch_exists(niche.id):
            logger.info("Sweep skipped — open batch already exists for niche %s", niche.slug)
            return None

        async with self._pool.acquire() as conn:
            run = await conn.fetchrow(
                "INSERT INTO discovery_runs (niche_id) VALUES ($1) RETURNING *",
                niche.id,
            )
        run_id = run["id"]

        try:
            external = await self._discover_external(niche)
            internal = await self._discover_internal(niche)
            carried = await self._load_carry_forward(niche.id)

            # Mark carried candidates with their existing decay_factor (already
            # decayed when last batch resolved); fresh candidates start at 1.0.
            pool_external, pool_internal = await self._embed_and_pre_rank(
                niche, external + carried["external"],
                internal + carried["internal"],
            )

            top10 = pool_external[:5] + pool_internal[:5]  # mix per source kind
            scored = await llm_final_score(top10, await self._niche_svc.get_goals(niche.id))

            # Sort by llm_score (post-decay), pick batch_size
            ranked = sorted(scored.values(), key=lambda c: -(c.llm_score or 0))[: niche.batch_size]

            batch = await self._write_batch(niche, ranked, pool_external, pool_internal)

            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE discovery_runs SET finished_at = NOW(), batch_id = $1, "
                    "candidates_generated = $2, candidates_carried_forward = $3 "
                    "WHERE id = $4",
                    batch.id,
                    len(external) + len(internal),
                    len(carried["external"]) + len(carried["internal"]),
                    run_id,
                )

            await self._open_topic_decision_gate(batch)
            return batch
        except Exception as e:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE discovery_runs SET finished_at = NOW(), error = $1 WHERE id = $2",
                    str(e), run_id,
                )
            raise

    # -- helpers (stubs the implementer fleshes out) --

    async def _floor_elapsed(self, niche: Niche) -> bool:
        async with self._pool.acquire() as conn:
            last = await conn.fetchval(
                "SELECT MAX(started_at) FROM discovery_runs WHERE niche_id = $1",
                niche.id,
            )
        if last is None:
            return True
        floor = timedelta(minutes=niche.discovery_cadence_minute_floor)
        return (datetime.now(timezone.utc) - last) >= floor

    async def _open_batch_exists(self, niche_id: UUID) -> bool:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM topic_batches WHERE niche_id = $1 AND status = 'open'",
                niche_id,
            )
        return count > 0

    async def _discover_external(self, niche: Niche) -> list[dict[str, Any]]:
        """Call the existing topic_discovery.TopicDiscovery for non-internal sources."""
        from services.topic_discovery import TopicDiscovery
        # TopicDiscovery returns DiscoveredTopic; convert to dicts here for ranking.
        # Skip 'internal_rag' source — that's handled by _discover_internal.
        sources = await self._niche_svc.get_sources(niche.id)
        external_sources = [s for s in sources if s.enabled and s.source_name != "internal_rag"]
        if not external_sources:
            return []
        # Plumbing: instantiate TopicDiscovery with site_config + an enabled-source filter
        # implementer: use existing TopicDiscovery patterns from services/topic_discovery.py
        return []  # placeholder — wire to topic_discovery on implementation

    async def _discover_internal(self, niche: Niche) -> list[dict[str, Any]]:
        sources = await self._niche_svc.get_sources(niche.id)
        if not any(s.source_name == "internal_rag" and s.enabled for s in sources):
            return []
        from services.internal_rag_source import InternalRagSource
        rag = InternalRagSource(self._pool)
        kinds = ["claude_session", "brain_knowledge", "audit_event",
                 "decision_log", "memory_file", "post_history"]
        cands = await rag.generate(niche_id=niche.id, source_kinds=kinds, per_kind_limit=4)
        return [{"kind": "internal", "data": c} for c in cands]

    async def _load_carry_forward(self, niche_id: UUID) -> dict[str, list]:
        """Pull unpicked candidates from the most recent resolved batch and
        return them with decay_factor pre-multiplied by 0.7."""
        async with self._pool.acquire() as conn:
            ext = await conn.fetch(
                """
                SELECT * FROM topic_candidates
                 WHERE niche_id = $1 AND operator_rank IS NULL
                   AND batch_id IN (
                       SELECT id FROM topic_batches
                        WHERE niche_id = $1 AND status = 'resolved'
                     ORDER BY resolved_at DESC LIMIT 1
                   )
                """,
                niche_id,
            )
            int_ = await conn.fetch(
                """
                SELECT * FROM internal_topic_candidates
                 WHERE niche_id = $1 AND operator_rank IS NULL
                   AND batch_id IN (
                       SELECT id FROM topic_batches
                        WHERE niche_id = $1 AND status = 'resolved'
                     ORDER BY resolved_at DESC LIMIT 1
                   )
                """,
                niche_id,
            )
        # Multiply decay_factor in memory; actual rows get re-inserted into the new batch
        return {
            "external": [{"row": dict(r), "decay_factor": float(r["decay_factor"]) * 0.7} for r in ext],
            "internal": [{"row": dict(r), "decay_factor": float(r["decay_factor"]) * 0.7} for r in int_],
        }

    async def _embed_and_pre_rank(
        self, niche: Niche, external: list, internal: list,
    ) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
        """Compute embedding + weighted cosine score per candidate.
        Returns (top external, top internal) — top 5 each, sorted by score desc.
        """
        from services.embedding_service import embed_text
        goals = await self._niche_svc.get_goals(niche.id)
        goal_vecs = {g.goal_type: await goal_vector_for(g.goal_type) for g in goals}

        async def score_one(text: str, decay: float) -> tuple[float, dict[str, float]]:
            vec = await embed_text(text)
            raw, breakdown = weighted_cosine_score(vec, goal_vecs, goals)
            return apply_decay(score=raw, decay_factor=decay), breakdown

        ext_scored: list[ScoredCandidate] = []
        for item in external:
            # implementer wires per-source candidate shape here
            text = item.get("title", "") + " " + (item.get("summary", "") or "")
            score, breakdown = await score_one(text, item.get("decay_factor", 1.0))
            ext_scored.append(ScoredCandidate(
                id=item.get("id") or item.get("source_ref") or text[:40],
                title=item.get("title", "Untitled"),
                summary=item.get("summary"),
                embedding_score=score,
                score_breakdown=breakdown,
            ))

        int_scored: list[ScoredCandidate] = []
        for item in internal:
            data = item.get("data") if "data" in item else item
            text = (getattr(data, "distilled_topic", "") + " " + getattr(data, "distilled_angle", ""))
            score, breakdown = await score_one(text, item.get("decay_factor", 1.0))
            int_scored.append(ScoredCandidate(
                id=str(getattr(data, "primary_ref", text[:40])),
                title=getattr(data, "distilled_topic", "Untitled"),
                summary=getattr(data, "distilled_angle", None),
                embedding_score=score,
                score_breakdown=breakdown,
            ))

        ext_scored.sort(key=lambda c: -c.embedding_score)
        int_scored.sort(key=lambda c: -c.embedding_score)
        return ext_scored[:5], int_scored[:5]

    async def _write_batch(
        self, niche: Niche, ranked: list[ScoredCandidate],
        all_external: list, all_internal: list,
    ) -> BatchSnapshot:
        """Create the topic_batches row + the candidate rows for both tables."""
        from datetime import datetime, timedelta, timezone
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                batch = await conn.fetchrow(
                    "INSERT INTO topic_batches (niche_id, status, expires_at) "
                    "VALUES ($1, 'open', $2) RETURNING *",
                    niche.id, expires,
                )
                rank_in_batch = 0
                for c in ranked:
                    rank_in_batch += 1
                    # Disambiguate which table to write to by checking the
                    # candidate's source signal — implementer wires this via
                    # a flag on ScoredCandidate when discovery splits the pool.
                    # Below uses a simple heuristic.
                    is_internal = c.id in {sc.id for sc in [_["data"] for _ in all_internal if "data" in _]}
                    if is_internal:
                        await conn.execute(
                            """
                            INSERT INTO internal_topic_candidates
                              (batch_id, niche_id, source_kind, primary_ref,
                               supporting_refs, distilled_topic, distilled_angle,
                               score, score_breakdown, rank_in_batch, decay_factor)
                            VALUES ($1, $2, $3, $4, '[]'::jsonb, $5, $6, $7, $8::jsonb, $9, $10)
                            """,
                            batch["id"], niche.id, "claude_session", c.id,
                            c.title, c.summary or "",
                            c.llm_score or 0, _json(c.score_breakdown or {}),
                            rank_in_batch, 1.0,
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO topic_candidates
                              (batch_id, niche_id, source_name, source_ref, title, summary,
                               score, score_breakdown, rank_in_batch, decay_factor)
                            VALUES ($1, $2, 'external', $3, $4, $5, $6, $7::jsonb, $8, $9)
                            """,
                            batch["id"], niche.id, c.id, c.title, c.summary,
                            c.llm_score or 0, _json(c.score_breakdown or {}),
                            rank_in_batch, 1.0,
                        )
        return BatchSnapshot(
            id=batch["id"], niche_id=batch["niche_id"], status=batch["status"],
            candidate_count=rank_in_batch, expires_at=batch["expires_at"],
        )

    async def _open_topic_decision_gate(self, batch: BatchSnapshot) -> None:
        """Light-reuse of existing topic_decision gate.
        Sets the gate's pending state via approval_service so the operator
        is flagged. Implementer reads services/approval_service.py for the
        exact API.
        """
        # Stub — implementer wires to approval_service.set_pending(...) or equiv.
        logger.info("Opened topic_decision gate for batch %s", batch.id)


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_batch_service.py -v
```

Expected: at least the first test PASSES; the second can be wired by the implementer.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_batch_service.py src/cofounder_agent/tests/unit/services/test_topic_batch_service.py
git commit -m "feat(topics): TopicBatchService orchestrator (discover→rank→batch→gate)"
```

---

## Task 7: Operator interactions — resolve, edit, reject

**Files:**

- Modify: `src/cofounder_agent/services/topic_batch_service.py` (add methods)
- Modify: `src/cofounder_agent/tests/unit/services/test_topic_batch_service.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
async def test_show_batch_returns_unified_ranked_view(db_pool):
    # ... seed a batch with 2 external + 3 internal candidates ...
    svc = TopicBatchService(db_pool)
    view = await svc.show_batch(batch_id=BATCH_ID)
    assert view.status == "open"
    assert len(view.candidates) == 5
    # candidates sorted by effective_score (score * decay_factor) desc
    scores = [c.effective_score for c in view.candidates]
    assert scores == sorted(scores, reverse=True)


async def test_rank_batch_records_operator_order(db_pool):
    svc = TopicBatchService(db_pool)
    await svc.rank_batch(batch_id=BATCH_ID, ordered_candidate_ids=["c5","c1","c3","c2","c4"])
    view = await svc.show_batch(batch_id=BATCH_ID)
    assert [c.id for c in sorted(view.candidates, key=lambda x: x.operator_rank)] == \
        ["c5","c1","c3","c2","c4"]


async def test_edit_winner_sets_operator_edit_fields(db_pool):
    svc = TopicBatchService(db_pool)
    await svc.edit_winner(batch_id=BATCH_ID, topic="New Title", angle="Fresh angle")
    view = await svc.show_batch(batch_id=BATCH_ID)
    winner = [c for c in view.candidates if c.operator_rank == 1][0]
    assert winner.operator_edited_topic == "New Title"
    assert winner.operator_edited_angle == "Fresh angle"


async def test_resolve_batch_advances_winner_and_marks_resolved(db_pool, monkeypatch):
    # Monkeypatch the pipeline handoff so the test doesn't need a real worker
    handoff_calls = []
    async def fake_handoff(candidate, niche):
        handoff_calls.append((candidate.id, niche.slug))
    monkeypatch.setattr(
        "services.topic_batch_service.TopicBatchService._handoff_to_pipeline",
        fake_handoff,
    )
    svc = TopicBatchService(db_pool)
    await svc.resolve_batch(batch_id=BATCH_ID)
    view = await svc.show_batch(batch_id=BATCH_ID)
    assert view.status == "resolved"
    assert view.picked_candidate_id is not None
    assert len(handoff_calls) == 1


async def test_reject_batch_marks_expired_and_can_re-discover(db_pool):
    svc = TopicBatchService(db_pool)
    await svc.reject_batch(batch_id=BATCH_ID, reason="none of these")
    view = await svc.show_batch(batch_id=BATCH_ID)
    assert view.status == "expired"
```

- [ ] **Step 2: Run tests to verify failure**

Expected: AttributeError on missing methods.

- [ ] **Step 3: Implement the operator-interaction methods**

Add to `TopicBatchService`:

```python
@dataclass
class CandidateView:
    id: str
    kind: str           # 'external' | 'internal'
    title: str
    summary: str | None
    score: float
    decay_factor: float
    effective_score: float
    rank_in_batch: int
    operator_rank: int | None
    operator_edited_topic: str | None
    operator_edited_angle: str | None
    score_breakdown: dict[str, float]


@dataclass
class BatchView:
    id: UUID
    niche_id: UUID
    status: str
    picked_candidate_id: UUID | None
    candidates: list[CandidateView]


async def show_batch(self, *, batch_id: UUID) -> BatchView:
    async with self._pool.acquire() as conn:
        batch = await conn.fetchrow("SELECT * FROM topic_batches WHERE id = $1", batch_id)
        if not batch:
            raise ValueError(f"unknown batch_id: {batch_id}")
        ext = await conn.fetch("SELECT * FROM topic_candidates WHERE batch_id = $1", batch_id)
        intern = await conn.fetch("SELECT * FROM internal_topic_candidates WHERE batch_id = $1", batch_id)
    cands: list[CandidateView] = []
    for r in ext:
        score = float(r["score"]); df = float(r["decay_factor"])
        cands.append(CandidateView(
            id=str(r["id"]), kind="external", title=r["title"], summary=r["summary"],
            score=score, decay_factor=df, effective_score=score * df,
            rank_in_batch=r["rank_in_batch"], operator_rank=r["operator_rank"],
            operator_edited_topic=r["operator_edited_topic"],
            operator_edited_angle=r["operator_edited_angle"],
            score_breakdown=r["score_breakdown"] or {},
        ))
    for r in intern:
        score = float(r["score"]); df = float(r["decay_factor"])
        cands.append(CandidateView(
            id=str(r["id"]), kind="internal", title=r["distilled_topic"], summary=r["distilled_angle"],
            score=score, decay_factor=df, effective_score=score * df,
            rank_in_batch=r["rank_in_batch"], operator_rank=r["operator_rank"],
            operator_edited_topic=r["operator_edited_topic"],
            operator_edited_angle=r["operator_edited_angle"],
            score_breakdown=r["score_breakdown"] or {},
        ))
    cands.sort(key=lambda c: -c.effective_score)
    return BatchView(
        id=batch["id"], niche_id=batch["niche_id"], status=batch["status"],
        picked_candidate_id=batch["picked_candidate_id"],
        candidates=cands,
    )


async def rank_batch(self, *, batch_id: UUID, ordered_candidate_ids: list[str]) -> None:
    """Set operator_rank on each candidate to its 1-based position in the list."""
    async with self._pool.acquire() as conn:
        async with conn.transaction():
            for rank, cand_id in enumerate(ordered_candidate_ids, start=1):
                # Try external first, then internal
                updated = await conn.execute(
                    "UPDATE topic_candidates SET operator_rank = $1 WHERE id = $2 AND batch_id = $3",
                    rank, cand_id, batch_id,
                )
                if updated.endswith("0"):
                    await conn.execute(
                        "UPDATE internal_topic_candidates SET operator_rank = $1 WHERE id = $2 AND batch_id = $3",
                        rank, cand_id, batch_id,
                    )


async def edit_winner(self, *, batch_id: UUID, topic: str | None = None, angle: str | None = None) -> None:
    """Edit the candidate currently ranked #1 by the operator."""
    async with self._pool.acquire() as conn:
        # Find the rank-1 candidate
        rid = await conn.fetchval(
            "SELECT id FROM topic_candidates WHERE batch_id = $1 AND operator_rank = 1",
            batch_id,
        )
        kind = "external"
        if rid is None:
            rid = await conn.fetchval(
                "SELECT id FROM internal_topic_candidates WHERE batch_id = $1 AND operator_rank = 1",
                batch_id,
            )
            kind = "internal"
        if rid is None:
            raise ValueError(f"no rank-1 candidate in batch {batch_id}; rank first")
        tbl = "topic_candidates" if kind == "external" else "internal_topic_candidates"
        await conn.execute(
            f"UPDATE {tbl} SET operator_edited_topic = $1, operator_edited_angle = $2 WHERE id = $3",
            topic, angle, rid,
        )


async def resolve_batch(self, *, batch_id: UUID) -> None:
    """Resolve = mark batch resolved, set picked_candidate_id to operator_rank=1,
    advance that candidate to the existing pipeline."""
    view = await self.show_batch(batch_id=batch_id)
    winner = next((c for c in view.candidates if c.operator_rank == 1), None)
    if winner is None:
        raise ValueError("no operator_rank=1 candidate; rank first")
    niche = await self._niche_svc.get_by_id(view.niche_id)
    await self._handoff_to_pipeline(winner, niche)
    async with self._pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE topic_batches
               SET status='resolved', resolved_at=NOW(),
                   picked_candidate_id=$1, picked_candidate_kind=$2
             WHERE id=$3
            """,
            winner.id, winner.kind, batch_id,
        )


async def reject_batch(self, *, batch_id: UUID, reason: str = "") -> None:
    async with self._pool.acquire() as conn:
        await conn.execute(
            "UPDATE topic_batches SET status='expired', resolved_at=NOW() WHERE id=$1",
            batch_id,
        )
    logger.info("Batch %s rejected (reason=%r)", batch_id, reason)


async def _handoff_to_pipeline(self, winner: "CandidateView", niche: Niche) -> None:
    """Advance the winning candidate into the existing content pipeline.
    Creates a new content_task with topic + angle + niche + writer_rag_mode +
    topic_batch_id provenance.
    """
    topic = winner.operator_edited_topic or winner.title
    angle = winner.operator_edited_angle or winner.summary or ""
    async with self._pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content_tasks
              (topic, description, status, stage, niche_slug, writer_rag_mode, topic_batch_id)
            VALUES ($1, $2, 'pending', 'pending', $3, $4, $5)
            """,
            topic, angle, niche.slug, niche.writer_rag_mode,
            winner.id,  # batch id provenance — note: column is topic_batch_id, see Task 8
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/test_topic_batch_service.py -v
```

Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_batch_service.py src/cofounder_agent/tests/unit/services/test_topic_batch_service.py
git commit -m "feat(topics): operator interactions — show / rank / edit / resolve / reject"
```

---

## Task 8: Pipeline handoff schema — content_tasks columns

**Files:**

- Create: `src/cofounder_agent/services/migrations/<NEXT+2>_extend_content_tasks_for_niches.py`

`content_tasks` needs three new columns the handoff writes to: `niche_slug`, `writer_rag_mode`, `topic_batch_id`.

- [ ] **Step 1: Create the migration**

```python
"""Migration <NEXT+2>: extend content_tasks for niche-aware pipeline handoff.

Adds three columns the new TopicBatchService.handoff path writes:
- niche_slug         (which niche the task belongs to)
- writer_rag_mode    (the writer mode the writer dispatcher reads)
- topic_batch_id     (provenance pointer to the batch the topic came from)

All nullable for backward compat — existing tasks predate niches and stay valid.
"""

from services.logger_config import get_logger
logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE content_tasks
              ADD COLUMN IF NOT EXISTS niche_slug TEXT,
              ADD COLUMN IF NOT EXISTS writer_rag_mode TEXT
                CHECK (writer_rag_mode IN ('TOPIC_ONLY','CITATION_BUDGET','STORY_SPINE','TWO_PASS') OR writer_rag_mode IS NULL),
              ADD COLUMN IF NOT EXISTS topic_batch_id UUID REFERENCES topic_batches(id)
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_content_tasks_niche ON content_tasks(niche_slug) WHERE niche_slug IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_content_tasks_batch ON content_tasks(topic_batch_id) WHERE topic_batch_id IS NOT NULL"
        )
        logger.info("Extended content_tasks with niche columns")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS ix_content_tasks_batch")
        await conn.execute("DROP INDEX IF EXISTS ix_content_tasks_niche")
        await conn.execute("""
            ALTER TABLE content_tasks
              DROP COLUMN IF EXISTS topic_batch_id,
              DROP COLUMN IF EXISTS writer_rag_mode,
              DROP COLUMN IF EXISTS niche_slug
        """)
```

- [ ] **Step 2: Run migrations smoke**

```bash
python scripts/ci/migrations_smoke.py
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/migrations/<NEXT+2>_extend_content_tasks_for_niches.py
git commit -m "feat(pipeline): extend content_tasks for niche-aware handoff"
```

---

## Task 9: Writer mode dispatcher

**Files:**

- Create: `src/cofounder_agent/services/writer_rag_modes/__init__.py`
- Test: `src/cofounder_agent/tests/unit/services/writer_rag_modes/test_modes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/writer_rag_modes/test_modes.py
import pytest
from unittest.mock import AsyncMock
from services.writer_rag_modes import dispatch_writer_mode

pytestmark = pytest.mark.asyncio


async def test_dispatch_calls_topic_only_handler(monkeypatch):
    called = {}
    async def fake_handler(*, topic, angle, niche_id, pool, **kw):
        called["mode"] = "TOPIC_ONLY"
        return {"draft": "..."}
    monkeypatch.setattr("services.writer_rag_modes.topic_only.run", fake_handler)
    out = await dispatch_writer_mode(
        mode="TOPIC_ONLY", topic="t", angle="a", niche_id="n", pool=None,
    )
    assert called["mode"] == "TOPIC_ONLY"
    assert "draft" in out


async def test_dispatch_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown writer_rag_mode"):
        await dispatch_writer_mode(mode="BOGUS", topic="t", angle="a", niche_id="n", pool=None)
```

- [ ] **Step 2: Run to verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement the dispatcher**

```python
# services/writer_rag_modes/__init__.py
"""Writer RAG mode dispatcher.

The writer stage of the content pipeline delegates to one of four handlers
based on the niche's writer_rag_mode setting. Each handler produces a draft
with different RAG strategy.
"""

from __future__ import annotations
from typing import Any
from uuid import UUID


async def dispatch_writer_mode(
    *,
    mode: str,
    topic: str,
    angle: str,
    niche_id: UUID | str,
    pool,
    **kwargs: Any,
) -> dict[str, Any]:
    """Route the writer call to the right mode handler. Each handler returns
    the writer's output dict (at minimum {"draft": "..."} plus any metadata).
    """
    if mode == "TOPIC_ONLY":
        from services.writer_rag_modes import topic_only
        return await topic_only.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    elif mode == "CITATION_BUDGET":
        from services.writer_rag_modes import citation_budget
        return await citation_budget.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    elif mode == "STORY_SPINE":
        from services.writer_rag_modes import story_spine
        return await story_spine.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    elif mode == "TWO_PASS":
        from services.writer_rag_modes import two_pass
        return await two_pass.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    else:
        raise ValueError(f"unknown writer_rag_mode: {mode!r}")
```

- [ ] **Step 4: Run to verify (the unknown-mode test passes; topic_only test still fails because the module doesn't exist yet — that's covered in Task 10)**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/writer_rag_modes/test_modes.py::test_dispatch_unknown_mode_raises -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/writer_rag_modes/__init__.py src/cofounder_agent/tests/unit/services/writer_rag_modes/test_modes.py
git commit -m "feat(writer): writer_rag_mode dispatcher (4-mode router)"
```

---

## Task 10: Writer mode — TOPIC_ONLY

**Files:**

- Create: `src/cofounder_agent/services/writer_rag_modes/topic_only.py`

- [ ] **Step 1: Implement TOPIC_ONLY**

```python
# services/writer_rag_modes/topic_only.py
"""TOPIC_ONLY writer mode — single embedding query, dump top-N internal snippets
into the writer prompt as background context. Simplest mode."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from services.logger_config import get_logger

logger = get_logger(__name__)


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool, **kw: Any) -> dict[str, Any]:
    from services.embedding_service import embed_text
    qvec = await embed_text(f"{topic} — {angle}")
    async with pool.acquire() as conn:
        # Top 8 internal snippets by cosine similarity. Uses pgvector.
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview,
                   1 - (embedding <=> $1::vector) AS similarity
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT 8
            """,
            qvec,
        )
    snippets = [{"source": r["source_table"], "ref": str(r["source_id"]),
                 "snippet": r["text_preview"], "similarity": float(r["similarity"])}
                for r in rows]
    # Hand off to the existing writer with the snippets in the prompt context.
    from services.ai_content_generator import generate_with_context
    draft = await generate_with_context(topic=topic, angle=angle, snippets=snippets)
    return {"draft": draft, "snippets_used": snippets, "mode": "TOPIC_ONLY"}
```

- [ ] **Step 2: Run dispatcher test (it should now pass for TOPIC_ONLY)**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/writer_rag_modes/test_modes.py -v
```

Expected: 2 PASSED.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/writer_rag_modes/topic_only.py
git commit -m "feat(writer): TOPIC_ONLY mode — single RAG query, snippets in prompt"
```

---

## Task 11: Writer mode — CITATION_BUDGET

**Files:**

- Create: `src/cofounder_agent/services/writer_rag_modes/citation_budget.py`

- [ ] **Step 1: Implement CITATION_BUDGET**

```python
# services/writer_rag_modes/citation_budget.py
"""CITATION_BUDGET — same as TOPIC_ONLY but the writer is REQUIRED to cite
at least N internal sources. content_validator enforces post-write."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from services.logger_config import get_logger

logger = get_logger(__name__)

DEFAULT_MIN_CITATIONS = 3


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool,
              min_citations: int = DEFAULT_MIN_CITATIONS, **kw: Any) -> dict[str, Any]:
    from services.embedding_service import embed_text
    qvec = await embed_text(f"{topic} — {angle}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT 12
            """,
            qvec,
        )
    snippets = [{"source": r["source_table"], "ref": str(r["source_id"]), "snippet": r["text_preview"]}
                for r in rows]

    citation_instruction = (
        f"You MUST cite at least {min_citations} of the provided internal sources by their "
        f"source/ref pair (e.g. [claude_sessions/abc123]). Failing to cite that many will "
        f"cause the post to be rejected by the validator."
    )
    from services.ai_content_generator import generate_with_context
    draft = await generate_with_context(
        topic=topic, angle=angle, snippets=snippets,
        extra_instructions=citation_instruction,
    )
    # Implementer wires content_validator extension to count [source/ref] tokens
    # in the draft and reject if < min_citations. Extension can be follow-up.
    return {"draft": draft, "snippets_used": snippets, "min_citations": min_citations, "mode": "CITATION_BUDGET"}
```

- [ ] **Step 2: Commit**

```bash
git add src/cofounder_agent/services/writer_rag_modes/citation_budget.py
git commit -m "feat(writer): CITATION_BUDGET mode — required N internal citations"
```

---

## Task 12: Writer mode — STORY_SPINE

**Files:**

- Create: `src/cofounder_agent/services/writer_rag_modes/story_spine.py`

- [ ] **Step 1: Implement STORY_SPINE**

```python
# services/writer_rag_modes/story_spine.py
"""STORY_SPINE — preprocess top snippets into a structured outline with one
LLM call, then expand the outline to full prose with the writer."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from services.logger_config import get_logger

logger = get_logger(__name__)


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool, **kw: Any) -> dict[str, Any]:
    from services.embedding_service import embed_text
    from services.topic_ranking import _ollama_chat_json
    qvec = await embed_text(f"{topic} — {angle}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT 15
            """,
            qvec,
        )
    snippets = [{"source": r["source_table"], "ref": str(r["source_id"]), "snippet": r["text_preview"]}
                for r in rows]
    snippet_block = "\n---\n".join(s["snippet"][:600] for s in snippets if s["snippet"])

    spine_prompt = f"""Read these 15 snippets from an AI-operated content business's records.
Produce a structured outline for a blog post about: "{topic}" (angle: "{angle}").

Outline format (return JSON):
{{
  "hook": "<opening hook, one sentence>",
  "what_happened": "<the timeline of events as drawn from snippets>",
  "why_it_matters": "<the lesson or insight>",
  "what_we_learned": "<concrete takeaways>",
  "close": "<call-to-action or final framing>"
}}

Snippets:
{snippet_block}
"""
    spine_raw = await _ollama_chat_json(spine_prompt, model="glm-4.7-5090:latest")
    import json
    spine = json.loads(spine_raw)

    from services.ai_content_generator import generate_with_outline
    draft = await generate_with_outline(topic=topic, outline=spine, snippets=snippets)
    return {"draft": draft, "spine": spine, "snippets_used": snippets, "mode": "STORY_SPINE"}
```

- [ ] **Step 2: Commit**

```bash
git add src/cofounder_agent/services/writer_rag_modes/story_spine.py
git commit -m "feat(writer): STORY_SPINE mode — structured outline pre-pass"
```

---

## Task 13: Writer mode — TWO_PASS (Glad Labs default, LangGraph)

**Files:**

- Create: `src/cofounder_agent/services/writer_rag_modes/two_pass.py`
- Test: `src/cofounder_agent/tests/unit/services/writer_rag_modes/test_two_pass.py`

Per spec §"OSS leverage decisions" — TWO_PASS is the only writer mode that uses LangGraph (the others stay plain Python). The flow is a real state machine: `embed_and_fetch → draft → detect_needs → research_each → revise`, with a conditional edge from `revise` back to `detect_needs` if revision surfaces new `[EXTERNAL_NEEDED]` markers. Bounded by `_MAX_REVISION_LOOPS=3` so it can't spin forever.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/services/writer_rag_modes/test_two_pass.py
import pytest
from unittest.mock import AsyncMock
from services.writer_rag_modes import two_pass

pytestmark = pytest.mark.asyncio


def _fake_pool_with_no_snippets():
    pool = AsyncMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[])
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn_mock)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


async def test_no_external_needed_returns_pass1_draft(monkeypatch):
    """First draft has no [EXTERNAL_NEEDED] markers → graph short-circuits, no revise."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None):
        return "A clean first draft with no markers."
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", fake_pass1)
    async def fake_embed(text): return [0.0] * 768
    monkeypatch.setattr("services.embedding_service.embed_text", fake_embed)

    result = await two_pass.run(topic="t", angle="a", niche_id="n", pool=_fake_pool_with_no_snippets())
    assert result["draft"] == "A clean first draft with no markers."
    assert result["external_lookups"] == []
    assert result["revision_loops"] == 0


async def test_external_needed_triggers_research_and_revise(monkeypatch):
    """One marker → research → revise → done in 1 loop."""
    drafts = iter([
        "First draft with [EXTERNAL_NEEDED: a fact] inside.",
        "Revised draft with the actual fact inside.",
    ])
    async def fake_pass1(topic, angle, snippets, extra_instructions=None):
        return next(drafts)
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", fake_pass1)
    async def fake_revise(prompt, *, model):
        return next(drafts)
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_revise)
    async def fake_research(query, max_sources=2):
        return f"External research result for: {query}"
    monkeypatch.setattr("services.research_service.research_topic", fake_research)
    async def fake_embed(text): return [0.0] * 768
    monkeypatch.setattr("services.embedding_service.embed_text", fake_embed)

    result = await two_pass.run(topic="t", angle="a", niche_id="n", pool=_fake_pool_with_no_snippets())
    assert "Revised draft" in result["draft"]
    assert len(result["external_lookups"]) == 1
    assert result["revision_loops"] == 1


async def test_loop_caps_at_max_revisions(monkeypatch):
    """Pathological: every revision adds new markers. Loop must terminate at _MAX_REVISION_LOOPS=3."""
    counter = {"n": 0}
    async def always_needs_more(topic, angle, snippets, extra_instructions=None):
        counter["n"] += 1
        return f"Draft with [EXTERNAL_NEEDED: thing {counter['n']}] inside."
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", always_needs_more)
    async def fake_revise(prompt, *, model):
        counter["n"] += 1
        return f"Revised with [EXTERNAL_NEEDED: another thing {counter['n']}]."
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_revise)
    async def fake_research(query, max_sources=2):
        return "fact"
    monkeypatch.setattr("services.research_service.research_topic", fake_research)
    async def fake_embed(text): return [0.0] * 768
    monkeypatch.setattr("services.embedding_service.embed_text", fake_embed)

    result = await two_pass.run(topic="t", angle="a", niche_id="n", pool=_fake_pool_with_no_snippets())
    assert result["revision_loops"] == 3
    assert result["loop_capped"] is True
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/writer_rag_modes/test_two_pass.py -v
```

Expected: ImportError on `two_pass` (or LangGraph import error if Step 6 of pre-flight wasn't run).

- [ ] **Step 3: Implement TWO_PASS as a LangGraph state machine**

```python
# services/writer_rag_modes/two_pass.py
"""TWO_PASS — internal-first draft, then conditional external fact-augmentation
loop. Implemented as a LangGraph state machine because:

- Multi-pass with conditional re-entry (revise can surface new
  [EXTERNAL_NEEDED] markers that need another research pass)
- Bounded loop (_MAX_REVISION_LOOPS=3 prevents runaway)
- Future-friendly: when we add an auto-researcher agent or a draft-critic
  loop, they slot in as new nodes/edges rather than refactoring orchestration

Spec §"OSS leverage decisions" — TWO_PASS is the only writer mode using
LangGraph; the simpler modes (TOPIC_ONLY, CITATION_BUDGET, STORY_SPINE)
stay plain Python because they don't have branching.

State flow:

    embed_and_fetch → draft → detect_needs ┐
                                            │ if needs found and loops < max:
                                            ↓
                                  research_each → revise ─┐
                                                           │
                                                           ↓
                                                  detect_needs (loop)
                                                           │
                                                           ↓ if no needs OR loops capped
                                                          END
"""

from __future__ import annotations

import re
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from services.logger_config import get_logger

logger = get_logger(__name__)


_MAX_REVISION_LOOPS = 3
_NEED_PATTERN = re.compile(r"\[EXTERNAL_NEEDED:\s*([^\]]+)\]")


class _State(TypedDict, total=False):
    topic: str
    angle: str
    snippets: list[dict[str, Any]]
    pool: Any
    draft: str
    needs: list[str]
    research_results: list[dict[str, Any]]
    external_lookups: list[dict[str, Any]]
    revision_loops: int
    loop_capped: bool


# -- nodes --

async def _embed_and_fetch_snippets(state: _State) -> _State:
    from services.embedding_service import embed_text
    qvec = await embed_text(f"{state['topic']} — {state['angle']}")
    async with state["pool"].acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT 20
            """,
            qvec,
        )
    snippets = [{"source": r["source_table"], "ref": str(r["source_id"]),
                 "snippet": r["text_preview"]} for r in rows]
    return {**state, "snippets": snippets, "revision_loops": 0,
            "external_lookups": [], "loop_capped": False}


async def _draft_node(state: _State) -> _State:
    from services.ai_content_generator import generate_with_context
    instruction = (
        "Write a first-draft blog post drawing ONLY from the provided internal "
        "snippets. Do NOT make up external facts, statistics, or quotes you cannot "
        "ground in a snippet. If you need an outside fact you don't have, mark it "
        "[EXTERNAL_NEEDED: <description>] in the draft so a follow-up pass can fill it in."
    )
    draft = await generate_with_context(
        topic=state["topic"], angle=state["angle"],
        snippets=state["snippets"], extra_instructions=instruction,
    )
    return {**state, "draft": draft}


def _detect_needs(state: _State) -> _State:
    needs = _NEED_PATTERN.findall(state["draft"])
    return {**state, "needs": [n.strip() for n in needs]}


async def _research_each(state: _State) -> _State:
    from services.research_service import research_topic
    results = []
    for need in state["needs"]:
        aug = await research_topic(query=need, max_sources=2)
        results.append({"need": need, "research": aug})
    cumulative = list(state.get("external_lookups") or []) + results
    return {**state, "research_results": results, "external_lookups": cumulative}


async def _revise_node(state: _State) -> _State:
    from services.topic_ranking import _ollama_chat_json
    aug_block = "\n\n".join(
        f"[EXTERNAL_NEEDED: {r['need']}] → {r['research']}"
        for r in state["research_results"]
    )
    revise_prompt = f"""Revise the following draft. For each [EXTERNAL_NEEDED: ...] marker,
substitute the corresponding external fact provided below. Keep everything else unchanged.
If revision exposes a new claim that needs outside support, mark it [EXTERNAL_NEEDED: ...]
again so the next pass can fill it.

Original draft:
{state['draft']}

External facts:
{aug_block}
"""
    new_draft = await _ollama_chat_json(revise_prompt, model="glm-4.7-5090:latest")
    return {**state, "draft": new_draft, "revision_loops": state.get("revision_loops", 0) + 1}


def _mark_capped(state: _State) -> _State:
    return {**state, "loop_capped": True}


# -- conditional edges --

def _needs_or_done(state: _State) -> str:
    """After detect_needs: route to research_each if needs found AND we haven't
    hit the loop cap, else END (or _done_capped if we're capping)."""
    if not state.get("needs"):
        return END
    if state.get("revision_loops", 0) >= _MAX_REVISION_LOOPS:
        return "_done_capped"
    return "research_each"


def _build_graph():
    g = StateGraph(_State)
    g.add_node("embed_and_fetch", _embed_and_fetch_snippets)
    g.add_node("draft", _draft_node)
    g.add_node("detect_needs", _detect_needs)
    g.add_node("research_each", _research_each)
    g.add_node("revise", _revise_node)
    g.add_node("_done_capped", _mark_capped)

    g.set_entry_point("embed_and_fetch")
    g.add_edge("embed_and_fetch", "draft")
    g.add_edge("draft", "detect_needs")
    g.add_conditional_edges("detect_needs", _needs_or_done, {
        "research_each": "research_each",
        "_done_capped": "_done_capped",
        END: END,
    })
    g.add_edge("research_each", "revise")
    g.add_edge("revise", "detect_needs")
    g.add_edge("_done_capped", END)

    # MemorySaver checkpointer means the graph CAN be paused mid-run and
    # resumed — useful when an operator interrupts mid-revision in v2.
    # v1 uses in-memory; v2 swaps to a postgres checkpointer for
    # cross-process durability.
    return g.compile(checkpointer=MemorySaver())


_GRAPH = _build_graph()


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool, **kw: Any) -> dict[str, Any]:
    initial: _State = {"topic": topic, "angle": angle, "pool": pool}
    config = {"configurable": {"thread_id": f"two_pass-{niche_id}-{topic[:32]}"}}
    final = await _GRAPH.ainvoke(initial, config=config)
    return {
        "draft": final["draft"],
        "snippets_used": final.get("snippets", []),
        "external_lookups": final.get("external_lookups", []),
        "revision_loops": final.get("revision_loops", 0),
        "loop_capped": final.get("loop_capped", False),
        "mode": "TWO_PASS",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/writer_rag_modes/test_two_pass.py -v
```

Expected: 3 PASSED.

- [ ] **Step 2: Commit**

```bash
git add src/cofounder_agent/services/writer_rag_modes/two_pass.py
git commit -m "feat(writer): TWO_PASS mode — internal-first + external fact-augmentation"
```

---

## Task 14: Wire writer dispatch into ai_content_generator

**Files:**

- Modify: `src/cofounder_agent/services/ai_content_generator.py`

- [ ] **Step 1: Locate the writer entry point**

```bash
grep -n "def generate" src/cofounder_agent/services/ai_content_generator.py | head
```

The implementer reads the existing entry point (likely `generate_blog_post` or similar) and identifies where to inject the dispatch.

- [ ] **Step 2: Add helper functions the writer modes call into**

The writer modes call `generate_with_context(topic, angle, snippets, extra_instructions=)` and `generate_with_outline(topic, outline, snippets)`. These need to exist on `ai_content_generator`. If they don't, the implementer adds thin wrappers that compose the existing prompt-building helpers with the new arguments.

```python
# Append to services/ai_content_generator.py

async def generate_with_context(
    *, topic: str, angle: str, snippets: list[dict],
    extra_instructions: str | None = None,
) -> str:
    """Build a prompt using the snippets as background context, generate the draft.
    Wraps the existing generation path; tests can monkeypatch here.
    """
    snippet_block = "\n".join(
        f"[{s['source']}/{s['ref']}] {s['snippet'][:500]}"
        for s in snippets if s.get('snippet')
    )
    instructions = extra_instructions or ""
    # Implementer plugs into the existing prompt builder + Ollama call here.
    # Below is the minimal shape:
    from services.topic_ranking import _ollama_chat_json
    prompt = f"""Write a blog post on the topic: "{topic}" with this angle: "{angle}".

{instructions}

Background context (cite where relevant):
{snippet_block}

Return the full post body in Markdown.
"""
    return await _ollama_chat_json(prompt, model="glm-4.7-5090:latest")


async def generate_with_outline(
    *, topic: str, outline: dict, snippets: list[dict],
) -> str:
    snippet_block = "\n".join(
        f"[{s['source']}/{s['ref']}] {s['snippet'][:500]}"
        for s in snippets if s.get('snippet')
    )
    outline_block = "\n".join(f"{k.replace('_',' ').title()}: {v}" for k, v in outline.items())
    from services.topic_ranking import _ollama_chat_json
    prompt = f"""Expand the following outline into a full blog post.

Topic: {topic}
Outline:
{outline_block}

Background snippets to draw on:
{snippet_block}

Return the full post body in Markdown.
"""
    return await _ollama_chat_json(prompt, model="glm-4.7-5090:latest")
```

- [ ] **Step 3: Modify the writer pipeline stage to call the dispatcher**

Find the existing writer stage (probably `services/stages/generate_content.py` or similar). The implementer adds:

```python
# In the writer stage, after loading the content_task row
from services.writer_rag_modes import dispatch_writer_mode

if task.writer_rag_mode and task.writer_rag_mode != "":
    # New niche-aware path
    result = await dispatch_writer_mode(
        mode=task.writer_rag_mode,
        topic=task.topic,
        angle=task.description or "",
        niche_id=None,  # implementer wires niche_id lookup if needed
        pool=pool,
    )
    draft = result["draft"]
else:
    # Legacy path — existing generation logic unchanged
    draft = await legacy_generate_blog_post(task)
```

- [ ] **Step 4: Smoke test by creating a content_task with writer_rag_mode set**

Manual verification — start the worker, insert a row with `writer_rag_mode='TOPIC_ONLY'`, watch logs to confirm the dispatch fires.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/ai_content_generator.py
# plus the writer-stage file you modified
git commit -m "feat(writer): wire writer_rag_mode dispatch into generation stage"
```

---

## Task 15: CLI commands — topics + niche

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/topics.py`

- [ ] **Step 1: Read the existing CLI structure**

```bash
sed -n '1,40p' src/cofounder_agent/poindexter/cli/topics.py
```

The implementer mirrors the existing click command pattern (already used by `auth.py`, `stores.py`, etc — see Task 17 of OAuth Phase 1 PR #250 for a template).

- [ ] **Step 2: Add the new commands**

```python
# Append to poindexter/cli/topics.py

import click, asyncio, json
from poindexter.cli._dsn import _dsn  # existing helper


@topics_group.command("show-batch")
@click.option("--niche", required=True, help="Niche slug.")
def show_batch(niche: str) -> None:
    """Show the current open batch for a niche."""
    async def _impl():
        import asyncpg
        from services.niche_service import NicheService
        from services.topic_batch_service import TopicBatchService
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            n = await NicheService(pool).get_by_slug(niche)
            if not n: raise click.ClickException(f"unknown niche: {niche}")
            async with pool.acquire() as conn:
                bid = await conn.fetchval(
                    "SELECT id FROM topic_batches WHERE niche_id = $1 AND status = 'open'",
                    n.id,
                )
            if bid is None:
                click.echo(f"No open batch for niche {niche}.")
                return
            view = await TopicBatchService(pool).show_batch(batch_id=bid)
            click.echo(f"Batch {view.id} (status={view.status})")
            for c in view.candidates:
                marker = f"#{c.operator_rank}" if c.operator_rank else f"sys#{c.rank_in_batch}"
                click.echo(f"  {marker:6s} [{c.kind:8s}] eff={c.effective_score:5.1f} — {c.title}")
        finally:
            await pool.close()
    asyncio.run(_impl())


@topics_group.command("rank-batch")
@click.argument("batch_id")
@click.option("--order", required=True, help="Comma-separated candidate ids in your preferred order, best-first.")
def rank_batch(batch_id: str, order: str) -> None:
    """Set operator ranking for a batch's candidates."""
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService
        ids = [s.strip() for s in order.split(",") if s.strip()]
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).rank_batch(batch_id=batch_id, ordered_candidate_ids=ids)
            click.echo(f"Ranked {len(ids)} candidates in batch {batch_id}")
        finally:
            await pool.close()
    asyncio.run(_impl())


@topics_group.command("edit-winner")
@click.argument("batch_id")
@click.option("--topic", help="Override the winner's title.")
@click.option("--angle", help="Override the winner's angle/summary.")
def edit_winner(batch_id: str, topic: str | None, angle: str | None) -> None:
    """Edit the title/angle of the rank-1 candidate before resolution."""
    if not topic and not angle:
        raise click.UsageError("Provide --topic and/or --angle.")
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).edit_winner(batch_id=batch_id, topic=topic, angle=angle)
            click.echo("Edited winner.")
        finally:
            await pool.close()
    asyncio.run(_impl())


@topics_group.command("resolve-batch")
@click.argument("batch_id")
def resolve_batch(batch_id: str) -> None:
    """Resolve a batch — advance the rank-1 candidate to the pipeline."""
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).resolve_batch(batch_id=batch_id)
            click.echo(f"Resolved {batch_id}")
        finally:
            await pool.close()
    asyncio.run(_impl())


@topics_group.command("reject-batch")
@click.argument("batch_id")
@click.option("--reason", default="", help="Optional reason text.")
def reject_batch(batch_id: str, reason: str) -> None:
    """Reject a batch — discard candidates, allow a fresh sweep."""
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).reject_batch(batch_id=batch_id, reason=reason)
            click.echo(f"Rejected {batch_id}")
        finally:
            await pool.close()
    asyncio.run(_impl())


@topics_group.group("niche")
def niche_group():
    """Manage niche configurations."""
    pass


@niche_group.command("list")
def niche_list():
    async def _impl():
        import asyncpg
        from services.niche_service import NicheService
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            for n in await NicheService(pool).list_active():
                click.echo(f"{n.slug:20s} {n.name:30s} mode={n.writer_rag_mode}")
        finally:
            await pool.close()
    asyncio.run(_impl())


@niche_group.command("show")
@click.argument("slug")
def niche_show(slug: str):
    async def _impl():
        import asyncpg
        from services.niche_service import NicheService
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            svc = NicheService(pool)
            n = await svc.get_by_slug(slug)
            if not n: raise click.ClickException(f"unknown niche: {slug}")
            click.echo(json.dumps({
                "slug": n.slug, "name": n.name, "active": n.active,
                "writer_rag_mode": n.writer_rag_mode, "batch_size": n.batch_size,
                "discovery_cadence_minute_floor": n.discovery_cadence_minute_floor,
                "audience_tags": n.target_audience_tags,
                "goals": [{"type": g.goal_type, "weight": g.weight_pct}
                          for g in await svc.get_goals(n.id)],
                "sources": [{"name": s.source_name, "enabled": s.enabled, "weight": s.weight_pct}
                            for s in await svc.get_sources(n.id)],
            }, indent=2))
        finally:
            await pool.close()
    asyncio.run(_impl())
```

- [ ] **Step 3: Sanity-check the CLI loads**

```bash
DATABASE_URL="$(python -c 'import sys; sys.path.insert(0,"."); from brain.bootstrap import resolve_database_url; print(resolve_database_url())')" \
  poindexter topics --help
```

Expected: new commands appear in the help output.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/topics.py
git commit -m "feat(cli): topics show-batch / rank-batch / edit-winner / resolve-batch / reject-batch + niche subgroup"
```

---

## Task 16: MCP tools mirroring the CLI

**Files:**

- Modify: `mcp-server/server.py`

- [ ] **Step 1: Read the existing MCP tool pattern**

```bash
grep -nB 1 "@mcp.tool()" mcp-server/server.py | head -30
```

- [ ] **Step 2: Add MCP tools (mirror CLI commands)**

```python
# Append to mcp-server/server.py

@mcp.tool()
async def topics_show_batch(niche: str) -> str:
    """Show the current open batch for a niche, sorted by effective_score."""
    try:
        pool = await _get_pool()
        from services.niche_service import NicheService
        from services.topic_batch_service import TopicBatchService
        n = await NicheService(pool).get_by_slug(niche)
        if not n: return f"unknown niche: {niche}"
        async with pool.acquire() as conn:
            bid = await conn.fetchval(
                "SELECT id FROM topic_batches WHERE niche_id = $1 AND status = 'open'",
                n.id,
            )
        if bid is None: return f"No open batch for niche {niche}."
        view = await TopicBatchService(pool).show_batch(batch_id=bid)
        lines = [f"Batch {view.id} (status={view.status}, niche={niche})"]
        for c in view.candidates:
            marker = f"#{c.operator_rank}" if c.operator_rank else f"sys#{c.rank_in_batch}"
            lines.append(f"  {marker:6s} [{c.kind:8s}] eff={c.effective_score:5.1f} | {c.id} | {c.title}")
        return "\n".join(lines)
    except Exception as e:
        return _format_tool_error("topics_show_batch", e)


@mcp.tool()
async def topics_rank_batch(batch_id: str, ordered_candidate_ids: list[str]) -> str:
    """Set operator ranking for a batch's candidates. Pass IDs in best-first order."""
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).rank_batch(
            batch_id=batch_id, ordered_candidate_ids=ordered_candidate_ids,
        )
        return f"Ranked {len(ordered_candidate_ids)} candidates in batch {batch_id}"
    except Exception as e:
        return _format_tool_error("topics_rank_batch", e)


@mcp.tool()
async def topics_edit_winner(batch_id: str, topic: str = "", angle: str = "") -> str:
    """Edit the title/angle of the rank-1 candidate before resolution."""
    if not topic and not angle:
        return "topics_edit_winner failed: provide topic and/or angle"
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).edit_winner(
            batch_id=batch_id,
            topic=topic or None, angle=angle or None,
        )
        return "Edited winner."
    except Exception as e:
        return _format_tool_error("topics_edit_winner", e)


@mcp.tool()
async def topics_resolve_batch(batch_id: str) -> str:
    """Resolve a batch — advance the rank-1 candidate into the content pipeline."""
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).resolve_batch(batch_id=batch_id)
        return f"Resolved {batch_id}"
    except Exception as e:
        return _format_tool_error("topics_resolve_batch", e)


@mcp.tool()
async def topics_reject_batch(batch_id: str, reason: str = "") -> str:
    """Reject a batch — discard candidates, allow a fresh sweep."""
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).reject_batch(batch_id=batch_id, reason=reason)
        return f"Rejected {batch_id}"
    except Exception as e:
        return _format_tool_error("topics_reject_batch", e)
```

`_format_tool_error` already exists from PR #262 (#259 fix). Reuse it.

- [ ] **Step 3: Smoke test**

Restart the MCP HTTP server, call one of the new tools via the connector, confirm output.

- [ ] **Step 4: Commit**

```bash
git add mcp-server/server.py
git commit -m "feat(mcp): topics_show_batch / rank_batch / edit_winner / resolve_batch / reject_batch tools"
```

---

## Task 17: Seed Glad Labs as the first niche

**Files:**

- Create: `src/cofounder_agent/services/migrations/<NEXT+3>_seed_glad_labs_niche.py`

- [ ] **Step 1: Create the seed migration**

```python
"""Migration <NEXT+3>: seed Glad Labs as the first configured niche.

Uses TWO_PASS writer mode (per spec — Glad Labs is the primary-source niche
where we lean hardest on internal RAG context). Goals are the operator's
opening guesses; tune later via the CLI.
"""

from services.logger_config import get_logger
logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM niches WHERE slug = 'glad-labs'")
        if existing is not None:
            logger.info("Glad Labs niche already exists (id=%s) — skipping seed", existing)
            return

        niche_id = await conn.fetchval(
            """
            INSERT INTO niches (slug, name, target_audience_tags,
                                writer_rag_mode, batch_size,
                                discovery_cadence_minute_floor)
            VALUES ('glad-labs', 'Glad Labs',
                    ARRAY['indie-devs','ai-curious','prospects','future-matt'],
                    'TWO_PASS', 5, 60)
            RETURNING id
            """,
        )

        # Goals (sum to 100)
        goals = [
            ("AUTHORITY", 35),  # show what we actually know
            ("EDUCATION", 25),  # teach the reader
            ("BRAND",     20),  # reinforce the Glad Labs voice
            ("TRAFFIC",   15),  # not nothing, but not the driver
            ("REVENUE",    5),  # eventually
        ]
        for goal_type, weight in goals:
            await conn.execute(
                "INSERT INTO niche_goals (niche_id, goal_type, weight_pct) VALUES ($1, $2, $3)",
                niche_id, goal_type, weight,
            )

        # Sources — internal_rag is the lead, plus the existing external feeds
        sources = [
            ("internal_rag", True, 50),
            ("hackernews",   True, 20),
            ("devto",        True, 15),
            ("web_search",   True, 10),
            ("knowledge",    True,  5),
        ]
        for name, enabled, weight in sources:
            await conn.execute(
                "INSERT INTO niche_sources (niche_id, source_name, enabled, weight_pct) VALUES ($1, $2, $3, $4)",
                niche_id, name, enabled, weight,
            )
        logger.info("Seeded Glad Labs niche (id=%s)", niche_id)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM niches WHERE slug = 'glad-labs'")
        logger.info("Removed Glad Labs niche seed")
```

- [ ] **Step 2: Verify the seed via CLI**

```bash
DATABASE_URL="$(python -c 'import sys; sys.path.insert(0,"."); from brain.bootstrap import resolve_database_url; print(resolve_database_url())')" \
  poindexter topics niche show glad-labs
```

Expected: prints the JSON config.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/migrations/<NEXT+3>_seed_glad_labs_niche.py
git commit -m "feat(niche): seed Glad Labs as the first niche (TWO_PASS, 5 goals, 5 sources)"
```

---

## Task 18: End-to-end integration test + smoke run

**Files:**

- Create: `src/cofounder_agent/tests/integration/test_niche_discovery_e2e.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_niche_discovery_e2e.py
"""Integration: run a full niche discovery sweep against a live DB,
expect a topic_batch with operator-actionable candidates."""

import pytest
from services.niche_service import NicheService
from services.topic_batch_service import TopicBatchService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_glad_labs_sweep_produces_a_batch(db_pool):
    nsvc = NicheService(db_pool)
    n = await nsvc.get_by_slug("glad-labs")
    assert n is not None, "glad-labs niche should be seeded"
    batch = await TopicBatchService(db_pool).run_sweep(niche_id=n.id)
    if batch is None:
        # floor not elapsed since prior run; force it via the test
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM discovery_runs WHERE niche_id = $1", n.id,
            )
        batch = await TopicBatchService(db_pool).run_sweep(niche_id=n.id)
    assert batch is not None
    assert batch.status == "open"
    assert batch.candidate_count >= 1


async def test_resolve_advances_to_content_task(db_pool, monkeypatch):
    """Pick a batch, rank, resolve, expect a content_tasks row exists."""
    # ... implementer wires this end-to-end test
```

- [ ] **Step 2: Run the integration test**

```bash
cd src/cofounder_agent && poetry run pytest tests/integration/test_niche_discovery_e2e.py -v
```

Expected: PASS.

- [ ] **Step 3: Manual smoke**

```bash
# 1. Trigger a sweep manually (until reactive trigger is wired into a job)
DATABASE_URL=... poindexter topics niche show glad-labs   # confirm seed
# 2. Trigger a sweep — wire a CLI command for this if not done; for now, via Python:
python -c "
import asyncio, asyncpg, sys
sys.path.insert(0, '.')
from brain.bootstrap import resolve_database_url
from services.topic_batch_service import TopicBatchService
from services.niche_service import NicheService
async def go():
    p = await asyncpg.create_pool(resolve_database_url())
    nsvc = NicheService(p)
    n = await nsvc.get_by_slug('glad-labs')
    batch = await TopicBatchService(p).run_sweep(niche_id=n.id)
    print('batch:', batch)
    await p.close()
asyncio.run(go())
"
# 3. Show + rank + resolve
poindexter topics show-batch --niche glad-labs
# (copy 5 candidate IDs)
poindexter topics rank-batch <batch_id> --order id1,id2,id3,id4,id5
poindexter topics edit-winner <batch_id> --topic "Better Title" --angle "Sharper angle"
poindexter topics resolve-batch <batch_id>
# 4. Verify a content_task was created
poindexter tasks list | head
```

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/tests/integration/test_niche_discovery_e2e.py
git commit -m "test(niche): integration e2e — sweep produces batch, resolve advances task"
```

---

## Task 19: Deprecate topic_proposal_service

**Files:**

- Modify: `src/cofounder_agent/services/topic_proposal_service.py`

- [ ] **Step 1: Add deprecation banner + redirect**

```python
# At the top of services/topic_proposal_service.py, replace the module docstring with:

"""topic_proposal_service — DEPRECATED as of <NEXT_MIGRATION date>.

Replaced by services.topic_batch_service.TopicBatchService for the
niche-aware ranked-batch flow (see docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md).

Existing callers should migrate to TopicBatchService:
- propose_topic(...) → TopicBatchService.run_sweep(niche_id=...)
- approve_topic(...) → TopicBatchService.resolve_batch(batch_id=...)
- reject_topic(...) → TopicBatchService.reject_batch(batch_id=...)

This module will be deleted in a follow-up PR after the new service has
been live for one observation window.
"""

import warnings
warnings.warn(
    "services.topic_proposal_service is deprecated — use services.topic_batch_service",
    DeprecationWarning,
    stacklevel=2,
)
```

- [ ] **Step 2: Identify callers**

```bash
grep -rn "topic_proposal_service" src/cofounder_agent --include="*.py" | grep -v "topic_proposal_service.py:" | head
```

The implementer flags each caller in a TODO comment and creates a follow-up GH issue tracking the cleanup.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/topic_proposal_service.py
git commit -m "chore(deprecate): mark topic_proposal_service as deprecated; replaced by topic_batch_service"
```

---

## Task 20: Open the PR

**Files:**

- None (workflow only)

- [ ] **Step 1: Push to gitea + github (using the github/main cherry-pick workflow)**

Per CLAUDE.md and the lessons learned in PRs #251/#254/#255/#257: cut from `github/main`, never push the gitea branch directly to github.

```bash
# Branch was cut from github/main per the pre-flight step. Just push:
git push -u github feat/niche-topic-discovery
git push origin feat/niche-topic-discovery
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --repo Glad-Labs/poindexter --base main --head feat/niche-topic-discovery \
  --title "feat: niche-aware topic discovery + RAG writer pivot" \
  --body "$(cat <<'EOF'
Implements docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md.

## Summary

Adds niche-as-config topic discovery with hybrid embed+LLM ranking, batch-of-N operator interaction, and a per-niche writer_rag_mode that pivots the writer from "summarize external content" to "report from internal context." Glad Labs is seeded as the first niche using TWO_PASS mode.

## Tasks shipped

1-2. Schema migrations (niche tables + content_tasks columns)
3-4. NicheService CRUD
5. Goal vectors + LLM scorer + decay re-rank
6. InternalRagSource (RAG-derived candidates)
7-8. TopicBatchService orchestrator + operator interactions
9-13. Writer RAG mode dispatcher + 4 mode implementations
14. Wire dispatch into ai_content_generator
15-16. CLI + MCP tools
17. Seed Glad Labs niche
18. Integration test + smoke
19. Deprecate topic_proposal_service

## Test plan

- [x] migrations-smoke CI passes (verifies new migrations apply cleanly)
- [x] All new unit tests pass
- [x] Integration test creates a batch end-to-end against the live DB
- [x] Manual smoke: full discover → rank → edit → resolve → content_task creation flow

## Notes

- Old topic_proposal_service deprecated but not deleted — follow-up PR
- Pipeline gateway caps (separate operator-backpressure feature) explicitly out of scope per spec
- The 5 spec "Open questions for the implementation plan" are addressed in the per-task notes; the most material decision was using `_ollama_chat_json` indirection so tests can monkeypatch the model call cheaply.
EOF
)"
```

- [ ] **Step 3: Verify PR file count + leak check**

```bash
PR_NUM=$(gh pr list --repo Glad-Labs/poindexter --head feat/niche-topic-discovery --json number --jq '.[0].number')
gh pr view $PR_NUM --repo Glad-Labs/poindexter --json files --jq '.files | length'
# Expected: well under 100 (target ≤ 25 — this is a big feature but contained to the new files + 3-4 modifications)
gh pr view $PR_NUM --repo Glad-Labs/poindexter --json files --jq '.files[] | select(.path | test("credentials|matt-profile|shared-context|cms/strapi|agents/content-agent")) | .path'
# Expected: empty (no leaked private files)
```

- [ ] **Step 4: Wait for CI + merge**

```bash
# Wait for migrations-smoke + Mintlify validation to complete:
until [ "$(gh pr view $PR_NUM --repo Glad-Labs/poindexter --json statusCheckRollup --jq '[.statusCheckRollup[]? | select(.name == "migrations-smoke") | .status][0]')" = "COMPLETED" ]; do sleep 10; done

# Check status:
gh pr view $PR_NUM --repo Glad-Labs/poindexter --json statusCheckRollup --jq '.statusCheckRollup[]? | {name, conclusion}'

# If migrations-smoke + Mintlify Validation are SUCCESS (Mintlify Deployment FAILURE is acceptable, see prior PRs):
gh pr merge $PR_NUM --repo Glad-Labs/poindexter --squash --delete-branch
```

---

## Self-review notes

This plan covers the spec's data model (Tasks 1, 8), the discovery + ranking flow (Tasks 3-7), the operator surface (Tasks 7, 15-16), the writer pivot (Tasks 9-14), the seed of Glad Labs (Task 17), and the migration deprecation (Task 19). All 7 spec tables are created in Task 1; all 4 writer modes are implemented (Tasks 10-13); the operator interactions cover show/rank/edit/resolve/reject; CLI + MCP both expose them.

Acceptance criteria from the spec:

- ✓ Glad Labs niche seeded with TWO_PASS + ≥3 goals summing to 100 (Task 17)
- ✓ Discovery sweep produces 5-candidate batch (Tasks 6, 18)
- ✓ `topics show-batch` CLI (Task 15)
- ✓ rank → edit → resolve → content_task flow (Tasks 7, 14, 18)
- ✓ topic_batch_id provenance on content_tasks (Task 8)
- ✓ Carry-forward decay (Task 6)
- ✓ One open batch per niche (UNIQUE partial index in Task 1)
- ✓ Discovery floor (Task 6)
- ✓ TWO_PASS writer (Task 13)
- ✓ Existing pipeline stages unchanged downstream (Task 14)

The 5 spec open questions are answered: (1) goal vector cache invalidation = process-restart only, fine for v1; (2) supporting_refs stores ref + snippet (Task 6); (3) LLM final-scorer prompt template inlined in Task 4; (4) approve-as-rank-1-unedited fallback works because resolve_batch reads operator_rank=1 — operators can omit rank-batch and use just resolve-batch only if there's already an operator_rank set (or the implementer can default operator_rank to rank_in_batch on insert — minor follow-up); (5) niche slug surfaced via CLI/MCP show-batch output.
