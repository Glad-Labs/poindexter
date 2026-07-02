"""Topic batch orchestrator — replaces topic_proposal_service.

Per-niche flow:
  1. discover candidates (external source plugins + InternalRagSource) per
     niche_sources weights → pool of ~20
  2. carry-forward leftover candidates from prior batch with decay
  3. dedup the combined pool (intra-batch + vs-existing) via
     get_deduplicator() — mirrors the legacy TopicDiscovery._deduplicate
     pass the niche-batch rewrite originally dropped
  4. embedding pre-rank against goal vectors → top 10
  5. LLM final-score on the top 10 → final batch_size winners
  6. write topic_batch + topic_candidates / internal_topic_candidates rows
  7. open the topic_decision approval gate
  8. record discovery_run

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
from services.site_config import SiteConfig
from services.topic_length import pick_target_length
from services.topic_ranking import (
    ScoredCandidate,
    apply_decay,
    goal_vector_for,
    weighted_cosine_score,
)
from services.topic_sanity import (
    TopicSanityError,
    evaluate_topic_sanity,
    resolve_min_alpha_words,
)
from utils.findings import emit_finding

# #272 Phase-2d: the module-level ``site_config`` global + ``set_site_config``
# setter were removed. ``TopicBatchService`` now REQUIRES a keyword-only
# ``site_config`` in its constructor; construction sites thread it
# (jobs read ``config.get("_site_config")``, the CLI passes
# ``container.site_config``, tests pass ``SiteConfig()``). The internal
# ``topic_ranking`` helpers receive ``self._site_config``.

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


@dataclass
class OpenBatch:
    """An open batch + its resolved niche metadata — the unit the operator
    console's topic-triage surface lists.

    Wraps ``BatchView`` (the merged, effective-score-sorted candidate view)
    with the niche slug/name so the UI can label each batch without a second
    round-trip. ``niche_slug`` / ``niche_name`` are ``None`` only if the niche
    row vanished out from under an orphaned batch.
    """

    view: BatchView
    niche_slug: str | None
    niche_name: str | None


class _DedupCandidate:
    """Mutable ``_TopicLike`` adapter the deduplicators operate on.

    The dedup engines (``services.topic_dedup.TopicDeduplicator`` /
    ``SemanticDeduplicator``) mark ``.is_duplicate`` in place on objects
    exposing ``.title``. The niche-batch candidates are dicts of several
    shapes (fresh ``{"data": ...}`` vs carry-forward ``{"row": ...}``), so
    we wrap each one — remembering its pool and the original item — for the
    pass, then rebuild the filtered pools from the survivors.
    """

    __slots__ = ("title", "is_duplicate", "pool", "item")

    def __init__(self, title: str, pool: str, item: Any) -> None:
        self.title = title
        self.is_duplicate = False
        self.pool = pool  # "external" | "internal"
        self.item = item


class TopicBatchService:
    def __init__(self, pool, *, site_config: SiteConfig):
        self._pool = pool
        self._niche_svc = NicheService(pool)
        # #272 Phase-2d: injection is mandatory — no module-global fallback.
        self._site_config = site_config

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
            # Internal discovery is best-effort: a failure here (e.g. an
            # LLM distill call returning empty/unparseable JSON) must NOT
            # sink the whole sweep and discard the external candidates we
            # already gathered. This was the 2026-05-28 content-gen stall —
            # one empty json.loads in _discover_internal bubbled out of
            # run_sweep, so no batch formed for ~2 days even though external
            # taps were returning candidates fine.
            try:
                internal = await self._discover_internal(niche)
            except Exception:
                logger.warning(
                    "Niche %s: internal RAG discovery failed — proceeding "
                    "with external candidates only this sweep",
                    niche.slug, exc_info=True,
                )
                internal = []
            carried = await self._load_carry_forward(niche.id)

            # Dedup BEFORE embed/pre-rank so duplicates never reach a batch
            # (and we don't pay to embed them). This mirrors the dedup pass
            # the legacy ``TopicDiscovery._deduplicate`` runs — the niche-
            # batch rewrite that replaced topic_proposal_service dropped it,
            # so batches were coming back with duplicate candidates (e.g.
            # internal RAG distilling one theme from two source rows landed
            # "operator surface unreachability" in a single batch x3).
            combined_external, combined_internal = await self._dedupe_candidates(
                external + carried["external"],
                internal + carried["internal"],
            )

            # Deterministic topic-sanity filter BEFORE embed/rank, so a
            # contentless title (2026-06-30: a dots-only dev.to headline
            # the LLM scorer then ranked TOP of its batch) never occupies
            # a batch slot, is never embedded, and is never carried
            # forward. Loud, not silent — emits one aggregated
            # topic_sanity_rejected finding per sweep.
            combined_external, combined_internal = (
                self._drop_contentless_candidates(
                    niche, combined_external, combined_internal,
                )
            )

            pool_external, pool_internal = await self._embed_and_pre_rank(
                niche,
                combined_external,
                combined_internal,
            )

            # Top-N per pool is operator-tunable via niche_top_n_per_pool
            # (migration 0119). Default 5 matches the prior hardcoded
            # slice — pre-rank + LLM-final-score then sees up to 2*N
            # candidates. Same key gates the slice inside
            # _embed_and_pre_rank so both ends of the funnel stay consistent.
            top_n = self._site_config.get_int("niche_top_n_per_pool", 5)
            top10 = pool_external[:top_n] + pool_internal[:top_n]

            # Lazy import so tests can patch services.topic_ranking.llm_final_score.
            from services.topic_ranking import llm_final_score

            goals = await self._niche_svc.get_goals(niche.id)
            # #272 Phase-2d: topic_ranking has no module global — pass the
            # DI-injected ``self._site_config``.
            scored = await llm_final_score(top10, goals, site_config=self._site_config)

            # Cap internal_rag's share of the batch so the system-introspection
            # corpus (claude_sessions / brain / audit / memory) can't crowd out
            # external/consumer topics — finding #5 of the 2026-06-19 validation,
            # where internal_rag took 4/5 slots and the operator had to hand-edit
            # the winner to a real consumer topic. Internal candidates are known
            # by their pool; the cap is operator-tunable (1.0 disables it) and
            # backfills so a thin-external sweep still produces a full batch.
            internal_ids = {c.id for c in pool_internal}
            internal_share_cap = self._site_config.get_float(
                "niche_internal_rag_batch_share_cap", 0.5,
            )
            ranked = self._apply_source_diversity_cap(
                list(scored.values()),
                internal_ids,
                niche.batch_size,
                internal_share_cap,
            )

            # Guard against the empty-batch wedge. If discovery + ranking
            # yielded nothing this sweep (every source dry, everything
            # deduped away, or the LLM final-score returned no usable
            # rows), do NOT persist an empty ``open`` batch. A
            # candidate-less open batch can never be resolved, yet
            # ``_open_batch_exists`` would then short-circuit every future
            # sweep for this niche — a silent, multi-day content stall (the
            # shape of the 2026-05-28 and 2026-06-11 incidents). Record the
            # empty run on ``discovery_runs`` for observability and bail;
            # because no open batch is left behind, the next sweep retries
            # cleanly once the underlying source/LLM issue clears.
            if not ranked:
                logger.warning(
                    "Niche %s: sweep produced 0 rankable candidates "
                    "(external=%d internal=%d) — suppressing empty-batch "
                    "creation so the niche isn't wedged",
                    niche.slug, len(external), len(internal),
                )
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE discovery_runs SET finished_at = NOW(), "
                        "candidates_generated = $1, error = $2 WHERE id = $3",
                        len(external) + len(internal),
                        "no rankable candidates — empty batch suppressed",
                        run_id,
                    )
                return None

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

    @staticmethod
    def _apply_source_diversity_cap(
        scored_candidates: list,
        internal_ids: set[str],
        batch_size: int,
        internal_share_cap: float,
    ) -> list:
        """Pick up to ``batch_size`` candidates by LLM score while capping the
        internal_rag share, so external/consumer sources are guaranteed batch
        representation (finding #5: internal_rag took 4/5 slots on 2026-06-19).

        SOFT cap. Pass 1 walks candidates best-score-first and admits internal
        ones only until ``floor(batch_size * cap)`` are taken — external fills
        the rest. Pass 2 backfills any slots still empty (external was too thin)
        with the best remaining candidates, so a sweep never under-fills the
        batch and a single-source (internal-only) batch is identical to the old
        plain top-N-by-score slice. ``cap >= 1.0`` disables the cap.

        ``internal_ids`` is the set of ``ScoredCandidate.id`` values from the
        internal pool — the caller knows each candidate's source by its pool.
        """
        ranked = sorted(scored_candidates, key=lambda c: -(c.llm_score or 0))
        if internal_share_cap >= 1.0 or batch_size <= 0:
            return ranked[:batch_size]
        max_internal = int(batch_size * internal_share_cap)
        picked: list = []
        internal_taken = 0
        for c in ranked:  # pass 1 — honour the cap
            if len(picked) >= batch_size:
                break
            if c.id in internal_ids and internal_taken >= max_internal:
                continue
            picked.append(c)
            if c.id in internal_ids:
                internal_taken += 1
        if len(picked) < batch_size:  # pass 2 — backfill rather than under-fill
            chosen = {id(c) for c in picked}
            for c in ranked:
                if len(picked) >= batch_size:
                    break
                if id(c) not in chosen:
                    picked.append(c)
        return picked

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
        """Dispatch every enabled non-internal_rag TopicSource plugin for
        the niche and aggregate the resulting DiscoveredTopics into the
        ``{"kind": "external", "data": {...}}`` shape consumed by
        ``_embed_and_pre_rank``."""
        sources = await self._niche_svc.get_sources(niche.id)
        external_sources = [
            s for s in sources if s.enabled and s.source_name != "internal_rag"
        ]
        if not external_sources:
            return []

        # Lazy imports — keeps the registry import out of cold paths and
        # matches the rest of this file's style.
        from plugins.config import PluginConfig
        from plugins.registry import get_topic_sources
        # Index registry by name so we can match niche-source rows to
        # plugin instances. A niche may legitimately reference a source
        # name that's not registered (legacy config, plugin uninstalled,
        # typo) — log + skip rather than blow up the whole sweep.
        registry = {
            getattr(p, "name", type(p).__name__): p for p in get_topic_sources()
        }

        candidates: list[dict[str, Any]] = []
        for source in external_sources:
            plugin = registry.get(source.source_name)
            if plugin is None:
                logger.warning(
                    "Niche %s references TopicSource %r which is not "
                    "registered — skipping. Check plugin install or rename.",
                    niche.slug,
                    source.source_name,
                )
                continue

            # Per-source config — the same plugin.topic_source.<name> row
            # the standalone runner reads. Layered with the niche-aware
            # context the source needs to scope its output.
            try:
                plugin_cfg = await PluginConfig.load(
                    self._pool, "topic_source", source.source_name,
                )
            except Exception as exc:
                logger.warning(
                    "Niche %s: failed to load PluginConfig for %s (%s) — "
                    "using empty config",
                    niche.slug, source.source_name, exc,
                )
                plugin_cfg = None

            extract_config: dict[str, Any] = dict(
                plugin_cfg.config if plugin_cfg else {}
            )
            extract_config.update({
                "_site_config": self._site_config,
                "niche_slug": niche.slug,
                "niche_id": str(niche.id),
                # Niche-aware sourcing (§2b): web_search derives queries from
                # these when no explicit categories/seed_queries are configured.
                # Harmless for sources that ignore them — keeps the orchestrator
                # path consistent with the niche-bound tap handler.
                "niche_name": niche.name,
                "target_audience_tags": list(niche.target_audience_tags),
            })

            try:
                topics = await plugin.extract(self._pool, extract_config)
            except Exception as exc:
                # Per-source isolation — one bad source must not starve
                # the rest of the sweep.
                logger.exception(
                    "Niche %s: TopicSource %s extract failed: %s",
                    niche.slug, source.source_name, exc,
                )
                continue

            for t in topics or []:
                # Convert DiscoveredTopic → dict shape that
                # _embed_and_pre_rank reads. ``source_url`` doubles as
                # source_ref so the (batch_id, source_name, source_ref)
                # uniqueness constraint on topic_candidates holds even
                # when two sources surface the same headline.
                title = getattr(t, "title", "") or ""
                desc = getattr(t, "description", "") or ""
                src_url = getattr(t, "source_url", "") or ""
                candidates.append({
                    "kind": "external",
                    "data": {
                        "title": title,
                        "summary": desc,
                        "source_name": source.source_name,
                        "source_ref": src_url or title[:80],
                        "source_url": src_url,
                        "category": getattr(t, "category", "") or "",
                        "relevance_score": float(
                            getattr(t, "relevance_score", 0.0) or 0.0
                        ),
                    },
                })

        logger.info(
            "Niche %s: discovered %d external candidate(s) across %d source(s)",
            niche.slug, len(candidates), len(external_sources),
        )
        return candidates

    async def _discover_internal(self, niche: Niche) -> list[dict[str, Any]]:
        sources = await self._niche_svc.get_sources(niche.id)
        if not any(
            s.source_name == "internal_rag" and s.enabled for s in sources
        ):
            return []
        from services.internal_rag_source import InternalRagSource
        # Pass the DI-injected ``self._site_config`` down to the migrated
        # ``InternalRagSource`` (caller-bridge, #272 leaf batch 5 →
        # Phase-2d: topic_batch_service is now required-DI, no module global).
        rag = InternalRagSource(self._pool, site_config=self._site_config)
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
        per_kind_limit = self._site_config.get_int(
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
        decay = self._site_config.get_float(
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

    async def _dedupe_candidates(
        self,
        external: list,
        internal: list,
    ) -> tuple[list, list]:
        """Drop duplicate candidates before they reach the batch.

        Delegates to ``get_deduplicator()`` so the ``topic_dedup_engine``
        app_setting still selects word-overlap (default) vs semantic, and
        runs BOTH passes over the combined external+internal pool:

        - intra-batch: collapses the same topic surfaced twice this sweep
          (cross-source, or internal RAG distilling one theme from two
          source rows — identical ``distilled_topic``, distinct
          ``primary_ref`` — which survives the dict-keyed pre-rank as two
          ids). Fresh candidates are listed before carry-forwards, so a
          fresh/carried collision keeps the fresh copy.
        - vs-existing: drops candidates whose title already matches a
          published post or in-flight content_task.

        Fail-open: a deduplicator error must never sink the sweep — log and
        return the candidates un-deduped. A duplicate is a far smaller
        problem than the content stall an exception here would cause (same
        posture as the empty-batch-wedge guard in ``run_sweep``).
        """
        from services.topic_dedup_semantic import get_deduplicator

        wrappers: list[_DedupCandidate] = []
        for item in external:
            wrappers.append(
                _DedupCandidate(self._external_title(item), "external", item)
            )
        for item in internal:
            wrappers.append(
                _DedupCandidate(self._internal_title(item), "internal", item)
            )
        if not wrappers:
            return external, internal

        deduper = get_deduplicator(self._pool, site_config=self._site_config)
        try:
            await deduper.mark_duplicates(wrappers)
        except Exception:
            logger.warning(
                "Dedup pass failed — proceeding with un-deduped candidates",
                exc_info=True,
            )
            return external, internal

        fresh_external = [
            w.item for w in wrappers
            if w.pool == "external" and not w.is_duplicate
        ]
        fresh_internal = [
            w.item for w in wrappers
            if w.pool == "internal" and not w.is_duplicate
        ]
        dropped = len(wrappers) - (len(fresh_external) + len(fresh_internal))
        if dropped:
            logger.info(
                "Dedup dropped %d duplicate candidate(s) "
                "(external %d→%d, internal %d→%d)",
                dropped, len(external), len(fresh_external),
                len(internal), len(fresh_internal),
            )
        return fresh_external, fresh_internal

    @staticmethod
    def _external_title(item: Any) -> str:
        """Title of an external candidate across its shapes — fresh
        ``{"data": {...}}`` / carry-forward ``{"row": {...}}`` / legacy
        flat dict. Mirrors the shape handling in ``_embed_and_pre_rank``."""
        if isinstance(item, dict) and "row" in item:
            row = item["row"]
        elif isinstance(item, dict) and "data" in item:
            row = item["data"]
        else:
            row = item
        if isinstance(row, dict):
            return (row.get("title") or "").strip()
        return ""

    @staticmethod
    def _internal_title(item: Any) -> str:
        """Distilled topic of an internal candidate across its shapes —
        fresh ``InternalCandidate`` dataclass under ``data`` / carry-forward
        row dict under ``row``."""
        if isinstance(item, dict) and "data" in item:
            data = item["data"]
        elif isinstance(item, dict) and "row" in item:
            data = item["row"]
        else:
            data = item
        # InternalCandidate dataclass (fresh) exposes ``.distilled_topic``;
        # carry-forward row dicts carry it under the "distilled_topic" key
        # (falling back to "title"). 3-arg getattr keeps both shapes happy.
        topic = getattr(data, "distilled_topic", None)
        if topic is None and isinstance(data, dict):
            topic = data.get("distilled_topic") or data.get("title")
        return (topic or "").strip()

    def _drop_contentless_candidates(
        self,
        niche: Niche,
        external: list,
        internal: list,
    ) -> tuple[list, list]:
        """Drop candidates whose title fails the deterministic topic-sanity
        gate before they can occupy a batch slot.

        2026-06-30 incident: the dev.to tap surfaced a post titled
        ``". .. . ... . .... . .... . ... ."`` — the embedding pre-rank
        didn't sink it and the LLM final-scorer ranked it TOP of its batch
        (65 vs 40-48 for real headlines), so it auto-resolved into a full
        GPU run. Sanity must be calculated, not LLM-judged
        (``feedback_calculated_vs_generated``); this filter runs after
        dedup and before embedding so garbage is never embedded, ranked,
        or carried forward.

        Loud, not silent (``feedback_no_silent_defaults``): emits ONE
        aggregated ``topic_sanity_rejected`` finding per sweep with every
        dropped title, so a tap emitting separator junk is visible on the
        Findings board instead of quietly shrinking batches.
        """
        min_words = resolve_min_alpha_words(self._site_config)
        kept_external: list = []
        kept_internal: list = []
        dropped: list[tuple[str, str, str]] = []  # (pool, reason, title)

        for item in external:
            title = self._external_title(item)
            verdict = evaluate_topic_sanity(title, min_alpha_words=min_words)
            if verdict.ok:
                kept_external.append(item)
            else:
                dropped.append(("external", verdict.reason or "", title))
        for item in internal:
            title = self._internal_title(item)
            verdict = evaluate_topic_sanity(title, min_alpha_words=min_words)
            if verdict.ok:
                kept_internal.append(item)
            else:
                dropped.append(("internal", verdict.reason or "", title))

        if dropped:
            logger.warning(
                "Niche %s: dropped %d contentless candidate(s) at sweep "
                "intake: %s",
                niche.slug, len(dropped),
                "; ".join(f"[{p}/{r}] {t!r:.60}" for p, r, t in dropped),
            )
            emit_finding(
                source="topic_batch_service",
                kind="topic_sanity_rejected",
                title=(
                    f"Dropped {len(dropped)} contentless topic candidate(s) "
                    f"at sweep intake (niche {niche.slug})"
                ),
                body="\n".join(
                    f"- [{pool}] {reason}: {title!r}"
                    for pool, reason, title in dropped
                ),
                severity="warn",
                dedup_key=f"topic-sanity-intake:{niche.slug}",
                extra={
                    "stage": "sweep_intake",
                    "niche_slug": niche.slug,
                    "dropped": [
                        {"pool": pool, "reason": reason, "title": title[:200]}
                        for pool, reason, title in dropped
                    ],
                },
            )
        return kept_external, kept_internal

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
        # #272 Phase-2d: thread the DI-injected site_config into the
        # topic_ranking helpers (no module global there anymore).
        goal_vecs = {
            g.goal_type: await goal_vector_for(g.goal_type, site_config=self._site_config)
            for g in goals
        }

        async def score_one(text: str, decay: float) -> tuple[float, dict[str, float]]:
            # Ollama refuses to embed empty input — return a zero score so
            # the candidate sinks to the bottom of the rank rather than
            # crashing the entire sweep. Caller already populates a
            # placeholder title so the candidate row stays valid for the
            # operator to see + reject.
            if not text or not text.strip():
                return 0.0, {g.goal_type: 0.0 for g in goals}
            vec = await embed_text(text, site_config=self._site_config)
            raw, breakdown = weighted_cosine_score(vec, goal_vecs, goals)
            return apply_decay(score=raw, decay_factor=decay), breakdown

        ext_scored: list[ScoredCandidate] = []
        for item in external:
            # External candidates arrive in three shapes:
            #   - carry-forward: {"row": <db row dict>, "decay_factor": ...}
            #   - fresh plugin output: {"kind": "external", "data": <dict>}
            #   - legacy flat dict (defensive — tests may still emit this)
            if isinstance(item, dict) and "row" in item:
                row = item["row"]
            elif isinstance(item, dict) and "data" in item:
                row = item["data"]
            else:
                row = item
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
            def _field(name: str, default: str = "", _d: Any = data) -> str:
                if hasattr(_d, name):
                    return getattr(_d, name) or default
                if isinstance(_d, dict):
                    return _d.get(name) or default
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
        top_n = self._site_config.get_int("niche_top_n_per_pool", 5)
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
        expires_days = self._site_config.get_int("niche_batch_expires_days", 7)
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
            assert tbl in ("topic_candidates", "internal_topic_candidates")  # nosec B101  # safelist guard for the f-string below
            await conn.execute(
                f"UPDATE {tbl} "  # nosec B608  # tbl is safelisted via the assert above; values use $N params
                "SET operator_edited_topic = $1, operator_edited_angle = $2 "
                "WHERE id = $3",
                topic, angle, rid,
            )

    async def resolve_batch(self, *, batch_id: UUID) -> None:
        """Resolve the batch: hand the rank-1 candidate to the content
        pipeline, then mark the batch ``resolved`` + record provenance.

        Raises ``ValueError`` if no operator_rank=1 candidate exists, or if
        the batch references a niche that no longer exists.
        """
        view = await self.show_batch(batch_id=batch_id)
        winner = next(
            (c for c in view.candidates if c.operator_rank == 1), None,
        )
        if winner is None:
            raise ValueError("no operator_rank=1 candidate; rank first")
        niche = await self._niche_svc.get_by_id(view.niche_id)
        if niche is None:
            # Defensive: topic_batches.niche_id is ON DELETE CASCADE, so an
            # orphaned batch shouldn't occur in practice — but if the niche
            # row is somehow gone, _handoff_to_pipeline would AttributeError
            # on ``niche.slug`` mid-write. Fail loud with context per
            # feedback_no_silent_defaults instead of a cryptic NoneType crash.
            raise ValueError(
                f"resolve_batch: batch {batch_id} references unknown niche "
                f"{view.niche_id} — cannot hand off to pipeline"
            )
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

    async def list_open_batches(self) -> list[OpenBatch]:
        """Return every ``open`` batch across all niches, newest first.

        Each entry is a merged, effective-score-sorted candidate view (via
        :meth:`show_batch`) plus the resolved niche slug/name for display.
        Resolved/expired batches are excluded — the operator only drains
        what's still pending a decision.

        Powers the console topic-triage surface (``GET
        /api/topics/proposals``) and the ``topics_*`` MCP/CLI flow. A stuck
        *open* batch that never gets resolved is the recurring "content goes
        dark" failure class, so surfacing open batches where the operator can
        act on them is the whole point.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM topic_batches WHERE status = 'open' "
                "ORDER BY created_at DESC",
            )
        out: list[OpenBatch] = []
        for r in rows:
            view = await self.show_batch(batch_id=r["id"])
            niche = await self._niche_svc.get_by_id(view.niche_id)
            out.append(
                OpenBatch(
                    view=view,
                    niche_slug=niche.slug if niche else None,
                    niche_name=niche.name if niche else None,
                )
            )
        return out

    async def get_open_batch_id(self, niche_id: UUID) -> UUID | None:
        """Return the ``id`` of the current open batch for ``niche_id``, or ``None``.

        Used by the MCP ``topics_show_batch`` tool to avoid an inline SQL
        call in the adapter layer (transport-adapter contract #1344).
        """
        async with self._pool.acquire() as conn:
            bid = await conn.fetchval(
                "SELECT id FROM topic_batches WHERE niche_id = $1 AND status = 'open'",
                niche_id,
            )
        return bid

    async def _handoff_to_pipeline(
        self,
        winner: CandidateView,
        niche: Niche,
        batch_id: UUID,
    ) -> None:
        """Advance the winning candidate into the existing content
        pipeline by inserting directly into ``pipeline_tasks`` (the
        underlying table — see #188).

        Uses the operator-edited topic/angle when present, otherwise
        falls back to the candidate's title/summary.

        ``topic_batch_id`` carries the BATCH id (provenance pointer back
        to the batch the topic came from), NOT the candidate id — the
        plan body originally threaded ``winner.id`` here, which would
        have made provenance point at the candidate row that owns the
        topic and broken the FK to ``topic_batches``.

        The angle/summary lives in ``pipeline_versions.stage_data``
        under ``metadata.angle`` so the writer dispatcher can read it
        back via the ``content_tasks`` view's ``metadata`` projection.
        """
        from uuid import uuid4

        topic = winner.operator_edited_topic or winner.title
        angle = winner.operator_edited_angle or winner.summary or ""

        # Deterministic topic-sanity gate — the LAST seam before a batch
        # winner becomes a pipeline_tasks row (2026-06-30 incident: a
        # dots-only headline reached here and burned a full GPU run).
        # Judges the topic that actually ships (operator edit wins), and
        # raises BEFORE any DB write. topic_auto_resolve catches the typed
        # error and expires the batch; the operator resolve paths surface
        # it as a 400 / CLI error (edit the winner, then re-resolve).
        verdict = evaluate_topic_sanity(
            topic, min_alpha_words=resolve_min_alpha_words(self._site_config),
        )
        if not verdict.ok:
            emit_finding(
                source="topic_batch_service",
                kind="topic_sanity_rejected",
                title=(
                    f"Blocked contentless topic at batch handoff "
                    f"({verdict.reason})"
                ),
                body=(
                    f"Batch {batch_id} (niche {niche.slug}) winner candidate "
                    f"{winner.id} ({winner.kind}) failed the topic-sanity "
                    f"gate: {verdict.detail}\n\nTopic: {topic!r}"
                ),
                severity="warn",
                dedup_key=f"topic-sanity-handoff:{batch_id}",
                extra={
                    "stage": "batch_handoff",
                    "batch_id": str(batch_id),
                    "niche_slug": niche.slug,
                    "candidate_id": str(winner.id),
                    "candidate_kind": winner.kind,
                    "reason": verdict.reason,
                    "alpha_word_count": verdict.alpha_word_count,
                    "topic": (topic or "")[:200],
                },
            )
            raise TopicSanityError(topic, verdict)

        task_id = str(uuid4())

        # task_id and task_type are NOT NULL on pipeline_tasks. The
        # legacy code path supplied task_type='blog_post'; preserve that.
        # `content_type` is a view-only computed column (= task_type),
        # so we don't write it directly — readers get it back via the
        # content_tasks view.
        stage_data = {
            "metadata": {
                "angle": angle,
                "summary": winner.summary,
                "source": "topic_batch",
                # discovered_by mirrors source — task_executor's off-brand
                # gate exempts a tuple of discovered_by values (url_seed,
                # url_list, operator_telegram, operator_cli, topic_batch).
                # Without this field set, an operator-resolved batch would
                # hit the keyword whitelist and get rejected even though
                # the operator vetted the topic via rank-batch + resolve-
                # batch. See Glad-Labs/poindexter#351.
                "discovered_by": "topic_batch",
                "niche_slug": niche.slug,
            }
        }
        # Resolve template_slug per the shared policy: niche
        # default → app_settings default → fail loud. Per-niche
        # default is preferred (the structured DB seam) per
        # feedback_filter_on_seams_not_slugs. The legacy bug was
        # that this INSERT omitted template_slug entirely, leaving
        # it NULL → content_router_service fails the task per
        # feedback_no_silent_defaults (jank-audit finding #3).
        from services.template_slug_resolver import resolve_template_slug
        template_slug = await resolve_template_slug(
            self._pool, niche_slug=niche.slug,
        )
        # Vary post length via the shared weighted picker (#542) instead of
        # letting the row fall to the ``target_length`` column default
        # (1500). The auto-queue used to omit this column entirely, so every
        # niche post landed on 1500 and the DB-configurable
        # ``topic_discovery_length_distribution`` had no effect — the
        # length-uniformity bug. An explicit picked value flows on to the
        # writer via ``flows/content_generation`` → ``two_pass_writer``.
        target_length = pick_target_length(self._site_config)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO pipeline_tasks
                      (task_id, task_type, topic, status, stage,
                       niche_slug, topic_batch_id, template_slug,
                       target_length)
                    VALUES ($1, 'blog_post', $2, 'pending', 'pending',
                            $3, $4, $5, $6)
                    """,
                    task_id, topic, niche.slug,
                    batch_id, template_slug, target_length,
                )
                await conn.execute(
                    """
                    INSERT INTO pipeline_versions
                      (task_id, version, title, stage_data, created_at)
                    VALUES ($1, 1, $2, $3::jsonb, NOW())
                    ON CONFLICT (task_id, version) DO NOTHING
                    """,
                    task_id, topic, json.dumps(stage_data, default=str),
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
