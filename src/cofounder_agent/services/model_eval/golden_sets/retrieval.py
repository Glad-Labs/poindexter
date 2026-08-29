"""Retrieval golden-set bootstrap — end-to-end recall over the real corpus.

Distinct from the reranker set next door, and the difference is the point.
``golden_sets/reranker.py`` hands a model 20 candidates with one known-relevant
and asks "can you rank it first?". This set asks the harder, upstream question:
**given the whole 79k-row production corpus, does retrieval surface the right
chunk at all?** A perfect reranker cannot rescue a chunk the retriever never
returned, so the two measure genuinely different failures.

Why questions are generated from a *span*, and why the span's position matters
-----------------------------------------------------------------------------
poindexter#1033 found that retrieval matched on ~4.3k median characters and
returned the first 500 — and that in 40% of sampled queries the best-matching
window was not the first one. The obvious eval (post title -> its own chunk)
would have been blind to exactly that: titles describe a document's opening, so
every case would probe the head of the text and the bug would score clean.

So each case picks a concrete span from the chunk, records **where** it sits,
and asks a local model to write a question that span alone answers. Cases are
then stratified into two regions:

- ``head`` — the span starts inside the first ``legacy_preview_chars`` (500)
- ``deep`` — the span starts past it, i.e. content the pre-#1033 payload
  discarded entirely

Reporting recall separately per region is the money metric. If ``deep`` recall
now matches ``head`` recall, the payload + BM25 repairs did their job; a gap
means something on the path is still reading only the head.

No dummy data: spans are real text from real indexed chunks. The set is
versioned by a stable hash of the sampled row keys, so the same corpus yields
the same version and re-embedding rolls it forward.
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from services.logger_config import get_logger
from services.model_eval.types import GoldenCase, GoldenSet

logger = get_logger(__name__)

_GOLDEN_NAME = "model_eval_retrieval"

# Non-reasoning, instruction-following, small enough not to fight the render
# queue for VRAM. Overridable via ``retrieval_eval_question_model``.
_DEFAULT_QUESTION_MODEL = "qwen2.5:7b"

# The pre-#1033 payload ceiling. A span starting at or past this offset was
# invisible to every consumer before that fix — that is what makes the
# head/deep split meaningful rather than arbitrary.
LEGACY_PREVIEW_CHARS = 500

_QUESTION_SYSTEM = (
    "You write retrieval test questions. Given a passage, you write ONE "
    "specific question that the passage answers. Reply with the question "
    "only — no preamble, no quotes, no explanation."
)

_QUESTION_PROMPT = """\
Write one specific question that is answered by this passage.

Requirements:
- The question must be answerable from this passage alone.
- Name the concrete subjects the passage discusses, so the question is
  findable by someone searching a large archive.
- Do not use the words "passage", "text", "excerpt", "above" or "document".
- One sentence. Question only.

