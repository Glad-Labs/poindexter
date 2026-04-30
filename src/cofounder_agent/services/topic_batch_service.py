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

See spec §"Flow" + §"Discovery sweep" in
docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md.

Implementation note — lazy imports
==================================
Both ``embed_text`` and ``llm_final_score`` are imported *inside* the
methods that use them rather than at module top-level. This keeps the
test seam intact: ``monkeypatch.setattr("services.topic_ranking.embed_text", ...)``
needs to patch the source module before it's bound into our namespace,
otherwise the test would have to patch
``services.topic_batch_service.embed_text`` instead. Lazy imports give
test authors the cleaner patch path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from services.logger_config import get_logger
from services.niche_service import Niche, NicheService
from services.topic_ranking import (
    ScoredCandidate,
    apply_decay,
    goal_vector_for,
    weighted_cosine_score,
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
            logger.info(
                "Sweep skipped — discovery cadence floor not elapsed for niche %s",
                niche.slug,
            )
            return None
        if await self._open_batch_exists(niche.id):
            logger.info(
                "Sweep skipped — open batch already exists for niche %s",
                niche.slug,
            )
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

            pool_external, pool_internal = await self._embed_and_pre_rank(
                niche,
                external + carried["external"],
                internal + carried["internal"],
            )

            top10 = pool_external[:5] + pool_internal[:5]

            # Lazy import so tests can patch services.topic_ranking.llm_final_score.
            from services.topic_ranking import llm_final_score

            goals = await self._niche_svc.get_goals(niche.id)
            scored = await llm_final_score(top10, goals)

            ranked = sorted(
                scored.values(), key=lambda c: -(c.llm_score or 0)
            )[: niche.batch_size]

            batch = await self._write_batch(
                niche, ranked, pool_external, pool_internal,
            )

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
        except Exception as exc:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE discovery_runs SET finished_at = NOW(), error = $1 WHERE id = $2",
                    str(exc),
                    run_id,
                )
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _floor_elapsed(self, niche: Niche) -> bool:
        """True if enough time has passed since the last sweep for this niche.

        Compares against ``MAX(started_at)`` — even an in-flight or errored
        run counts toward the floor so we don't hammer external sources
        when something's wedged. Excludes the current run because this
        check runs *before* the discovery_runs INSERT.
        """
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
                "SELECT count(*) FROM topic_batches "
                "WHERE niche_id = $1 AND status = 'open'",
                niche_id,
            )
        return (count or 0) > 0

    async def _discover_external(self, niche: Niche) -> list[dict[str, Any]]:
        """Call existing topic_discovery wiring for non-internal sources.

        TODO(Task 6 follow-up): wire to ``services.topic_discovery.TopicDiscovery``
        once the per-niche source plugin filter is exposed. Today we log
        a warning (so misconfiguration surfaces) and return [].
        """
        sources = await self._niche_svc.get_sources(niche.id)
        external_sources = [
            s for s in sources if s.enabled and s.source_name != "internal_rag"
        ]
        if external_sources:
            logger.warning(
                "Niche %s has %d external source(s) configured but topic_discovery "
                "wiring is not yet implemented in TopicBatchService — returning [] "
                "(TODO follow-up task)",
                niche.slug,
                len(external_sources),
            )
        return []

    async def _discover_internal(self, niche: Niche) -> list[dict[str, Any]]:
        sources = await self._niche_svc.get_sources(niche.id)
        if not any(
            s.source_name == "internal_rag" and s.enabled for s in sources
        ):
            return []
        from services.internal_rag_source import InternalRagSource

        rag = InternalRagSource(self._pool)
        # Per spec: per-kind limit 4, all 6 valid kinds except git_commit
        # (which still needs git-log plumbing).
        kinds = [
            "claude_session",
            "brain_knowledge",
            "audit_event",
            "decision_log",
            "memory_file",
            "post_history",
        ]
        cands = await rag.generate(
            niche_id=niche.id, source_kinds=kinds, per_kind_limit=4,
        )
        return [{"kind": "internal", "data": c} for c in cands]

    async def _load_carry_forward(self, niche_id: UUID) -> dict[str, list]:
        """Pull unpicked candidates from the most recent resolved batch and
        return them with ``decay_factor`` pre-multiplied by 0.7.

        The 0.7 decay multiplier on each carry-forward implements the spec's
        "older candidates lose weight" — by the third batch a candidate has
        decayed to 0.7^3 ≈ 0.343 of its original score.
        """
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
        return {
            "external": [
                {"row": dict(r), "decay_factor": float(r["decay_factor"]) * 0.7}
                for r in ext
            ],
            "internal": [
                {"row": dict(r), "decay_factor": float(r["decay_factor"]) * 0.7}
                for r in int_
            ],
        }

    async def _embed_and_pre_rank(
        self,
        niche: Niche,
        external: list,
        internal: list,
    ) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
        """Compute embedding + weighted cosine score per candidate.

        Returns (top external, top internal) — top 5 each, sorted by score
        descending.

        ``embed_text`` is imported lazily so
        ``monkeypatch.setattr("services.topic_ranking.embed_text", ...)``
        works without reaching into this module's namespace.
        """
        from services.topic_ranking import embed_text

        goals = await self._niche_svc.get_goals(niche.id)
        goal_vecs = {
            g.goal_type: await goal_vector_for(g.goal_type) for g in goals
        }

        async def score_one(text: str, decay: float) -> tuple[float, dict[str, float]]:
            vec = await embed_text(text)
            raw, breakdown = weighted_cosine_score(vec, goal_vecs, goals)
            return apply_decay(score=raw, decay_factor=decay), breakdown

        ext_scored: list[ScoredCandidate] = []
        for item in external:
            # External candidates are dicts (carry-forward rows or plugin
            # output). Carry-forward rows have a "row" key; fresh discovery
            # output is the dict itself.
            row = item.get("row") if isinstance(item, dict) and "row" in item else item
            text = (row.get("title") or "") + " " + (row.get("summary") or "")
            decay = item.get("decay_factor", 1.0) if isinstance(item, dict) else 1.0
            score, breakdown = await score_one(text, decay)
            ext_scored.append(
                ScoredCandidate(
                    id=str(row.get("id") or row.get("source_ref") or text[:40]),
                    title=row.get("title") or "Untitled",
                    summary=row.get("summary"),
                    embedding_score=score,
                    score_breakdown=breakdown,
                )
            )

        int_scored: list[ScoredCandidate] = []
        for item in internal:
            data = item["data"] if isinstance(item, dict) and "data" in item else item.get("row", item)
            decay = item.get("decay_factor", 1.0) if isinstance(item, dict) else 1.0

            # ``data`` is either an InternalCandidate dataclass (fresh) or a
            # row dict (carry-forward). Reach in via getattr-with-fallback
            # so both shapes work.
            def _field(name: str, default: str = "") -> str:
                if hasattr(data, name):
                    return getattr(data, name) or default
                if isinstance(data, dict):
                    return data.get(name) or default
                return default

            topic = _field("distilled_topic", "")
            angle = _field("distilled_angle", "")
            text = (topic + " " + angle).strip()
            score, breakdown = await score_one(text, decay)

            primary_ref = _field("primary_ref", text[:40])
            int_scored.append(
                ScoredCandidate(
                    id=str(primary_ref or text[:40]),
                    title=topic or "Untitled",
                    summary=angle or None,
                    embedding_score=score,
                    score_breakdown=breakdown,
                )
            )

        ext_scored.sort(key=lambda c: -c.embedding_score)
        int_scored.sort(key=lambda c: -c.embedding_score)
        return ext_scored[:5], int_scored[:5]

    async def _write_batch(
        self,
        niche: Niche,
        ranked: list[ScoredCandidate],
        all_external: list,
        all_internal: list,
    ) -> BatchSnapshot:
        """Create the topic_batches row + per-candidate rows in the
        appropriate table (external vs internal).

        Determines the destination table by checking which pre-rank pool
        the candidate's id appears in. The two pools are disjoint by
        construction (external candidates carry source_ref-derived ids;
        internal candidates carry primary_ref-derived ids).

        ``all_external`` and ``all_internal`` are
        ``list[ScoredCandidate]`` — the embed-pre-rank output. We just
        need their ids to route each ranked candidate to the right table.
        """
        internal_ids: set[str] = {str(sc.id) for sc in all_internal}

        expires = datetime.now(timezone.utc) + timedelta(days=7)
        rank_in_batch = 0

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                batch_row = await conn.fetchrow(
                    "INSERT INTO topic_batches (niche_id, status, expires_at) "
                    "VALUES ($1, 'open', $2) RETURNING *",
                    niche.id,
                    expires,
                )
                for c in ranked:
                    rank_in_batch += 1
                    is_internal = c.id in internal_ids
                    if is_internal:
                        await conn.execute(
                            """
                            INSERT INTO internal_topic_candidates
                              (batch_id, niche_id, source_kind, primary_ref,
                               supporting_refs, distilled_topic, distilled_angle,
                               score, score_breakdown, rank_in_batch, decay_factor)
                            VALUES ($1, $2, $3, $4, '[]'::jsonb, $5, $6, $7, $8::jsonb, $9, $10)
                            """,
                            batch_row["id"],
                            niche.id,
                            "claude_session",
                            c.id,
                            c.title,
                            c.summary or "",
                            c.llm_score or 0,
                            _json(c.score_breakdown or {}),
                            rank_in_batch,
                            1.0,
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO topic_candidates
                              (batch_id, niche_id, source_name, source_ref, title, summary,
                               score, score_breakdown, rank_in_batch, decay_factor)
                            VALUES ($1, $2, 'external', $3, $4, $5, $6, $7::jsonb, $8, $9)
                            """,
                            batch_row["id"],
                            niche.id,
                            c.id,
                            c.title,
                            c.summary,
                            c.llm_score or 0,
                            _json(c.score_breakdown or {}),
                            rank_in_batch,
                            1.0,
                        )

        return BatchSnapshot(
            id=batch_row["id"],
            niche_id=batch_row["niche_id"],
            status=batch_row["status"],
            candidate_count=rank_in_batch,
            expires_at=batch_row["expires_at"],
        )

    async def _open_topic_decision_gate(self, batch: BatchSnapshot) -> None:
        """Open the operator approval gate for this new batch.

        TODO(Task 6 follow-up): wire to ``services.approval_service`` once
        the topic_decision gate API stabilises. Today this is a structured
        log line so observability picks it up.
        """
        logger.info("Opened topic_decision gate for batch %s", batch.id)


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str)
