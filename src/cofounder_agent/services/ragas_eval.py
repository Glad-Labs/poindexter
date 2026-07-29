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

import asyncio
import math
from typing import Any

from services.gpu_admission import GpuBusyError
from services.logger_config import get_logger

logger = get_logger(__name__)


def _run_embed_coro(coro: Any) -> Any:
    """Bridge an embedding coroutine onto whatever loop is calling us.

    Ragas 0.4.x's ``ResponseRelevancy.calculate_similarity`` (the
    ``answer_relevancy`` metric) calls the LangChain ``Embeddings`` sync
    interface — ``embed_query``/``embed_documents`` — directly and
    synchronously from inside its own async ``_ascore``, so this is
    reached from *within* whatever loop is already running (poindexter#847).

    A fresh thread + new event loop would be the usual sync-from-async
    bridge, but it's wrong here: ``dispatch_embed`` drives the caller's
    asyncpg pool, and asyncpg connections are bound to the loop they were
    created on — handing them to a second loop on another thread raises.
    So this reuses the CURRENT loop instead. That's safe specifically
    because Ragas's own ``Executor.results()`` always calls
    ``nest_asyncio.apply()`` before scoring any metric (see
    ``ragas/executor.py`` and ``ragas/async_utils.py``), which patches
    ``asyncio.run`` to re-enter the already-running loop rather than
    reject it. ``nest_asyncio`` is a direct dependency of ``ragas``
    itself, so it's guaranteed importable here. Applying it again is a
    no-op if Ragas already did (``nest_asyncio.apply`` checks
    ``asyncio._nest_patched`` first) — this makes the bridge correct on
    its own terms rather than implicitly trusting Ragas's internals to
    keep doing so.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import nest_asyncio

    nest_asyncio.apply()
    return asyncio.run(coro)


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


def _build_dispatcher_ragas_wrappers(
    *, pool: Any, judge_model: str, embed_model: str,
) -> tuple[Any, Any]:
    """LangChain-shaped adapters that route Ragas through the dispatcher.

    Poindexter#826: ``ChatOllama`` / ``OllamaEmbeddings`` own their HTTP
    transport, so Ragas judge + embedding calls bypassed the LiteLLM
    dispatcher — no ``cost_logs`` rows, no Langfuse traces, and the judge
    resolved its own base URL (silently ignoring per-model ``api_base``
    overrides). These adapters re-establish the seam at the layer the
    libraries provide: a ``BaseChatModel`` whose ``_agenerate`` is a
    normal ``dispatch_complete`` call (``phase='qa_ragas_judge'``,
    ``response_format=json_object`` — the #1910 constrained-decoding fix,
    previously ``format="json"``), and an ``Embeddings`` whose async
    methods call ``dispatch_embed``.

    Ragas 0.4's executor drives the async LangChain surface
    (``agenerate_prompt`` / ``aembed_*``) — except ``answer_relevancy``,
    whose ``calculate_similarity`` calls the *sync* ``embed_query``/
    ``embed_documents`` directly from inside its own async ``_ascore``
    (poindexter#847). The chat model's sync ``_generate`` still raises
    loudly — Ragas never reaches it. The embeddings' sync methods bridge
    onto the caller's own loop via ``_run_embed_coro`` rather than
    raising, because the asyncpg pool ``dispatch_embed`` uses is
    loop-bound: reusing the current loop (safe here — see
    ``_run_embed_coro``) avoids the cross-loop footgun a fresh
    thread+loop bridge would hit.
    """
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from services.gpu_scheduler import qa_rail_wait_budget_s
    from services.llm_providers.dispatcher import dispatch_complete, dispatch_embed

    class _DispatcherChatModel(BaseChatModel):
        judge_model_name: str
        dispatch_pool: Any

        model_config = {"arbitrary_types_allowed": True}

        @property
        def _llm_type(self) -> str:
            return "poindexter_dispatcher"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise NotImplementedError(
                "_DispatcherChatModel is async-only — Ragas drives the "
                "async LangChain surface (poindexter#826)"
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            role_map = {"ai": "assistant", "system": "system"}
            payload = [
                {
                    "role": role_map.get(getattr(m, "type", "human"), "user"),
                    "content": str(m.content),
                }
                for m in messages
            ]
            # poindexter#914 P2 — bounded wait. This rail is fail-soft: a
            # degraded run reports sentinel scores and emits a finding, which
            # is strictly better than queueing behind a ~230s image render
            # until the 900s lock ceiling. GpuBusyError propagates to the
            # rail's existing degraded path rather than being swallowed here,
            # so a skip stays visible instead of looking like a clean pass.
            completion = await dispatch_complete(
                pool=self.dispatch_pool,
                messages=payload,
                model=self.judge_model_name,
                tier="standard",
                phase="qa_ragas_judge",
                temperature=0.0,
                response_format={"type": "json_object"},
                max_wait_s=qa_rail_wait_budget_s(),
                priority="background",
            )
            text = getattr(completion, "text", "") or ""
            generation = ChatGeneration(
                message=AIMessage(content=text),
                generation_info={"finish_reason": "stop"},
            )
            return ChatResult(generations=[generation])

    class _DispatcherEmbeddings(Embeddings):
        def __init__(self, pool_: Any, model: str):
            self._pool = pool_
            self._model = model

        def embed_documents(self, texts):
            return _run_embed_coro(self.aembed_documents(texts))

        def embed_query(self, text):
            return _run_embed_coro(self.aembed_query(text))

        async def aembed_documents(self, texts):
            return [
                await dispatch_embed(self._pool, t, self._model) for t in texts
            ]

        async def aembed_query(self, text):
            return await dispatch_embed(self._pool, text, self._model)

    llm = LangchainLLMWrapper(
        _DispatcherChatModel(judge_model_name=judge_model, dispatch_pool=pool)
    )
    embeddings = LangchainEmbeddingsWrapper(_DispatcherEmbeddings(pool, embed_model))
    return llm, embeddings


async def _build_ragas_models(
    site_config: Any = None, pool: Any = None,
) -> tuple[Any, Any]:
    """Build the LLM + embedding wrappers Ragas needs.

    Ragas expects LangChain-shaped LLM/embedding objects. With a ``pool``
    (production / worker path) they route through the LiteLLM dispatcher
    (poindexter#826) so judge + embedding calls land in cost accounting,
    Langfuse, and per-model ``api_base`` overrides. Without a pool the
    legacy direct langchain-ollama wiring is used (tests / bootstrap) —
    the same dispatcher-or-direct shape as ``llm_text`` / ``topic_ranking``.

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

    # Production path: route judge + embeddings through the LiteLLM
    # dispatcher (poindexter#826). The adapters carry the #1910 JSON-mode
    # constraint via response_format={"type": "json_object"}.
    if pool is not None:
        return _build_dispatcher_ragas_wrappers(
            pool=pool, judge_model=judge_model, embed_model=embed_model,
        )

    # format="json" is required: without Ollama's constrained decoding,
    # phi4:14b and similar models wrap their responses in markdown code
    # fences (```json ... ```) that RagasOutputParserException cannot
    # parse — even on the fix_output_format retry. All Ragas 0.4.x
    # internal prompts (faithfulness_statements, nli_statements, etc.)
    # expect bare JSON, so JSON-mode is safe for all three metrics. See
    # Glad-Labs/glad-labs-stack#1910.
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


def _coerce_metric(value: Any) -> float:
    """Collapse a raw Ragas metric value onto the -1.0-sentinel contract.

    Ragas under ``raise_exceptions=False`` reports a failed metric as
    ``float('nan')`` — which is truthy, so the previous ``or -1.0`` guard
    passed it straight through into the ragas_score audit details, where
    ``json.dumps`` emitted the literal ``NaN`` that Postgres jsonb rejects
    (the whole row was lost; Loki 'Failed to write audit log
    event=ragas_score'). Missing / None / unparseable / non-finite values
    all collapse to the documented -1.0 sentinel. A genuine 0.0 score is
    preserved — the old falsy check silently replaced it with the sentinel.
    """
    if value is None:
        return -1.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return -1.0
    return score if math.isfinite(score) else -1.0


def _emit_degraded_metrics_finding(failed_metrics: list[str], task_id: str | None) -> None:
    """Make a silently-sentineled Ragas metric VISIBLE (poindexter#847).

    ``_emit_ragas_score_audit`` used to just quietly drop a -1.0 metric
    from the averaged ``ragas_score`` audit row, with a reduced
    ``metric_count`` as the only trace — invisible on any dashboard. The
    former repeat offender — Ragas 0.4.3's ``ResponseRelevancy.
    calculate_similarity`` calling the sync ``embed_query``/
    ``embed_documents`` against our async-only ``_DispatcherEmbeddings``,
    failing ``answer_relevancy`` on every call — is fixed at the source
    (``_run_embed_coro``); this stays as the general safety net for any
    OTHER metric/judge hiccup (Ollama down, judge model unpulled, etc.),
    so a degraded rail is never silent again. One finding per distinct
    failed-metric combination, deduped (not task-scoped) so a chronic
    per-post failure pages once instead of once per post — the
    downstream findings_alert_router/alert_dispatcher chain owns
    collapsing repeat fires, not this call site.
    """
    key = ",".join(failed_metrics)
    try:
        from utils.findings import emit_finding

        emit_finding(
            source="ragas_eval.evaluate_sample",
            kind="qa_rail_degraded",
            severity="warn",
            title=f"Ragas metric(s) failing: {key}",
            body=(
                f"evaluate_sample() collapsed {key} to the -1.0 sentinel "
                "(Ragas raised or returned a non-finite score under "
                "raise_exceptions=False). The metric is silently excluded "
                "from the averaged ragas_score, so the aggregate trend no "
                "longer reflects all three dimensions. Rail is advisory — "
                "this does not block publish. See poindexter#847."
            ),
            dedup_key=f"qa_rail_degraded:ragas:{key}",
            extra={"failed_metrics": failed_metrics, "task_id": task_id},
        )
    except Exception:  # noqa: BLE001  # silent-ok: emit_finding is fire-and-forget
        # by contract (utils.findings docstring says it never raises); this only
        # fires if that contract itself breaks. Mirrors the identical guard in
        # deepeval_rails._surface_deepeval_degraded.
        logger.debug("[ragas] degraded-metric finding emit skipped", exc_info=True)


def _surface_gpu_busy_skip(rail: str, busy: Any, *, task_id: str | None) -> None:
    """Report a QA rail that skipped because admission refused the wait.

    Deliberately its own finding kind (poindexter#914 P2). A contention skip
    and a broken rail produce the same sentinel scores, so without a distinct
    kind the two are indistinguishable in triage — and a burst of GPU pressure
    would read as the QA stack degrading. ``info`` severity, not ``warn``: a
    bounded skip under load is the system working as designed, whereas
    ``qa_rail_degraded`` means something is actually wrong.
    """
    try:
        from utils.findings import emit_finding

        eta = f"{busy.eta_seconds:.0f}s" if busy.eta_seconds is not None else "unknown"
        emit_finding(
            source=f"{rail}_eval.evaluate_sample",
            kind="qa_rail_gpu_busy_skip",
            severity="info",
            title=f"{rail} rail skipped — GPU busy beyond its wait budget",
            body=(
                f"GPU admission refused the wait ({busy.reason}; holder ETA "
                f"~{eta}), so the {rail} rail skipped this pass rather than "
                "queueing behind a long render up to the lock ceiling. Scores "
                "are sentinels and the rail is advisory, so publish is not "
                "blocked. Persistent skips mean render pressure is crowding "
                "out QA — raise gpu_sched_qa_rail_max_wait_s, or reduce the "
                "contention."
            ),
            dedup_key=f"qa_rail_gpu_busy_skip:{rail}",
            extra={"rail": rail, "reason": busy.reason,
                   "eta_seconds": busy.eta_seconds, "task_id": task_id},
        )
    except Exception:  # noqa: BLE001  # silent-ok: emit_finding never raises by
        # contract; this only fires if that contract itself breaks. Mirrors the
        # guard in _surface_degraded above.
        logger.debug("[%s] gpu-busy-skip finding emit skipped", rail, exc_info=True)


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
    aggregate trend line. Any sentinel metric also raises a
    ``qa_rail_degraded`` finding (poindexter#847) — the audit row's
    reduced ``metric_count`` alone was invisible on any dashboard.
    Skipped entirely when ALL metrics returned -1.0 (full Ragas
    failure — already surfaced through the warning log path above).
    """
    valid = {k: v for k, v in scores.items() if v >= 0}
    failed = sorted(k for k, v in scores.items() if v < 0)
    if failed:
        _emit_degraded_metrics_finding(failed, task_id)
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
    pool: Any = None,
) -> dict[str, float]:
    """Run Ragas faithfulness + answer_relevancy + context_precision
    against a single (topic, content, contexts) tuple.

    Returns ``{"faithfulness": 0-1, "answer_relevancy": 0-1, "context_precision": 0-1}``.

    Never raises for eval failures — Ragas errors (Ollama down, judge
    model not pulled, etc) are caught and surfaced as ``-1.0`` for the
    affected metric. ``ImportError`` is the deliberate exception: a
    missing/broken ragas dependency is a deployment regression, not a
    transient eval failure, and it propagates so the caller can surface
    the true cause (poindexter#839 — the ragas 0.4.x /
    langchain-community 0.4.2 incompat read as "judge or embedding
    backend likely unreachable" for 12 days).
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

        llm, embeddings = await _build_ragas_models(site_config, pool=pool)

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
            "faithfulness": _coerce_metric(scores_raw.get("faithfulness")),
            "answer_relevancy": _coerce_metric(scores_raw.get("answer_relevancy")),
            "context_precision": _coerce_metric(scores_raw.get("context_precision")),
        }
        _emit_ragas_score_audit(scores, topic, task_id)
        return scores
    except ImportError:
        # A broken ragas import chain (e.g. ragas 0.4.x importing
        # langchain_community.chat_models.vertexai, removed in
        # langchain-community 0.4.2 — poindexter#839) is a deployment
        # regression. Swallowing it into sentinels made a 12-day outage
        # read as a backend hiccup; log the truth, then fail loud so the
        # caller surfaces an accurate misconfig skip.
        logger.warning(
            "[ragas] ragas import chain broken (dependency incompat?) — "
            "failing loud", exc_info=True,
        )
        raise
    except GpuBusyError as busy:
        # poindexter#914 P2 — an admission skip is NOT a rail malfunction, and
        # must not read like one. Same sentinel result (the rail genuinely did
        # not run), but a distinct finding kind so "the GPU was busy" can never
        # be mistaken for "Ragas is broken" during triage. Without this split
        # the contention skips would silently inflate the qa_rail_degraded rate
        # and send someone debugging a healthy rail.
        logger.info(
            "[ragas] skipped — GPU admission rejected (%s, holder ETA ~%ss)",
            busy.reason,
            f"{busy.eta_seconds:.0f}" if busy.eta_seconds is not None else "?",
        )
        _surface_gpu_busy_skip("ragas", busy, task_id=task_id)
        return {
            "faithfulness": -1.0,
            "answer_relevancy": -1.0,
            "context_precision": -1.0,
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