Passage:
\"\"\"
{span}
\"\"\"
"""

# Two kinds of bad question, both dropped rather than repaired — a smaller
# honest set beats a larger noisy one.
#
# 1. META: the prompt explicitly forbids "passage"/"text"/"excerpt"/"document"/
#    "above". Any occurrence means the model disobeyed, and the question is
#    then unanswerable out of context ("What does the passage suggest about
#    leverage?") — retrieval would be blamed for a question that names no
#    subject. Observed live at ~3/10 before this filter, so it is not
#    hypothetical.
# 2. DEICTIC: a bare demonstrative pointing at unstated context.
_META = re.compile(
    r"\b(passage|excerpt|the\s+text|the\s+document|the\s+article|the\s+above)\b",
    re.IGNORECASE,
)
_DEICTIC = re.compile(
    r"\b(this|these|those|aforementioned|said)\s+"
    r"(system|approach|method|process|change|fix|issue)\b",
    re.IGNORECASE,
)

# Weak specificity floor: a question sharing almost no vocabulary with its span
# ("What improvements are being made to the system?") is ambiguous across the
# corpus, and a miss on it measures the question, not the retriever.
#
# CAVEAT, stated because it bounds what this eval can claim: requiring lexical
# overlap tilts the set slightly toward lexical (BM25) retrieval. The floor is
# deliberately low (2 shared content words) so it removes only questions about
# nothing in particular. Per-case ``question_span_overlap`` is recorded so the
# tilt is measurable rather than assumed.
_MIN_SPAN_OVERLAP_WORDS = 2
_WORD_RE = re.compile(r"[a-z0-9]{4,}")


def _pick_span(chunk: str, span_chars: int, rng: random.Random) -> tuple[int, str] | None:
    """Choose a paragraph-ish span and return ``(start_offset, span_text)``.

    Prefers a paragraph boundary so the span reads as coherent prose; falls
    back to a character window. Returns ``None`` when the chunk is too short
    to yield a span of the requested size.
    """
    if len(chunk) < span_chars:
        return None

    # Candidate starts at paragraph boundaries that leave room for a full span.
    bounds = [m.end() for m in re.finditer(r"\n\s*\n", chunk)]
    bounds = [b for b in bounds if b + span_chars <= len(chunk)]
    if bounds:
        start = rng.choice(bounds)
    else:
        start = rng.randint(0, len(chunk) - span_chars)
    return start, chunk[start : start + span_chars]


async def _fetch_candidate_chunks(
    pool: Any, *, min_chars: int, per_source: int
) -> list[dict[str, Any]]:
    """Sample indexed chunks that carry a real payload, stratified by source.

    Only rows with ``chunk_text`` are eligible: a NULL row still falls back to
    the 500-char preview, so generating a "deep" question from one would be
    asking retrieval for text the corpus genuinely does not hold. That is a
    backfill gap, not a retrieval failure, and conflating them would make this
    eval unreadable.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, chunk_index, chunk_text
              FROM (
                SELECT source_table, source_id, chunk_index, chunk_text,
                       row_number() OVER (
                         PARTITION BY source_table ORDER BY md5(source_id)
                       ) AS rn
                  FROM embeddings
                 WHERE chunk_text IS NOT NULL
                   AND length(chunk_text) >= $1
                   AND is_summary = FALSE
              ) s
             WHERE rn <= $2
             ORDER BY source_table, source_id, chunk_index
            """,
            min_chars,
            per_source,
        )
    return [dict(r) for r in rows]


def _version_for(rows: list[dict[str, Any]]) -> int:
    """Stable version from the sampled row keys — same corpus, same version."""
    key = "|".join(
        f"{r['source_table']}:{r['source_id']}:{r['chunk_index']}" for r in rows
    )
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


async def build_retrieval_golden_set(
    *,
    pool: Any,
    site_config: Any,
    question_fn: Any = None,
) -> GoldenSet:
    """Mine (question -> gold chunk) cases from the live corpus.

    ``question_fn`` is an injectable ``async (span: str) -> str`` so unit tests
    stay offline. Production passes ``None`` and gets the local-LLM path.

    Fails loud on an empty sample rather than returning a zero-case set: an
    eval that scored nothing must never report a clean sheet.
    """
    size = site_config.get_int("retrieval_eval_golden_size", 120)
    span_chars = site_config.get_int("retrieval_eval_span_chars", 400)
    min_chars = site_config.get_int("retrieval_eval_min_chunk_chars", 1200)
    per_source = site_config.get_int("retrieval_eval_per_source_cap", 60)
    seed = site_config.get_int("retrieval_eval_seed", 1033)

    rows = await _fetch_candidate_chunks(pool, min_chars=min_chars, per_source=per_source)
    if not rows:
        raise RuntimeError(
            "retrieval golden set: no chunks with chunk_text >= "
            f"{min_chars} chars. Run scripts/backfill_embeddings_chunk_text.py "
            "(host AND container passes) before evaluating — scoring an "
            "unbackfilled corpus measures the backfill, not retrieval."
        )

    rng = random.Random(seed)
    rng.shuffle(rows)

    if question_fn is None:
        question_fn = _make_llm_question_fn(site_config=site_config, pool=pool)

    cases: list[GoldenCase] = []
    used: list[dict[str, Any]] = []
    skipped_short = skipped_deictic = skipped_empty = skipped_vague = 0

    for row in rows:
        if len(cases) >= size:
            break
        chunk = row["chunk_text"] or ""
        picked = _pick_span(chunk, span_chars, rng)
        if picked is None:
            skipped_short += 1
            continue
        start, span = picked

        try:
            question = (await question_fn(span) or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("[retrieval_eval] question generation failed: %s", e)
            skipped_empty += 1
            continue

        question = question.strip().strip('"').split("\n")[0].strip()
        if not question or len(question) < 15:
            skipped_empty += 1
            continue
        if _META.search(question) or _DEICTIC.search(question):
            skipped_deictic += 1
            continue

        overlap = _WORD_RE.findall(question.lower())
        span_words = set(_WORD_RE.findall(span.lower()))
        shared = sorted({w for w in overlap if w in span_words})
        if len(shared) < _MIN_SPAN_OVERLAP_WORDS:
            skipped_vague += 1
            continue

        cases.append(
            GoldenCase(
                query=question,
                candidates=[],
                payload={
                    "source_table": row["source_table"],
                    "source_id": row["source_id"],
                    "chunk_index": row["chunk_index"],
                    "span_start": start,
                    "span_text": span,
                    "chunk_chars": len(chunk),
                    # The whole point of the split — see module docstring.
                    "region": "head" if start < LEGACY_PREVIEW_CHARS else "deep",
                    # Diagnostic, not a filter input beyond the floor above —
                    # lets a reader judge how lexically easy the set is.
                    "question_span_overlap": len(shared),
                },
            )
        )
        used.append(row)

    if not cases:
        raise RuntimeError(
            "retrieval golden set: every candidate was skipped "
            f"(short={skipped_short}, meta/deictic={skipped_deictic}, "
            f"vague={skipped_vague}, empty={skipped_empty}). "
            "Refusing to return an empty set."
        )

    n_deep = sum(1 for c in cases if c.payload["region"] == "deep")
    logger.info(
        "[retrieval_eval] golden set built: %d cases (%d deep / %d head), "
        "skipped short=%d meta/deictic=%d vague=%d empty=%d",
        len(cases), n_deep, len(cases) - n_deep,
        skipped_short, skipped_deictic, skipped_vague, skipped_empty,
    )
    return GoldenSet(name=_GOLDEN_NAME, version=_version_for(used), cases=cases)


def _make_llm_question_fn(*, site_config: Any, pool: Any) -> Any:
    """Build the question generator — pinned local model, never the dispatcher.

    ``pool`` is deliberately NOT forwarded to ``ollama_chat_text``. Passing a
    pool routes through ``dispatch_complete``, which honours
    ``plugin.llm_provider.primary.<tier>`` — on this install that is LiteLLM,
    and the ``budget`` tier resolves to an Anthropic model. Building a
    120-case golden set would then spend real API money to write test
    questions, against the local-first LLM policy. With ``pool=None``
    ``ollama_chat_text`` takes its direct-httpx path to local Ollama, so a
    golden-set build is free and runs offline.

    ``pool`` stays in the signature because the caller has one and a future
    local-provider dispatch path would want it; it is unused today by design.
    """
    from services.llm_text import ollama_chat_text

    del pool  # see docstring — routing through the dispatcher would bill an API
    model = (
        site_config.get("retrieval_eval_question_model", "") or ""
    ).strip() or _DEFAULT_QUESTION_MODEL

    async def _fn(span: str) -> str:
        return await ollama_chat_text(
            _QUESTION_PROMPT.format(span=span),
            model=model,
            system=_QUESTION_SYSTEM,
            site_config=site_config,
            pool=None,
            phase="retrieval_eval_question",
            think=False,
            max_tokens=site_config.get_int("retrieval_eval_question_max_tokens", 80),
        )

    return _fn


__all__ = [
    "LEGACY_PREVIEW_CHARS",
    "build_retrieval_golden_set",
]
