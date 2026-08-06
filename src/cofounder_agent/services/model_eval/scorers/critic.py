"""Critic-judge scorer (judge calibration, poindexter#985).

Scores one candidate judge model against the critic golden set by running
the EXACT production critic path (``modules.content.api.MultiModelQA
.critic_review_once`` — same qa.review prompt pack, review window, JSON
parse, and score-over-boolean approval rule), so the number measures what
the pipeline will actually do with that judge.

Primary metric: **judge balanced accuracy** — the mean of the approve-rate
on known-good cases and the veto-rate on known-bad (corrupted) cases, in
[0, 1]. A sycophant that approves everything and a zealot that vetoes
everything both land at 0.5; a coin-flip judge lands near 0.5; only a
discriminating judge scores high. An unusable review (unreachable model,
unparseable output) counts AGAINST the judge on that case — an output the
pipeline can't parse is a failure of the judge, not a skip.

``detail`` carries the per-class rates, per-corruption-kind veto rates,
score percentiles per class, and the unusable count, so the CLI/report can
show the full picture without widening ``MetricResult``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from statistics import median
from typing import Any

from services.logger_config import get_logger
from services.model_eval.types import GoldenSet, MetricResult

logger = get_logger(__name__)

_SLOT = "pipeline_critic_model"

# (title, content, topic, model) -> ReviewerResult-like (has .approved/.score) | None
ReviewFn = Callable[..., Awaitable[Any]]


class CriticScorer:
    """Judge calibration scorer over the critic golden set."""

    capability = "critic"
    primary_metric = "judge_balanced_accuracy"

    def __init__(
        self,
        *,
        pool: Any,
        platform: Any,
        settings_service: Any = None,
        review_fn: ReviewFn | None = None,
    ) -> None:
        self._pool = pool
        self._platform = platform
        self._settings = settings_service
        # Injectable for tests / offline runs — defaults to the real
        # production critic path built lazily per score() call.
        self._review_fn = review_fn

    def _build_review_fn(self, site_config: Any) -> ReviewFn:
        if self._review_fn is not None:
            return self._review_fn
        # Substrate reaches content through the api adapter, never a deep
        # import (modules/content/api.py boundary).
        from modules.content.api import MultiModelQA

        qa = MultiModelQA(
            pool=self._pool,
            settings_service=self._settings,
            site_config=site_config,
            platform=self._platform,
        )

        async def _review(*, title: str, content: str, topic: str, model: str) -> Any:
            return await qa.critic_review_once(
                title=title, content=content, topic=topic, model=model,
            )

        return _review

    async def ascore(
        self, *, model: str, golden_set: GoldenSet, site_config: Any
    ) -> MetricResult:
        review = self._build_review_fn(site_config)
        started = time.monotonic()

        good_total = good_approved = 0
        bad_total = bad_vetoed = 0
        unusable = 0
        scores: dict[str, list[float]] = {"good": [], "bad": []}
        veto_by_kind: dict[str, list[int]] = {}

        for case in golden_set.cases:
            p = case.payload
            expected = p.get("expected")
            kind = str(p.get("kind", "?"))
            try:
                result = await review(
                    title=str(p.get("title", "")),
                    content=str(p.get("content", "")),
                    topic=str(p.get("topic", "")),
                    model=model,
                )
            except Exception as exc:  # noqa: BLE001 — one bad case must not abort the run
                logger.warning(
                    "[model_eval.critic] review raised on case kind=%s: %s",
                    kind, exc,
                )
                result = None

            approved = bool(getattr(result, "approved", False)) if result is not None else False
            if result is None:
                unusable += 1
            else:
                cls = "good" if expected == "approve" else "bad"
                scores[cls].append(float(getattr(result, "score", 0.0)))

            if expected == "approve":
                good_total += 1
                good_approved += int(result is not None and approved)
            else:
                bad_total += 1
                vetoed = int(result is not None and not approved)
                bad_vetoed += vetoed
                veto_by_kind.setdefault(kind, []).append(vetoed)

        good_rate = (good_approved / good_total) if good_total else 0.0
        bad_rate = (bad_vetoed / bad_total) if bad_total else 0.0
        balanced = (good_rate + bad_rate) / 2

        def _pcts(vals: list[float]) -> dict[str, float]:
            if not vals:
                return {}
            s = sorted(vals)
            return {
                "min": s[0],
                "p50": float(median(s)),
                "max": s[-1],
            }

        detail = {
            "good_approve_rate": round(good_rate, 3),
            "bad_veto_rate": round(bad_rate, 3),
            "good_n": good_total,
            "bad_n": bad_total,
            "unusable_reviews": unusable,
            "veto_rate_by_kind": {
                k: round(sum(v) / len(v), 3) for k, v in sorted(veto_by_kind.items())
            },
            "scores_good": _pcts(scores["good"]),
            "scores_bad": _pcts(scores["bad"]),
            "golden_version": golden_set.version,
        }

        return MetricResult(
            slot=_SLOT,
            model=model,
            metric_name=self.primary_metric,
            value=round(balanced, 4),
            n_cases=len(golden_set.cases),
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=detail,
        )
