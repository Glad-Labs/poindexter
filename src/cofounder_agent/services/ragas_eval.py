"""Ragas-based RAG evaluation (#205).

Measures whether our retrieval actually helps the writer. Three
canonical Ragas metrics wired to our local Ollama backend:

- ``faithfulness`` — does the generated content stay grounded in
  the retrieved context, or does the model invent claims?
- ``answer_relevancy`` — does the generated content address the
  topic the operator requested?
- ``context_precision`` — were the retrieved documents actually
  useful, or did the retriever pull in noise?

Doesn't run synchronously inside the pipeline (each metric is a
~2K-token LLM call; running them per-task would tank throughput).
Designed as a sampling job + on-demand CLI:

  poindexter ragas evaluate --limit 10

Operator picks a sample window, the command builds Ragas TestSet
records from recent content_tasks rows + their retrieval traces,
runs Ragas, prints the score breakdown.

Write-back path
---------------

Scores land in the ``audit_log`` table under
``event_type='ragas_score'`` so existing trend dashboards (Grafana
panel that aggregates audit_log by event_type) pick them up
automatically. No new table.
"""

from __future__ import annotations

import os
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Ragas LLM + embedding wiring
# ---------------------------------------------------------------------------


def _build_ragas_models(site_config: Any = None) -> tuple[Any, Any]:
    """Build the LLM + embedding wrappers Ragas needs.

    Ragas expects LangChain-shaped LLM/embedding objects. We wire it
    to the local Ollama backend via langchain-ollama + Ragas's wrappers
    so all eval calls stay local (free, private, audit-friendly).

    Returns ``(llm_wrapper, embeddings_wrapper)``. Lazy-imports the
    Ragas + langchain modules so the module imports cleanly when those
    deps aren't available.
    """
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper

    base_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")

    # Judge model — pick the same one our writer uses by default; the
    # operator can override per-call. ``llama3:8b`` is a reasonable
    # judge floor; the writer's glm-4.7 / qwen3 variants are stronger
    # but heavier per call.
    judge_model = "llama3:8b"
    embed_model = "nomic-embed-text"
    if site_config is not None:
        try:
            judge_model = (
                site_config.get("ragas_judge_model", "") or judge_model
            )
            embed_model = (
                site_config.get("embedding_model", "") or embed_model
            )
        except Exception:
            pass

    llm = LangchainLLMWrapper(
        ChatOllama(model=judge_model, base_url=base_url, temperature=0.0)
    )
    embeddings = LangchainEmbeddingsWrapper(
        OllamaEmbeddings(model=embed_model, base_url=base_url)
    )
    return llm, embeddings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_sample(
    *,
    topic: str,
    generated_content: str,
    retrieved_contexts: list[str] | None = None,
    site_config: Any = None,
) -> dict[str, float]:
    """Run Ragas faithfulness + answer_relevancy + context_precision
    against a single (topic, content, contexts) tuple.

    Returns ``{"faithfulness": 0-1, "answer_relevancy": 0-1, "context_precision": 0-1}``.

    Never raises — Ragas errors (Ollama down, judge model not pulled,
    etc) are caught and surfaced as ``-1.0`` for the affected metric.
    Caller correlates scores with audit_log for trend analysis.
    """
    if not topic or not generated_content:
        return {
            "faithfulness": -1.0,
            "answer_relevancy": -1.0,
            "context_precision": -1.0,
        }

    contexts = retrieved_contexts or [""]

    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            faithfulness,
        )

        llm, embeddings = _build_ragas_models(site_config)

        ds = Dataset.from_dict({
            "question": [topic],
            "answer": [generated_content],
            "contexts": [contexts],
            "ground_truth": [""],  # context_precision tolerates empty
        })

        result = evaluate(
            ds,
            metrics=[faithfulness, answer_relevancy, context_precision],
            llm=llm,
            embeddings=embeddings,
            raise_exceptions=False,
        )
        scores = result.scores[0] if result.scores else {}
        return {
            "faithfulness": float(scores.get("faithfulness", -1.0) or -1.0),
            "answer_relevancy": float(scores.get("answer_relevancy", -1.0) or -1.0),
            "context_precision": float(scores.get("context_precision", -1.0) or -1.0),
        }
    except Exception as e:
        logger.warning("[ragas] evaluate_sample failed: %s", e, exc_info=True)
        return {
            "faithfulness": -1.0,
            "answer_relevancy": -1.0,
            "context_precision": -1.0,
        }


def is_enabled(site_config: Any) -> bool:
    """Operator gate. ``app_settings.ragas_enabled = true`` to run."""
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("ragas_enabled", False))
    except Exception:
        try:
            v = site_config.get("ragas_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception:
            return False


__all__ = [
    "evaluate_sample",
    "is_enabled",
]
