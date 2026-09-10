"""End-to-end retrieval evaluation — does the retriever find the right chunk?

Complements two existing evaluators and overlaps neither:

- ``services/ragas_eval.py`` scores the *generated* content given whatever
  retrieval returned (faithfulness / answer-relevancy / context-precision). It
  cannot tell you the retriever missed a document, only that the answer was
  thin.
- ``services/model_eval/scorers/reranker.py`` scores a cross-encoder on a fixed
  candidate list. It cannot tell you the retriever never produced the candidate.

This module scores the retriever against the whole live corpus using the
``model_eval_retrieval`` golden set, so a miss is attributable to retrieval
itself.

Metrics
-------
``recall@k`` / ``MRR`` / ``nDCG@k``   — did the gold chunk come back, and where?
``payload_contains_span``            — did the DELIVERED text actually contain
                                       the answer span? A hit whose payload is
                                       truncated past the answer is not a hit
                                       for any downstream consumer.
``legacy_payload_contains_span``     — would the pre-#1033 500-char preview
                                       have contained it? This is the
                                       retrospective: the gap between these two
                                       is what the payload repair bought,
                                       measured on the current corpus without
                                       reverting anything.

All four are reported **stratified by region** (``head`` = answer span inside
the first 500 chars, ``deep`` = past it). The deep/head recall gap is the
headline number; see ``golden_sets/retrieval.py`` for why.

Results land in ``audit_log`` under ``event_type='retrieval_eval'`` — same
"no new table" posture as ragas_eval, so Grafana picks it up via the existing
Postgres datasource.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from services.logger_config import get_logger
from services.model_eval.golden_sets.retrieval import LEGACY_PREVIEW_CHARS
from services.model_eval.metrics import mrr, ndcg_at_k, recall_at_k
from services.model_eval.types import GoldenSet, MetricResult

logger = get_logger(__name__)

_SLOT = "retrieval"
_PRIMARY_K = 5
_RECALL_KS = (1, 5, 10)

_WORD = re.compile(r"[a-z0-9]{4,}")


def _content_words(text: str) -> set[str]:
    """Lowercase tokens of 4+ chars — a cheap, deterministic overlap basis."""
    return set(_WORD.findall(text.lower()))


def _contains_span(payload: str, span: str, *, threshold: float = 0.6) -> bool:
    """Does ``payload`` carry the substance of ``span``?

    Exact substring first (the common case — the payload IS the chunk). Falls
    back to content-word overlap so a payload that was windowed or normalized
    still counts, rather than scoring a false miss on whitespace differences.
    """
    if not span:
        return False
    if span in payload:
        return True
    span_words = _content_words(span)
    if not span_words:
        return False
    overlap = len(span_words & _content_words(payload)) / len(span_words)
    return overlap >= threshold


@dataclass
class _Bucket:
    """Running tallies for one region (or the whole set)."""

    n: int = 0
    recall: dict[int, float] = field(default_factory=lambda: dict.fromkeys(_RECALL_KS, 0.0))
    mrr_sum: float = 0.0
    ndcg_sum: float = 0.0
    payload_hits: int = 0
    legacy_hits: int = 0

    def add(
        self,
        ranked_rel: list[bool],
        *,
        payload_ok: bool,
        legacy_ok: bool,
    ) -> None:
        self.n += 1
        for k in _RECALL_KS:
            self.recall[k] += recall_at_k(ranked_rel, k)
        self.mrr_sum += mrr(ranked_rel)
        self.ndcg_sum += ndcg_at_k([1.0 if r else 0.0 for r in ranked_rel], _PRIMARY_K)
        self.payload_hits += int(payload_ok)
        self.legacy_hits += int(legacy_ok)

    def summary(self) -> dict[str, Any]:
        if not self.n:
            return {"n": 0}
        out: dict[str, Any] = {"n": self.n}
        for k in _RECALL_KS:
            out[f"recall@{k}"] = round(self.recall[k] / self.n, 4)
        out["mrr"] = round(self.mrr_sum / self.n, 4)
        out[f"ndcg@{_PRIMARY_K}"] = round(self.ndcg_sum / self.n, 4)
        # Conditioned on the whole set, not on hits — "of every question asked,
        # how often did the consumer actually receive the answer text".
        out["payload_contains_span"] = round(self.payload_hits / self.n, 4)
        out["legacy_payload_contains_span"] = round(self.legacy_hits / self.n, 4)
        return out


def _gold_key(payload: dict[str, Any]) -> tuple[str, str]:
    return str(payload["source_table"]), str(payload["source_id"])


async def score_retrieval(
    *,
    pool: Any,
    site_config: Any,
    golden_set: GoldenSet,
    hybrid: bool | None = None,
    rerank: bool | None = None,
    graph_expand: bool | None = None,
    top_k: int = 10,
    variant: str = "prod",
    embed_base_url: str | None = None,
    source_filter: list[str] | None = None,
) -> MetricResult:
    """Run every golden case through the real retriever and score the ranking.

    ``hybrid`` / ``rerank`` default to ``None`` = whatever prod is configured
    for, so the default run measures the live system. Passing explicit booleans
    lets the caller A/B a configuration (vector-only vs hybrid vs +rerank)
    against the identical case set — the comparison step 3 will need.

    Matching is at ``(source_table, source_id)`` rather than including
    ``chunk_index``: retrieval returning a DIFFERENT chunk of the right
    document is a real, useful hit for every consumer, and penalising it would
    measure chunk-boundary luck instead of retrieval.
    """
    from services.rag_engine import get_rag_retriever

    # Explicit override wins so this is runnable from the HOST, where the
    # DB-configured host.docker.internal does not resolve (the same split
    # that forced a two-pass #1033 backfill).
    embed_base_url = (
        embed_base_url
        or (site_config.get("local_llm_api_url", "") or "").strip()
        or None
    )

    # Deliberately does NOT pass ``site_config`` — it mirrors how the only
    # production caller (``MemoryClient._search_via_rag_engine``) invokes the
    # retriever: explicit parameters, no config object.
    #
    # This is not a style choice. The whole settings block inside
    # ``get_rag_retriever`` is gated on ``if site_config is not None``, so
    # passing one activates ``rag_source_filter`` — which is ``'posts'`` on this
    # install. The first run of this eval did pass site_config and scored
    # claude_sessions / memory / audit at a flat 0.000 across 82 of 120 cases:
    # not a quality finding, just a scope no consumer actually uses. Reproducing
    # the consumer's parameterisation is the only way the number means anything.
    #
    # (That ``rag_source_filter`` binds for nobody in production is itself a
    # finding — see the module's docs section — but it must not silently become
    # this eval's corpus.)
    if hybrid is None:
        hybrid = bool(site_config.get_bool("rag_hybrid_enabled", False))
    if rerank is None:
        rerank = bool(site_config.get_bool("rag_rerank_enabled", False))
    min_similarity = float(site_config.get_float("rag_min_similarity", 0.3))

    # A requested-but-unavailable reranker degrades to passthrough inside
    # ``get_rag_retriever`` with only a WARNING. That is right for production
    # (retrieval keeps working) and wrong for an eval: the run would be
    # labelled "prod" while measuring hybrid-without-rerank, and the two
    # variants would come back byte-identical — which is exactly what happened
    # on the first host run here, and reads as "the reranker does nothing"
    # rather than "the reranker never ran". Detect it and say so.
    rerank_degraded = False
    if rerank:
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            rerank_degraded = True
            logger.warning(
                "[retrieval_eval] rerank requested but sentence-transformers is "
                "absent — this run measures hybrid WITHOUT rerank. Variant "
                "relabelled; do not compare it against an in-container run."
            )

    retriever = await get_rag_retriever(
        pool,
        top_k=top_k,
        min_similarity=min_similarity,
        source_filter=source_filter,
        hybrid=hybrid,
        rerank=rerank,
        graph_expand=graph_expand,
        embed_base_url=embed_base_url,
    )
    if rerank_degraded:
        variant = f"{variant}[rerank-DEGRADED]"

    overall = _Bucket()
    by_region: dict[str, _Bucket] = {"head": _Bucket(), "deep": _Bucket()}
    by_source: dict[str, _Bucket] = {}
    errors = 0

    t0 = time.monotonic()
    for case in golden_set.cases:
        gold = _gold_key(case.payload)
        span = str(case.payload.get("span_text") or "")
        region = str(case.payload.get("region") or "head")
        src = str(case.payload.get("source_table") or "?")

        try:
            nodes = await retriever.aretrieve(case.query)
        except Exception as e:  # noqa: BLE001
            # Count it, never swallow it into a silent zero — a retriever that
            # raises on every case would otherwise report recall 0.0 and read
            # as a quality regression instead of an outage.
            errors += 1
            logger.warning("[retrieval_eval] retrieve failed: %s", e)
            continue

        ranked_rel: list[bool] = []
        payload_ok = legacy_ok = False
        for i, nws in enumerate(nodes):
            md = dict(getattr(nws.node, "metadata", {}) or {})
            key = (str(md.get("source_table", "")), str(md.get("source_id", "")))
            hit = key == gold
            ranked_rel.append(hit)
            if hit and not payload_ok:
                text = getattr(nws.node, "text", "") or ""
                payload_ok = _contains_span(text, span)
                legacy_ok = _contains_span(text[:LEGACY_PREVIEW_CHARS], span)
            del i

        overall.add(ranked_rel, payload_ok=payload_ok, legacy_ok=legacy_ok)
        by_region.setdefault(region, _Bucket()).add(
            ranked_rel, payload_ok=payload_ok, legacy_ok=legacy_ok
        )
        by_source.setdefault(src, _Bucket()).add(
            ranked_rel, payload_ok=payload_ok, legacy_ok=legacy_ok
        )

    detail: dict[str, Any] = {
        "golden_version": golden_set.version,
        "golden_name": golden_set.name,
        "variant": variant,
        "hybrid": hybrid,
        "rerank": rerank,
        "graph_expand": graph_expand,
        "rerank_degraded": rerank_degraded,
        "top_k": top_k,
        "source_filter": source_filter,
        "errors": errors,
        "overall": overall.summary(),
        "by_region": {k: v.summary() for k, v in by_region.items() if v.n},
        "by_source_table": {k: v.summary() for k, v in by_source.items() if v.n},
    }
    head = detail["by_region"].get("head", {})
    deep = detail["by_region"].get("deep", {})
    if head and deep:
        # The headline. Positive => deep content is still harder to reach.
        detail["deep_head_recall_gap"] = round(
            head.get(f"recall@{_PRIMARY_K}", 0.0) - deep.get(f"recall@{_PRIMARY_K}", 0.0),
            4,
        )

    return MetricResult(
        slot=_SLOT,
        model=variant,
        metric_name=f"recall@{_PRIMARY_K}",
        value=overall.summary().get(f"recall@{_PRIMARY_K}", 0.0) if overall.n else 0.0,
        n_cases=overall.n,
        latency_ms=int((time.monotonic() - t0) * 1000),
        detail=detail,
    )


async def persist_result(pool: Any, result: MetricResult) -> None:
    """Write one eval run to ``audit_log`` (event_type='retrieval_eval')."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_log (event_type, source, severity, details, "timestamp")
            VALUES ('retrieval_eval', 'retrieval_eval', 'info', $1::jsonb, now())
            """,
            json.dumps(
                {
                    "slot": result.slot,
                    "variant": result.model,
                    "metric": result.metric_name,
                    "value": result.value,
                    "n_cases": result.n_cases,
                    "latency_ms": result.latency_ms,
                    **result.detail,
                }
            ),
        )


__all__ = ["score_retrieval", "persist_result"]
