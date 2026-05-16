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
    """Resolve Ragas judge model via cost-tier API + per-call-site fallback.

    Lane B batch 2 sweep migration. Order:

    1. ``resolve_tier_model(pool, 'budget')`` — operator-tuned tier
       mapping (``app_settings.cost_tier.budget.model``). Eval is
       offline + latency-insensitive; the budget tier is correct.
    2. ``app_settings[ragas_judge_model]`` — per-call-site override
       (existing key; pre-dates this sweep).
    3. ``notify_operator()`` + raise — per ``feedback_no_silent_defaults.md``.

    ``site_config`` is the optional DI-injected SiteConfig instance the
    Ragas helper already accepts. The pool comes off ``site_config._pool``
    when available; in tests / legacy paths without a pool the tier step
    is skipped and the legacy setting is used directly.
    """
    from services.integrations.operator_notify import notify_operator
    from services.llm_providers.dispatcher import resolve_tier_model

    pool = getattr(site_config, "_pool", None) if site_config is not None else None
    if pool is not None:
        try:
            return await resolve_tier_model(pool, "budget")
        except (RuntimeError, ValueError, AttributeError) as exc:
            tier_exc: Exception | None = exc
        else:
            tier_exc = None
    else:
        tier_exc = RuntimeError("no asyncpg pool available")

    fallback: str | None = None
    if site_config is not None:
        try:
            fallback = site_config.get("ragas_judge_model", "") or None
        except Exception:
            fallback = None

    if fallback:
        try:
            await notify_operator(
                f"ragas_eval: cost_tier='budget' resolution failed "
                f"({tier_exc}); falling back to ragas_judge_model={fallback!r}",
                critical=False,
                site_config=site_config,
            )
        except Exception:
            pass  # Ragas eval is best-effort; never crash on notify failure
        return str(fallback)

    try:
        await notify_operator(
            f"ragas_eval: cost_tier='budget' has no model AND "
            f"ragas_judge_model is empty — eval skipped: {tier_exc}",
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
        "ragas_eval: no judge model resolvable via tier or "
        "ragas_judge_model setting"
    ) from tier_exc


async def _build_ragas_models(site_config: Any = None) -> tuple[Any, Any]:
    """Build the LLM + embedding wrappers Ragas needs.

    Ragas expects LangChain-shaped LLM/embedding objects. We wire it
    to the local Ollama backend via langchain-ollama + Ragas's wrappers
    so all eval calls stay local (free, private, audit-friendly).

    Returns ``(llm_wrapper, embeddings_wrapper)``. Lazy-imports the
    Ragas + langchain modules so the module imports cleanly when those
    deps aren't available.

    Lane B sweep: judge model is resolved through the cost-tier API
    (``cost_tier='budget'``) with ``ragas_judge_model`` as the
    per-call-site backstop. Eval is offline + latency-insensitive,
    so the budget tier is correct.
    """
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper

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
        scores_raw = result.scores[0] if result.scores else {}
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
