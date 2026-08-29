"""Generate topic candidates from the internal embedded corpus.

The writing pivot — instead of summarising external content, mine our
own claude_sessions / brain_knowledge / audit / decision_log / git
history / memory / posts for storyworthy events and turn each into a
proposed topic + angle. The operator's first-party knowledge is the
content moat; external sources are a popularity signal, not source
material (poindexter#822).

Storyworthy selection (poindexter#820): snippets are ranked by pgvector
similarity to the niche's weighted goal vectors within a recency window,
instead of plain newest-N — the newest ops rows are, by volume, routine
housekeeping ("clean cycle completed") that distilled into rejected
topics 1,441:4 over 30 days. Per-kind weights bias sampling toward
story-dense kinds (decision_log / memory) over status-dense ones
(audit / brain), and the distiller may answer ``{"storyworthy": false}``
to kill a non-story before it costs a batch slot. Every step fails open
to the legacy recency behaviour — a ranking outage must not sink a
discovery sweep (same posture as the dedup pass).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from services.logger_config import get_logger
from services.site_config import SiteConfig

# 2026-05-29 — SiteConfig DI migration (#272 leaf batch 5) converted this
# module from the module-level ``site_config`` singleton + ``set_site_config``
# setter to constructor DI via ``InternalRagSource`` taking ``site_config``
# in ``__init__`` (stored as ``self._site_config``). The
# ``niche_internal_rag_per_kind_limit`` / ``niche_internal_rag_snippet_max_chars``
# reads + the ``resolve_writer_model`` writer-model lookup now go through
# ``self._site_config``. The runtime ``pool`` is supplied by the caller, so
# there is no container build-time cached_property; ``topic_batch_service``
# constructs ``InternalRagSource(pool, site_config=...)`` from its own
# lifespan-bound SiteConfig (caller-bridge pattern).

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
    def __init__(self, pool, *, site_config: SiteConfig):
        self._pool = pool
        self._site_config = site_config

    async def extract(
        self,
        _pool: Any,
        config: dict[str, Any],
    ) -> list[Any]:
        """TopicSource.extract() shim for tap_builtin_topic_source.

        Adapts generate() → list[DiscoveredTopic] so this class can be
        treated polymorphically by the tap handler alongside real plugin
        objects.  The handler seeds niche_id into config from the tap row;
        source_kinds defaults to all implemented kinds (everything except
        git_commit, which is not yet plumbed).
        """
        from plugins.topic_source import DiscoveredTopic

        niche_id = config.get("niche_id")
        if not niche_id:
            raise ValueError(
                "InternalRagSource.extract: config must include niche_id "
                "(seeded by tap_builtin_topic_source from the tap row)"
            )
        source_kinds: list[str] = list(
            config.get("source_kinds")
            or [k for k in VALID_SOURCE_KINDS if k != "git_commit"]
        )
        per_kind_limit = self._site_config.get_int(
            "niche_internal_rag_per_kind_limit", 4
        )
        candidates = await self.generate(
            niche_id=niche_id,
            source_kinds=source_kinds,
            per_kind_limit=per_kind_limit,
        )
        return [
            DiscoveredTopic(
                title=c.distilled_topic,
                category=c.source_kind,
                source="internal_rag",
                description=c.distilled_angle,
            )
            for c in candidates
        ]

    async def generate(
        self,
        *,
        niche_id: UUID | str,
        source_kinds: list[str],
        per_kind_limit: int | None = None,
    ) -> list[InternalCandidate]:
        # ``per_kind_limit`` defaults to the operator-tunable
        # ``niche_internal_rag_per_kind_limit`` app_setting (migration
        # 0119). The prior hardcoded default was 5; falls back to that
        # when site_config isn't loaded so unit-test fixtures still work.
        if per_kind_limit is None:
            per_kind_limit = self._site_config.get_int(
                "niche_internal_rag_per_kind_limit", 5,
            )
        bad = [s for s in source_kinds if s not in VALID_SOURCE_KINDS]
        if bad:
            raise ValueError(f"unknown source_kinds: {bad}")

        # Storyworthy selection context (poindexter#820). Fail-open: when
        # the niche/goal-vector resolution is unavailable (missing niche,
        # embed outage, unit-test pool stubs) both come back None and
        # selection degrades to the legacy newest-N behaviour.
        query_vec, niche_context = await self._resolve_selection_context(niche_id)
        kind_weights = self._resolve_kind_weights()
        lookback_days = self._site_config.get_int(
            "niche_internal_rag_lookback_days", 30,
        )

        results: list[InternalCandidate] = []
        for kind in source_kinds:
            # Bias sampling toward story-dense kinds: effective limit =
            # per_kind_limit x weight (half-up rounding). Weight 0 skips
            # the kind entirely; an unlisted kind weighs 1.0.
            eff_limit = int(per_kind_limit * kind_weights.get(kind, 1.0) + 0.5)
            if eff_limit <= 0:
                continue
            snippets = await self._fetch_recent_snippets(
                kind, eff_limit,
                query_vec=query_vec, lookback_days=lookback_days,
            )
            for primary_ref, snippet, supporting in snippets:
                distilled = await self._distill_topic_angle(
                    [snippet] + [s["snippet"] for s in supporting],
                    niche_context=niche_context,
                )
                # Per-candidate resilience: a single empty / unparseable LLM
                # response must not sink the whole discovery sweep (it did —
                # 2026-05-28 content-gen stall, where one empty json.loads
                # bubbled out of run_sweep and discarded every external
                # candidate too). Skip the bad candidate, keep the rest.
                if distilled is None:
                    continue
                topic, angle = distilled
                results.append(InternalCandidate(
                    source_kind=kind,
                    primary_ref=primary_ref,
                    distilled_topic=topic,
                    distilled_angle=angle,
                    supporting_refs=supporting,
                    raw_snippet=snippet,
                ))
        return results

    async def _resolve_selection_context(
        self, niche_id: UUID | str,
    ) -> tuple[list[float] | None, str | None]:
        """Resolve (query_vec, niche_context) for storyworthy selection.

        ``query_vec`` is the niche's goal vectors combined by ``weight_pct``
        — the same vectors the downstream embed pre-rank scores candidates
        against, so snippet selection optimises for what actually wins a
        batch. ``niche_context`` is a short human-readable audience line for
        the distillation prompt.

        Fail-open by design: any resolution failure (unknown niche, embed
        outage, stub pools in tests) returns ``(None, None)`` and selection
        falls back to the legacy newest-N path. A ranking outage must never
        sink a discovery sweep.
        """
        try:
            from services.niche_service import NicheService
            from services.topic_ranking import goal_vector_for

            svc = NicheService(self._pool)
            niche = await svc.get_by_id(
                niche_id if isinstance(niche_id, UUID) else UUID(str(niche_id))
            )
            if niche is None:
                return None, None
            audience = ", ".join(niche.target_audience_tags or []) or "general"
            niche_context = f"{niche.name} (audience: {audience})"

            goals = await svc.get_goals(niche.id)
            combined: list[float] | None = None
            total_weight = 0.0
            for g in goals:
                weight = float(g.weight_pct or 0)
                if weight <= 0:
                    continue
                gv = await goal_vector_for(
                    g.goal_type, site_config=self._site_config,
                )
                if combined is None:
                    combined = [weight * x for x in gv]
                else:
                    combined = [c + weight * x for c, x in zip(combined, gv, strict=True)]
                total_weight += weight
            if combined is None or total_weight <= 0:
                return None, niche_context
            return [c / total_weight for c in combined], niche_context
        except Exception:
            logger.warning(
                "[internal_rag] selection-context resolution failed — "
                "falling back to recency selection",
                exc_info=True,
            )
            return None, None

    def _resolve_kind_weights(self) -> dict[str, float]:
        """Parse ``niche_internal_rag_kind_weights`` (JSON object) off site_config.

        Maps source_kind → sampling weight; a kind absent from the map
        weighs 1.0, weight 0 skips the kind. Malformed values fall back to
        the empty map (all kinds 1.0) with a warning rather than sinking
        the sweep.
        """
        raw = self._site_config.get("niche_internal_rag_kind_weights", "") or ""
        if not str(raw).strip():
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
            return {str(k): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            logger.warning(
                "[internal_rag] niche_internal_rag_kind_weights=%r is not a "
                "JSON object of kind→number; using flat weights", raw,
            )
            return {}

    async def _fetch_recent_snippets(
        self,
        source_kind: str,
        limit: int,
        *,
        query_vec: list[float] | None = None,
        lookback_days: int | None = None,
    ) -> list[tuple[str, str, list[dict[str, Any]]]]:
        """Pull the top-N entries for this kind from the embeddings table.

        With ``query_vec`` set, rows within the ``lookback_days`` window are
        ranked by pgvector cosine distance to the niche goal vector — the
        storyworthy-selection path (poindexter#820). Without it (or on any
        vector-query failure) the legacy newest-N ordering applies.

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
            rows = None
            if query_vec is not None:
                try:
                    # pgvector has no asyncpg codec — pass the vector in its
                    # text form (pattern: services/embeddings_db.py).
                    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
                    rows = await conn.fetch(
                        """
                        SELECT source_id, COALESCE(chunk_text, text_preview) AS snippet
                          FROM embeddings
                         WHERE source_table = $1
                           AND created_at > NOW() - make_interval(days => $2)
                         ORDER BY embedding <=> $3::vector
                         LIMIT $4
                        """,
                        st, int(lookback_days or 30), vec_str, limit,
                    )
                except Exception:
                    # Dimension mismatch / missing extension / stub pool —
                    # degrade to recency rather than sinking the sweep.
                    logger.warning(
                        "[internal_rag] vector-ranked snippet query failed "
                        "for %s — falling back to recency", st,
                        exc_info=True,
                    )
                    rows = None
            # Fall back when the vector path failed OR its lookback window
            # was empty — the ranked path must never return *less* than the
            # legacy recency path would have.
            if not rows:
                rows = await conn.fetch(
                    """
                    SELECT source_id, COALESCE(chunk_text, text_preview) AS snippet
                      FROM embeddings
                     WHERE source_table = $1
                     ORDER BY created_at DESC
                     LIMIT $2
                    """,
                    st, limit,
                )
        # Snippets are fed to _distill_topic_angle (an LLM), so they take the
        # full chunk rather than the 500-char display preview
        # (poindexter#1033). COALESCE covers rows not yet backfilled.
        return [(str(r["source_id"]), r["snippet"] or "", []) for r in rows]

    async def _distill_topic_angle(
        self,
        snippets: list[str],
        *,
        niche_context: str | None = None,
    ) -> tuple[str, str] | None:
        """Run a small LLM call to extract a proposed (topic, angle) from raw snippets.

        Returns ``None`` when the model returns an empty or unparseable
        response so the caller can skip this candidate instead of crashing
        the whole sweep (2026-05-28 content-gen stall).

        ``niche_context`` is formatted into the prompt (templates without a
        ``{niche_context}`` placeholder simply ignore it), and the prompt
        invites a ``{"storyworthy": false}`` verdict for routine ops status
        — treated as a skip, so a non-story dies here instead of after a
        full generation run (poindexter#820).

        Snippet truncation length is operator-tunable via
        ``niche_internal_rag_snippet_max_chars``. The model resolves via
        ``resolve_structured_model`` (DB-configurable
        ``structured_extraction_model``, default ``gemma3:27b``) — a
        JSON-reliable instruct model — NOT the writer model, because a
        reasoning writer model (``glm-4.7-5090``) returns empty ``content``
        under ``response_format=json_object``.
        """
        from services.topic_ranking import _ollama_chat_json

        snippet_max = self._site_config.get_int(
            "niche_internal_rag_snippet_max_chars", 600,
        )
        from services.llm_text import resolve_structured_model
        model = resolve_structured_model(site_config=self._site_config)
        joined = "\n---\n".join(s[:snippet_max] for s in snippets if s)
        from services.prompt_manager import get_prompt_manager
        prompt = get_prompt_manager().get_prompt(
            "research.distill_topic_angle",
            joined=joined,
            niche_context=niche_context or "a general technology audience",
        )
        # #272 Phase-2b: topic_ranking._ollama_chat_json no longer carries a
        # lifespan-bound module global — pass our injected SiteConfig.
        raw = await _ollama_chat_json(
            prompt, model=model, site_config=self._site_config,
        )
        if not raw or not raw.strip():
            logger.warning(
                "[internal_rag] distill returned empty response (model=%s) — "
                "skipping candidate", model,
            )
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                "[internal_rag] distill response not valid JSON (model=%s): "
                "%s — skipping candidate", model, e,
            )
            return None
        # Distiller judged the snippets a non-story (routine ops status,
        # housekeeping chatter). Skip quietly — this verdict is the point
        # of the storyworthiness prompt, not a failure. Only prompts that
        # ask for the verdict ever emit it, so old templates are unaffected.
        if isinstance(parsed, dict) and parsed.get("storyworthy") is False:
            logger.info(
                "[internal_rag] distiller judged snippets not storyworthy "
                "(reason=%r) — skipping candidate",
                str(parsed.get("reason") or "")[:120],
            )
            return None
        # The LLM occasionally returns `{"topic": ""}` (or omits the key).
        # An empty topic means the model failed to distill — skip the
        # candidate like the empty/unparseable cases above. Inventing a
        # placeholder here ("Untitled") let junk flow all the way to a
        # generated post (poindexter#808).
        topic = str(parsed.get("topic") or "").strip()
        if not topic:
            logger.warning(
                "[internal_rag] distill returned no topic (model=%s) — "
                "skipping candidate", model,
            )
            return None
        angle = str(parsed.get("angle") or "").strip()
        # The distiller occasionally leaks its own task narration into the
        # topic/angle instead of producing content (poindexter row
        # 5b662b41-66c0-403f-945a-b750e922340f: "I need to extract the
        # proposed blog post topic and unique angle from the provided
        # snippets. 1. Topic: ..."). strip_reasoning_artifacts alone can't
        # fix this — the surrounding prose IS the leak.
        from services.topic_sanity import detect_leaked_reasoning
        for field_name, field_value in (("topic", topic), ("angle", angle)):
            leak_reason = detect_leaked_reasoning(field_value)
            if leak_reason:
                logger.warning(
                    "[internal_rag] distill %s looks like leaked reasoning "
                    "(reason=%s, model=%s): %r — skipping candidate",
                    field_name, leak_reason, model, field_value[:120],
                )
                return None
        return topic, angle
