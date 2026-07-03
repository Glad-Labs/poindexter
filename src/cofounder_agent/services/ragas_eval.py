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

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Ragas LLM + embedding wiring
# ---------------------------------------------------------------------------


async def _resolve_judge_model(site_config: Any = None) -> str:
    """Resolve the Ragas judge model from ``ragas_judge_model``.

    Eval is offline + latency-insensitive; operators pin a small judge via
    ``app_settings.ragas_judge_model``. Fails loud (notify + raise) when unset,
    per ``feedback_no_silent_defaults.md`` — the ``cost_tier.*`` fallback was
    removed.
    """
    from services.integrations.operator_notify import notify_operator

    judge: str | None = None
    if site_config is not None:
        try:
            judge = site_config.get("ragas_judge_model", "") or None
        except Exception:
            judge = None

    if judge:
        return str(judge)

    try:
        await notify_operator(
            "ragas_eval: ragas_judge_model is empty — eval skipped "
            "(set ragas_judge_model)",
            critical=True,
            site_config=site_config,
        )
    except Exception as notify_exc:
        # poindexter#455 — used to be silent. The notify path failing
        # means the operator wouldn't hear the critical "ragas can't
        # find a judge model" alert AND wouldn't see why the notify
        # failed. Log it so the alternate stderr/log channels at least
        # carry the picture.
        logger.warning(
            "[ragas_eval] notify_operator failed while reporting missing "
            "judge model — operator will only see the alert in logs: "
            "%s: %s",
            type(notify_exc).__name__, notify_exc,
        )
    raise RuntimeError(
        "ragas_eval: no judge model resolvable — set ragas_judge_model"
    )


async def _build_ragas_models(site_config: Any = None) -> tuple[Any, Any]:
    """Build the LLM + embedding wrappers Ragas needs.

    Ragas expects LangChain-shaped LLM/embedding objects. We wire it
    to the local Ollama backend via langchain-ollama + Ragas's wrappers
    so all eval calls stay local (free, private, audit-friendly).

    Returns ``(llm_wrapper, embeddings_wrapper)``. Lazy-imports the
    Ragas + langchain modules so the module imports cleanly when those
    deps aren't available.

    Judge model is the per-step ``ragas_judge_model`` pin, read directly by
    ``_resolve_judge_model`` (fails loud when unset; the ``cost_tier.budget``
    fallback was removed). Eval is offline + latency-insensitive, so a small
    pinned judge is correct.
    """
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    # local_llm_api_url is the canonical Ollama base-URL setting (same
    # key topic_ranking.py / llm_text.py use). Reading OLLAMA_BASE_URL
    # directly was the legacy env-var bypass we're retiring — see
    # `feedback_no_silent_defaults` and `feedback_no_env_vars`.
    base_url = "http://localhost:11434"
    if site_config is not None:
        try:
            base_url = (
                site_config.get("local_llm_api_url", "") or base_url
            )
        except Exception as exc:
            # Symmetric to the embedding_model read below — surface the
            # read failure so a wrapper bug doesn't silently route Ragas
            # at the wrong backend.
            logger.warning(
                "[ragas_eval] local_llm_api_url read failed (%s: %s) — "
                "using default %r",
                type(exc).__name__, exc, base_url,
            )

    judge_model = (await _resolve_judge_model(site_config)).removeprefix("ollama/")
    embed_model = "nomic-embed-text"
    if site_config is not None:
        try:
            embed_model = (
                site_config.get("embedding_model", "") or embed_model
            )
        except Exception as exc:
            # poindexter#455 — used to be silent. If the operator pinned
            # a non-default embedding model in app_settings but the read
            # raised, the rail would silently fall back to nomic-embed-text
            # and the operator would scratch their head why their pinned
            # model wasn't being used.
            logger.warning(
                "[ragas_eval] embedding_model read failed (%s: %s) — "
                "using default %r",
                type(exc).__name__, exc, embed_model,
            )

    # format="json" is required: without Ollama's constrained decoding,
    # phi4:14b and similar models wrap their responses in markdown code
    # fences (```json ... ```) that RagasOutputParserException cannot
    # parse — even on the fix_output_format retry. All Ragas 0.4.x
    # internal prompts (faithfulness_statements, nli_statements, etc.)
    # expect bare JSON, so JSON-mode is safe for all three metrics. See
    # Glad-Labs/poindexter#1910.
    llm = LangchainLLMWrapper(
        ChatOllama(model=judge_model, base_url=base_url, temperature=0.0, format="json")
    )
    embeddings = LangchainEmbeddingsWrapper(
        OllamaEmbeddings(model=embed_model, base_url=base_url)
    )
    return llm, embeddings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _emit_ragas_score_audit(
    scores: dict[str, float],
    topic: str,
    task_id: str | None,
) -> None:
    """Fire-and-forget audit_log write powering the Grafana ragas panel.

    Schema: ``event_type='ragas_score'`` in ``audit_log``, with
    ``details`` containing the averaged ``score`` (0-1) plus the
    three component scores. The Grafana QA-rails dashboard queries
    these rows for the time-series + latest-stat + last-10-table
    panels.

    Only non-sentinel metrics (score >= 0) feed the average — a
    Ragas judge-LLM hiccup on a single metric shouldn't tank the
    aggregate trend line. Skipped entirely when ALL metrics returned
    -1.0 (full Ragas failure — already surfaced through the warning
    log path above).
    """
    valid = {k: v for k, v in scores.items() if v >= 0}
    if not valid:
        return  # full Ragas failure — nothing useful to record

    avg = sum(valid.values()) / len(valid)
    try:
        from services.audit_log import audit_log_bg
        audit_log_bg(
            "ragas_score",
            "ragas_eval",
            {
                "score": round(float(avg), 4),
                "faithfulness": round(float(scores.get("faithfulness", -1.0)), 4),
                "answer_relevancy": round(float(scores.get("answer_relevancy", -1.0)), 4),
                "context_precision": round(float(scores.get("context_precision", -1.0)), 4),
                "topic": (topic or "")[:200],
                "metric_count": len(valid),
            },
            task_id=task_id,
            severity="info",
        )
    except Exception as exc:  # noqa: BLE001
        # Telemetry write must never fail the Ragas caller — log and
        # carry on. Symmetric to multi_model_qa.py's qa_pass_completed
        # write pattern.
        logger.debug("[ragas] ragas_score audit_log_bg skipped: %s", exc)


