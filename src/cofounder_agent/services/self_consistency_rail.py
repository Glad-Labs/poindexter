"""Self-consistency rail for multi_model_qa (#196).

Implements the HalluCounter-style technique: sample the writer model
multiple times for a short summary of the generated content, embed
each sample, compute pairwise cosine similarity. High agreement →
the model is confident in its claims; low agreement → the model is
inconsistent across regenerations, which correlates with hallucinated
or unstable claims.

Cheap signal:

- N defaults to 3 samples (configurable via
  ``self_consistency_sample_count``).
- Each sample is a short summary (~200 tokens), not a full
  regeneration of the article. Keeps Ollama cycles bounded.
- Embeddings are reused across the parallel rails (same Ollama
  ``nomic-embed-text``), so this rail's marginal cost is N
  short LLM calls plus N embedding calls.

Activation
----------

``app_settings.self_consistency_enabled = true`` exposes the rail
inside ``MultiModelQA.review()`` (#196 Phase 2 wiring). Default off —
the rail's value depends on whether the writer model is consistent
enough to make the score meaningful at our scale.

Output contract
---------------

``evaluate(content, topic, site_config)`` returns
``(passed, score, reason)``:

- ``passed``: True iff mean pairwise similarity >= ``self_
  consistency_threshold`` (default 0.55).
- ``score``: mean pairwise cosine similarity, range [-1, 1]
  (effectively [0, 1] for sentence-transformers normalized vectors),
  or ``None`` when the rail measured NOTHING — see below.
- ``reason``: human-readable explanation that lands in the audit log.

Never raises — Ollama errors / embedding errors are caught and
surfaced as ``(True, None, 'self-consistency-skipped: ...')`` so the
rail can't take down the QA pass.

``score=None`` means NOT MEASURED — which is not the same as a score of
1.0 (poindexter#875). Every skip path used to return ``(True, 1.0, ...)``,
so a rail that never ran was recorded as a PERFECT PASS: prod audit rows
carried ``score: 100.0`` right next to ``"self-consistency-skipped:
embedding step failed"``. That inflated the QA Rails pass-rate, hid a real
embedding outage behind a green number, and was a live landmine for
graduation — the moment an operator sets
``qa_gates.self_consistency.required_to_pass=true``, every skip would
become a silent hard PASS. ``passed=True`` is retained so an advisory skip
still cannot veto; callers MUST branch on ``score is None`` rather than
record the value.
"""

from __future__ import annotations

import asyncio
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


_DEFAULT_SAMPLE_COUNT = 3
_DEFAULT_THRESHOLD = 0.55
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_EMBED_MODEL = "nomic-embed-text"
"""Bootstrap fallback only. Production reads ``app_settings.embed_model`` —
the same key the rest of the embedding stack uses."""
_DEFAULT_SUMMARY_PROMPT = (
    "Summarize the following article in two sentences. Stay strictly "
    "grounded in the article — do not introduce facts that aren't "
    "explicitly stated. Output only the summary, no preamble.\n\n"
    "Article topic: {topic}\n\n"
    "Article:\n{content}\n\nSummary:"
)
"""Inline bootstrap fallback — used only when the
:class:`UnifiedPromptManager` is unavailable. Production reads come from
``qa.self_consistency.summarize`` via Langfuse → YAML; this constant
protects the cold-start / test path."""

_SUMMARY_PROMPT_KEY = "qa.self_consistency.summarize"


def _resolve_summary_prompt(*, topic: str, content: str) -> str:
    """Fetch the summary prompt via UnifiedPromptManager, fall back to
    the inline constant if the manager isn't reachable. Mirrors the standard
    resolve-then-fallback prompt pattern so operator-edited prompts in Langfuse
    win without a restart."""
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _SUMMARY_PROMPT_KEY, topic=topic, content=content,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[self_consistency] prompt_manager lookup for %r failed "
            "(%s) — using inline fallback",
            _SUMMARY_PROMPT_KEY, exc,
        )
        return _DEFAULT_SUMMARY_PROMPT.format(topic=topic, content=content)


