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
from services.site_config import SiteConfig

# 2026-05-29 — SiteConfig DI migration (#272 leaf batch 5) converted this
# module from the module-level ``site_config`` singleton + ``set_site_config``
# setter to constructor DI via ``InternalRagSource`` taking ``site_config``
# in ``__init__`` (stored as ``self._site_config``). The
# ``niche_internal_rag_per_kind_limit`` / ``niche_internal_rag_snippet_max_chars``
# reads + the ``resolve_local_model`` writer-model lookup now go through
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

        results: list[InternalCandidate] = []
        for kind in source_kinds:
            snippets = await self._fetch_recent_snippets(kind, per_kind_limit)
            for primary_ref, snippet, supporting in snippets:
                distilled = await self._distill_topic_angle(
                    [snippet] + [s["snippet"] for s in supporting],
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

    async def _distill_topic_angle(
        self, snippets: list[str]
    ) -> tuple[str, str] | None:
        """Run a small LLM call to extract a proposed (topic, angle) from raw snippets.

        Returns ``None`` when the model returns an empty or unparseable
        response so the caller can skip this candidate instead of crashing
        the whole sweep (2026-05-28 content-gen stall).

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
        )
        # #272 Phase-2b: topic_ranking._ollama_chat_json no longer carries a
        # lifespan-bound module global — pass our injected SiteConfig.
        raw = await _ollama_chat_json(
            prompt, model=model, site_config=self._site_config,
        )
        import json
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
        # `dict.get(k, default)` returns the actual None/empty when the key
        # exists with that value — the default never fires. The LLM
        # occasionally returns `{"topic": ""}`, so fall back to truthy-or
        # to make sure topic is never an empty string downstream (which
        # otherwise crashes embed_text on empty input).
        return parsed.get("topic") or "Untitled", parsed.get("angle") or ""