async def evaluate_sample(
    *,
    topic: str,
    generated_content: str,
    retrieved_contexts: list[str] | None = None,
    site_config: Any = None,
    task_id: str | None = None,
) -> dict[str, float]:
    """Run Ragas faithfulness + answer_relevancy + context_precision
    against a single (topic, content, contexts) tuple.

    Returns ``{"faithfulness": 0-1, "answer_relevancy": 0-1, "context_precision": 0-1}``.

    Never raises — Ragas errors (Ollama down, judge model not pulled,
    etc) are caught and surfaced as ``-1.0`` for the affected metric.
    Caller correlates scores with audit_log for trend analysis.

    Side effect: on a successful run (>=1 non-sentinel metric) writes
    one ``event_type='ragas_score'`` row to ``audit_log`` for the
    Grafana QA-rails dashboard's Ragas panel block. Best-effort — the
    write failing never affects the returned scores.
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

        llm, embeddings = await _build_ragas_models(site_config)

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
        scores_raw = result.scores[0] if result.scores else {}  # type: ignore[union-attr]
        scores = {
            "faithfulness": float(scores_raw.get("faithfulness", -1.0) or -1.0),
            "answer_relevancy": float(scores_raw.get("answer_relevancy", -1.0) or -1.0),
            "context_precision": float(scores_raw.get("context_precision", -1.0) or -1.0),
        }
        _emit_ragas_score_audit(scores, topic, task_id)
        return scores
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
    except Exception as exc_primary:
        try:
            v = site_config.get("ragas_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception as exc_fallback:
            # poindexter#455 — symmetric to guardrails / deepeval
            # is_enabled fixes. Silent fallback masked broken
            # SiteConfig wrappers as "ragas disabled".
            logger.warning(
                "[ragas_eval] is_enabled: both get_bool and get raised while "
                "reading ragas_enabled — treating as disabled. "
                "Primary: %s: %s. Fallback: %s: %s",
                type(exc_primary).__name__, exc_primary,
                type(exc_fallback).__name__, exc_fallback,
            )
            return False


__all__ = [
    "evaluate_sample",
    "is_enabled",
]
