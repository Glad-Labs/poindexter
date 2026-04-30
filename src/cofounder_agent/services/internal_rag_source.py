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
