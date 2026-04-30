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
