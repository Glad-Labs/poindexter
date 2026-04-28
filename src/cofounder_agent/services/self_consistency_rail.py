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
  (effectively [0, 1] for sentence-transformers normalized vectors).
- ``reason``: human-readable explanation that lands in the audit log.

Never raises — Ollama errors / embedding errors are caught and
surfaced as ``(True, 1.0, 'self-consistency-skipped: ...')`` so the
rail can't take down the QA pass.
"""

from __future__ import annotations

import asyncio
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


_DEFAULT_SAMPLE_COUNT = 3
_DEFAULT_THRESHOLD = 0.55
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_SUMMARY_PROMPT = (
    "Summarize the following article in two sentences. Stay strictly "
    "grounded in the article — do not introduce facts that aren't "
    "explicitly stated. Output only the summary, no preamble.\n\n"
    "Article topic: {topic}\n\n"
    "Article:\n{content}\n\nSummary:"
)


def is_enabled(site_config: Any) -> bool:
    """Operator gate. ``app_settings.self_consistency_enabled = true``
    to activate."""
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("self_consistency_enabled", False))
    except Exception:
        try:
            v = site_config.get("self_consistency_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception:
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


def _site_str(site_config: Any, key: str, default: str) -> str:
    if site_config is None:
        return default
    try:
        return str(site_config.get(key, default) or default)
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
    """
    from services.ollama_client import OllamaClient

    truncated = content[:4000]
    prompt = _DEFAULT_SUMMARY_PROMPT.format(
        topic=topic[:200], content=truncated,
    )
    writer_model = _site_str(
        site_config, "pipeline_writer_model", "ollama/glm-4.7-5090:latest",
    )
    # Strip the ollama/ prefix; OllamaClient expects bare model names.
    writer_model = writer_model.removeprefix("ollama/")

    async def _one_sample(idx: int) -> str:
        client = OllamaClient()
        try:
            resp = await client.generate(
                prompt=prompt,
                model=writer_model,
                temperature=temperature,
                max_tokens=250,
            )
            return (resp or {}).get("response", "").strip() if isinstance(resp, dict) else str(resp).strip()
        except Exception as e:
            logger.debug("[self_consistency] sample %d failed: %s", idx, e)
            return ""

    samples = await asyncio.gather(*[_one_sample(i) for i in range(n)])
    return [s for s in samples if s]


async def _pairwise_mean_cosine(samples: list[str]) -> float:
    """Embed each sample, return mean pairwise cosine similarity.

    sentence-transformers normalizes by default; dot product == cosine.
    Returns 1.0 when N<2 (degenerate — treat single sample as
    perfectly consistent with itself).
    """
    if len(samples) < 2:
        return 1.0

    from services.ollama_client import OllamaClient
    import numpy as np

    client = OllamaClient()
    embeddings: list[list[float]] = []
    for s in samples:
        try:
            v = await client.embed(s, model="nomic-embed-text")
            embeddings.append(v)
        except Exception as e:
            logger.debug("[self_consistency] embed failed: %s", e)
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
) -> tuple[bool, float, str]:
    """Run the self-consistency rail.

    Returns ``(passed, score, reason)`` — see module docstring.
    Never raises.
    """
    if not content or not content.strip():
        return True, 1.0, "self-consistency-skipped: empty content"

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
                True, 1.0,
                f"self-consistency-skipped: only {len(samples)} valid sample(s) "
                f"(needed 2+, target N={n})",
            )

        score = await _pairwise_mean_cosine(samples)
        if score < 0:
            return True, 1.0, "self-consistency-skipped: embedding step failed"

        passed = score >= threshold
        reason = (
            f"self-consistency: mean pairwise cosine={score:.3f} "
            f"across {len(samples)} samples (threshold={threshold:.2f}) "
            f"{'PASS' if passed else 'FAIL — model output is unstable'}"
        )
        return passed, float(score), reason
    except Exception as e:
        logger.warning("[self_consistency] evaluate failed: %s", e, exc_info=True)
        return True, 1.0, f"self-consistency-skipped: {type(e).__name__}: {e}"


__all__ = [
    "evaluate",
    "is_enabled",
]
