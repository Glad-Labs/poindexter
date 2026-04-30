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


@dataclass
class CandidateView:
    """Unified view of a candidate row across the external/internal
    candidate tables — used by ``show_batch`` so the operator sees one
    flat ranked list regardless of source table.

    ``effective_score = score * decay_factor`` is precomputed so callers
    don't have to repeat the multiply.
    """

    id: str
    kind: str  # 'external' | 'internal'
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
    """Operator-facing snapshot of a batch + every candidate, sorted by
    ``effective_score`` desc."""

    id: UUID
    niche_id: UUID
    status: str
    picked_candidate_id: UUID | None
    candidates: list[CandidateView]


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

            # Top-N per pool is operator-tunable via niche_top_n_per_pool
            # (migration 0119). Default 5 matches the prior hardcoded
            # slice — pre-rank + LLM-final-score then sees up to 2*N
            # candidates. Same key gates the slice inside
            # _embed_and_pre_rank so both ends of the funnel stay consistent.
            from services.site_config import site_config
            top_n = site_config.get_int("niche_top_n_per_pool", 5)
            top10 = pool_external[:top_n] + pool_internal[:top_n]

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
        from services.site_config import site_config

        rag = InternalRagSource(self._pool)
        # Per spec: per-kind limit defaults to 4, all 6 valid kinds
        # except git_commit (which still needs git-log plumbing).
        # Operator-tunable via niche_internal_rag_per_kind_limit
        # (migration 0119).
        kinds = [
            "claude_session",
            "brain_knowledge",
            "audit_event",
            "decision_log",
            "memory_file",
            "post_history",
        ]
        per_kind_limit = site_config.get_int(
            "niche_internal_rag_per_kind_limit", 4,
        )
        cands = await rag.generate(
            niche_id=niche.id,
            source_kinds=kinds,
            per_kind_limit=per_kind_limit,
        )
        return [{"kind": "internal", "data": c} for c in cands]

    async def _load_carry_forward(self, niche_id: UUID) -> dict[str, list]:
        """Pull unpicked candidates from the most recent resolved batch and
        return them with ``decay_factor`` pre-multiplied by the configured
        carry-forward decay factor (default 0.7).

        The decay multiplier on each carry-forward implements the spec's
        "older candidates lose weight" — by the third batch a candidate
        has decayed to factor^3 of its original score (0.7^3 ≈ 0.343 at
        the default). Tunable via
        ``niche_carry_forward_decay_factor`` (migration 0119).
        """
        from services.site_config import site_config

        decay = site_config.get_float(
            "niche_carry_forward_decay_factor", 0.7,
        )
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
                {"row": dict(r), "decay_factor": float(r["decay_factor"]) * decay}
                for r in ext
            ],
            "internal": [
                {"row": dict(r), "decay_factor": float(r["decay_factor"]) * decay}
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
            row = item["row"] if isinstance(item, dict) and "row" in item else item
            assert row is not None
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
        # Same niche_top_n_per_pool key the run_sweep funnel uses so the
        # two ends of the pre-rank/final-score pipeline stay in lockstep.
        from services.site_config import site_config
        top_n = site_config.get_int("niche_top_n_per_pool", 5)
        return ext_scored[:top_n], int_scored[:top_n]

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

        # Batch lifetime — operator-tunable via niche_batch_expires_days
        # (migration 0119). Default 7 matches the prior hardcoded value.
        from services.site_config import site_config
        expires_days = site_config.get_int("niche_batch_expires_days", 7)
        expires = datetime.now(timezone.utc) + timedelta(days=expires_days)
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

    # ------------------------------------------------------------------
    # Operator interactions (Task 7)
    # ------------------------------------------------------------------

    async def show_batch(self, *, batch_id: UUID) -> BatchView:
        """Return a unified, ranked view of the batch + all candidates.

        Merges rows from ``topic_candidates`` and
        ``internal_topic_candidates`` so the operator UI doesn't have to
        know which table a candidate lives in. Sorted by
        ``effective_score`` (= score × decay_factor) desc so freshly
        scored picks float to the top and decayed carry-forwards drift
        down naturally.
        """
        async with self._pool.acquire() as conn:
            batch = await conn.fetchrow(
                "SELECT * FROM topic_batches WHERE id = $1", batch_id,
            )
            if batch is None:
                raise ValueError(f"unknown batch_id: {batch_id}")
            ext_rows = await conn.fetch(
                "SELECT * FROM topic_candidates WHERE batch_id = $1", batch_id,
            )
            int_rows = await conn.fetch(
                "SELECT * FROM internal_topic_candidates WHERE batch_id = $1",
                batch_id,
            )

        cands: list[CandidateView] = []
        for r in ext_rows:
            score = float(r["score"])
            df = float(r["decay_factor"])
            cands.append(
                CandidateView(
                    id=str(r["id"]),
                    kind="external",
                    title=r["title"],
                    summary=r["summary"],
                    score=score,
                    decay_factor=df,
                    effective_score=score * df,
                    rank_in_batch=r["rank_in_batch"],
                    operator_rank=r["operator_rank"],
                    operator_edited_topic=r["operator_edited_topic"],
                    operator_edited_angle=r["operator_edited_angle"],
                    score_breakdown=_loads(r["score_breakdown"]) or {},
                )
            )
        for r in int_rows:
            score = float(r["score"])
            df = float(r["decay_factor"])
            cands.append(
                CandidateView(
                    id=str(r["id"]),
                    kind="internal",
                    title=r["distilled_topic"],
                    summary=r["distilled_angle"],
                    score=score,
                    decay_factor=df,
                    effective_score=score * df,
                    rank_in_batch=r["rank_in_batch"],
                    operator_rank=r["operator_rank"],
                    operator_edited_topic=r["operator_edited_topic"],
                    operator_edited_angle=r["operator_edited_angle"],
                    score_breakdown=_loads(r["score_breakdown"]) or {},
                )
            )
        cands.sort(key=lambda c: -c.effective_score)
        return BatchView(
            id=batch["id"],
            niche_id=batch["niche_id"],
            status=batch["status"],
            picked_candidate_id=batch["picked_candidate_id"],
            candidates=cands,
        )

    async def rank_batch(
        self, *, batch_id: UUID, ordered_candidate_ids: list[str],
    ) -> None:
        """Set ``operator_rank`` on each candidate by its 1-based position
        in ``ordered_candidate_ids``.

        Tries ``topic_candidates`` first; if the UPDATE affected zero
        rows, falls back to ``internal_topic_candidates``. asyncpg
        returns the SQL command tag (e.g. ``"UPDATE 1"`` /
        ``"UPDATE 0"``) — we sniff the trailing digit to decide whether
        to fall through.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for rank, cand_id in enumerate(ordered_candidate_ids, start=1):
                    updated = await conn.execute(
                        "UPDATE topic_candidates SET operator_rank = $1 "
                        "WHERE id = $2 AND batch_id = $3",
                        rank, cand_id, batch_id,
                    )
                    if updated.endswith("0"):
                        await conn.execute(
                            "UPDATE internal_topic_candidates "
                            "SET operator_rank = $1 "
                            "WHERE id = $2 AND batch_id = $3",
                            rank, cand_id, batch_id,
                        )

    async def edit_winner(
        self,
        *,
        batch_id: UUID,
        topic: str | None = None,
        angle: str | None = None,
    ) -> None:
        """Edit the candidate currently ranked #1 by the operator.

        Probes ``topic_candidates`` first; falls back to
        ``internal_topic_candidates`` if no rank-1 row is found there.
        Raises ``ValueError`` if neither table has a rank-1 candidate so
        callers can prompt for ranking before editing.
        """
        async with self._pool.acquire() as conn:
            rid = await conn.fetchval(
                "SELECT id FROM topic_candidates "
                "WHERE batch_id = $1 AND operator_rank = 1",
                batch_id,
            )
            tbl = "topic_candidates"
            if rid is None:
                rid = await conn.fetchval(
                    "SELECT id FROM internal_topic_candidates "
                    "WHERE batch_id = $1 AND operator_rank = 1",
                    batch_id,
                )
                tbl = "internal_topic_candidates"
            if rid is None:
                raise ValueError(
                    f"no rank-1 candidate in batch {batch_id}; rank first",
                )
            await conn.execute(
                f"UPDATE {tbl} "
                "SET operator_edited_topic = $1, operator_edited_angle = $2 "
                "WHERE id = $3",
                topic, angle, rid,
            )

    async def resolve_batch(self, *, batch_id: UUID) -> None:
        """Resolve the batch: hand the rank-1 candidate to the content
        pipeline, then mark the batch ``resolved`` + record provenance.

        Raises ``ValueError`` if no operator_rank=1 candidate exists.
        """
        view = await self.show_batch(batch_id=batch_id)
        winner = next(
            (c for c in view.candidates if c.operator_rank == 1), None,
        )
        if winner is None:
            raise ValueError("no operator_rank=1 candidate; rank first")
        niche = await self._niche_svc.get_by_id(view.niche_id)
        # Pass batch_id explicitly so content_tasks.topic_batch_id
        # provenance points at the batch (not the candidate). The
        # candidate id can be reconstructed from picked_candidate_id +
        # picked_candidate_kind on the batch row itself.
        await self._handoff_to_pipeline(winner, niche, batch_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE topic_batches
                   SET status = 'resolved',
                       resolved_at = NOW(),
                       picked_candidate_id = $1,
                       picked_candidate_kind = $2
                 WHERE id = $3
                """,
                winner.id, winner.kind, batch_id,
            )
        logger.info(
            "Batch %s resolved (winner=%s kind=%s)",
            batch_id, winner.id, winner.kind,
        )

    async def reject_batch(
        self, *, batch_id: UUID, reason: str = "",
    ) -> None:
        """Reject the batch — flips status to ``expired`` and records
        ``resolved_at`` so the partial unique index frees up the
        one-open-batch-per-niche slot for the next sweep."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE topic_batches "
                "SET status = 'expired', resolved_at = NOW() WHERE id = $1",
                batch_id,
            )
        logger.info("Batch %s rejected (reason=%r)", batch_id, reason)

    async def _handoff_to_pipeline(
        self,
        winner: CandidateView,
        niche: Niche,
        batch_id: UUID,
    ) -> None:
        """Advance the winning candidate into the existing content
        pipeline by inserting a ``content_tasks`` row.

        Uses the operator-edited topic/angle when present, otherwise
        falls back to the candidate's title/summary.

        ``topic_batch_id`` carries the BATCH id (provenance pointer back
        to the batch the topic came from), NOT the candidate id — the
        plan body originally threaded ``winner.id`` here, which would
        have made provenance point at the candidate row that owns the
        topic and broken the FK to ``topic_batches``.
        """
        topic = winner.operator_edited_topic or winner.title
        angle = winner.operator_edited_angle or winner.summary or ""
        # task_id, task_type, and content_type are NOT NULL on
        # content_tasks; the legacy code path (topic_discovery.py:344)
        # supplies task_id via gen_random_uuid()::text and sets both
        # task_type and content_type to 'blog_post'. Mirror that here so
        # the INSERT doesn't trip NotNullViolationError at runtime.
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO content_tasks
                  (task_id, task_type, content_type,
                   topic, description, status, stage,
                   niche_slug, writer_rag_mode, topic_batch_id)
                VALUES (gen_random_uuid()::text, 'blog_post', 'blog_post',
                        $1, $2, 'pending', 'pending', $3, $4, $5)
                """,
                topic, angle, niche.slug, niche.writer_rag_mode, batch_id,
            )


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _loads(value: Any) -> dict[str, float] | None:
    """Coerce a JSONB column value into a plain dict.

    asyncpg returns JSONB as already-decoded dicts in most setups, but
    falls back to raw strings if the codec isn't registered. Handle
    both shapes so ``show_batch`` doesn't depend on connection-level
    codec config.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None