def is_enabled(site_config: Any) -> bool:
    """Operator gate. ``app_settings.self_consistency_enabled = true``
    to activate."""
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("self_consistency_enabled", False))
    except Exception as exc_primary:
        try:
            v = site_config.get("self_consistency_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception as exc_fallback:
            # poindexter#455 — symmetric to guardrails / deepeval / ragas
            # is_enabled fixes. Silent fallback masked broken SiteConfig
            # wrappers as "self-consistency disabled".
            from services.logger_config import get_logger
            get_logger(__name__).warning(
                "[self_consistency] is_enabled: both get_bool and get raised "
                "while reading self_consistency_enabled — treating as disabled. "
                "Primary: %s: %s. Fallback: %s: %s",
                type(exc_primary).__name__, exc_primary,
                type(exc_fallback).__name__, exc_fallback,
            )
            return False


def _site_int(site_config: Any, key: str, default: int) -> int:
    if site_config is None:
        return default
    try:
        return int(site_config.get_int(key, default))
    except Exception:
        return default


def _site_float(site_config: Any, key: str, default: float) -> float:
    if site_config is None:
        return default
    try:
        return float(site_config.get_float(key, default))
    except Exception:
        return default


async def _sample_summaries(
    *,
    topic: str,
    content: str,
    n: int,
    temperature: float,
    site_config: Any,
) -> list[str]:
    """Sample N short summaries of the content from the writer model.

    Truncate content to first 4000 chars so we don't blow the context
    window. The summaries are deliberately short — we're testing the
    model's CONSISTENCY about the article, not asking for original
    output, so a tight prompt + low max_tokens keeps cost flat.

    Routes through ``dispatch_complete`` so the call honors the
    configured LLM provider per
    ``plugin.llm_provider.primary.standard``. Returns an empty list
    when no DB pool is available (tests / bootstrap) — the caller's
    ``len(samples) < 2`` check then degrades gracefully to
    "self-consistency-skipped".
    """
    pool = getattr(site_config, "_pool", None) if site_config is not None else None
    if pool is None:
        logger.debug(
            "[self_consistency] no DB pool on site_config — skipping samples",
        )
        return []

    from services.llm_providers.dispatcher import dispatch_complete

    truncated = content[:4000]
    prompt = _resolve_summary_prompt(
        topic=topic[:200], content=truncated,
    )
    # poindexter#485 fail-loud sweep: was previously a literal
    # ``ollama/glm-4.7-5090:latest`` fallback that silently masked a
    # missing ``pipeline_writer_model`` setting. Routes through the
    # LOCAL-writer resolver — a self-consistency probe only self-checks the
    # article, so it uses a guaranteed-local writer-grade model
    # (pipeline_local_writer_model, else the writer when it is itself local)
    # and never bills cloud prices when pipeline_writer_model is pinned to a
    # paid model for a writer experiment (the 2026-07-07 Sonnet-canary).
    from services.llm_text import resolve_local_writer_model
    try:
        writer_model = resolve_local_writer_model(site_config=site_config)
    except ValueError as exc:
        # Advisory rail — a missing local pin degrades to skipped, never a
        # hard error (advisory QA must not fail the gate).
        logger.debug(
            "[self_consistency] no local writer-grade model resolvable "
            "(%s) — skipping samples",
            exc,
        )
        return []

    sample_failures: list[str] = []

    async def _one_sample(idx: int) -> str:
        try:
            result = await dispatch_complete(
                pool=pool,
                messages=[{"role": "user", "content": prompt}],
                model=writer_model,
                tier="standard",
                # Without a phase this rail's samples logged as the generic
                # "dispatch_complete" and drew num_ctx from the global
                # ``ollama_num_ctx``. The prompt carries the article, so it is
                # context-sensitive; ``qa_self_consistency_num_ctx`` tunes it.
                phase="qa_self_consistency",
                temperature=temperature,
                max_tokens=250,
            )
            return (getattr(result, "text", "") or "").strip()
        except Exception as e:
            logger.debug("[self_consistency] sample %d failed: %s", idx, e)
            sample_failures.append(f"sample {idx}: {e}")
            return ""

    samples = await asyncio.gather(*[_one_sample(i) for i in range(n)])

    if sample_failures:
        from utils.findings import emit_finding

        emit_finding(
            source="self_consistency_rail",
            kind="self_consistency_sample_failed",
            title=(
                f"{len(sample_failures)} of {n} self-consistency sample(s) "
                "failed"
            ),
            body=(
                f"_sample_summaries: {sample_failures}. Fewer samples than "
                "requested feed the self-consistency score — the rail "
                "still runs, just with a smaller sample."
            ),
            dedup_key="self_consistency_sample_failed",
        )

    return [s for s in samples if s]


async def _pairwise_mean_cosine(
    samples: list[str], *, site_config: Any = None,
) -> float:
    """Embed each sample, return mean pairwise cosine similarity.

    sentence-transformers normalizes by default; dot product == cosine.
    Returns 1.0 when N<2 (degenerate — treat single sample as
    perfectly consistent with itself). Returns -1.0 when no pool
    is available OR any embed call fails.
    """
    if len(samples) < 2:
        return 1.0

    pool = getattr(site_config, "_pool", None) if site_config is not None else None
    if pool is None:
        logger.debug(
            "[self_consistency] no DB pool — cannot embed; signal failure",
        )
        return -1.0

    import numpy as np

    from services.llm_providers.dispatcher import dispatch_embed

    # DB-first per feedback_db_first_config — was a hardcoded literal, which
    # would silently diverge the moment an operator repoints `embed_model`
    # (the key the rest of the embedding stack already reads).
    embed_model = _DEFAULT_EMBED_MODEL
    if site_config is not None:
        try:
            embed_model = (
                str(site_config.get("embed_model", _DEFAULT_EMBED_MODEL) or "").strip()
                or _DEFAULT_EMBED_MODEL
            )
        except Exception:
            embed_model = _DEFAULT_EMBED_MODEL

    embeddings: list[list[float]] = []
    for s in samples:
        try:
            v = await dispatch_embed(
                pool=pool, text=s, model=embed_model, tier="free",
            )
            embeddings.append(v)
        except Exception as e:
            # WARNING, not debug (poindexter#878): the worker only ships INFO+
            # to Loki, so this failure was invisible there while the rail's
            # skip path reported a perfect 100 — the outage had no observable
            # surface at all for 9+ days. Returns on the first failure, so this
            # logs once per evaluate(), not once per sample.
            logger.warning(
                "[self_consistency] embed failed (model=%s) — rail cannot "
                "measure: %s", embed_model, e,
            )
            return -1.0  # signal failure to caller

    arr = np.array(embeddings, dtype=float)
    # Normalize each row to unit length so dot product == cosine.
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    arr = arr / norms

    sims = arr @ arr.T
    n = arr.shape[0]
    # Sum upper-triangular (exclude diagonal), divide by pair count.
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += float(sims[i, j])
            pairs += 1
    return total / pairs if pairs else 1.0


async def evaluate(
    *,
    content: str,
    topic: str,
    site_config: Any = None,
) -> tuple[bool, float | None, str]:
    """Run the self-consistency rail.

    Returns ``(passed, score, reason)`` — see module docstring. ``score`` is
    ``None`` on every skip path: the rail measured nothing, and reporting a
    number there would fabricate a passing measurement. Never raises.
    """
    if not content or not content.strip():
        return True, None, "self-consistency-skipped: empty content"

    n = _site_int(site_config, "self_consistency_sample_count", _DEFAULT_SAMPLE_COUNT)
    temperature = _site_float(
        site_config, "self_consistency_temperature", _DEFAULT_TEMPERATURE,
    )
    threshold = _site_float(
        site_config, "self_consistency_threshold", _DEFAULT_THRESHOLD,
    )

    try:
        samples = await _sample_summaries(
            topic=topic, content=content, n=n,
            temperature=temperature, site_config=site_config,
        )
        if len(samples) < 2:
            return (
                True, None,
                f"self-consistency-skipped: only {len(samples)} valid sample(s) "
                f"(needed 2+, target N={n})",
            )

        score = await _pairwise_mean_cosine(samples, site_config=site_config)
        if score < 0:
            return True, None, "self-consistency-skipped: embedding step failed"

        passed = score >= threshold
        reason = (
            f"self-consistency: mean pairwise cosine={score:.3f} "
            f"across {len(samples)} samples (threshold={threshold:.2f}) "
            f"{'PASS' if passed else 'FAIL — model output is unstable'}"
        )
        return passed, float(score), reason
    except Exception as e:
        logger.warning("[self_consistency] evaluate failed: %s", e, exc_info=True)
        return True, None, f"self-consistency-skipped: {type(e).__name__}: {e}"


__all__ = [
    "evaluate",
    "is_enabled",
]
