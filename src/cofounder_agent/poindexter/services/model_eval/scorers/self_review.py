"""Self-review detector scorer (poindexter#1031).

Runs the real ``detect_contradictions`` path over the self-review golden set
and reports how often it actually catches an injected contradiction.

The metric is **balanced accuracy over both case classes**, not the detection
rate, and that choice is the point. Detection rate alone is trivially gamed by
a detector that flags every draft; false-positive rate alone is gamed by one
that never flags anything — which is precisely the failure mode being measured
(1-of-4 detection looks identical to a quiet, healthy stage from production).
Reporting one number that can only be raised by getting BOTH right is what
makes the result trustworthy.

``detect_contradictions`` returns ``(review_or_None, stats)``, where ``None``
means "nothing to fix". That is the whole ground-truth signal: non-None on a
``contradiction`` case is a catch, non-None on a ``clean`` case is a false
positive.

Unusable cases (the model unreachable, the call raising) are counted and
reported separately rather than silently scored as PASS — scoring an error as
"no contradiction found" would credit a broken model with the behaviour of a
cautious one, and inflate exactly the number this exists to measure.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from services.model_eval.types import GoldenSet, MetricResult

logger = logging.getLogger(__name__)


class DetectFn(Protocol):
    """(draft, title, topic, model) -> review text or None."""

    async def __call__(
        self, *, draft: str, title: str, topic: str, model: str
    ) -> str | None: ...


class SelfReviewScorer:
    """Detection-rate scorer over the self-review golden set."""

    capability = "self_review"
    primary_metric = "self_review_balanced_accuracy"

    def __init__(self, *, pool: Any = None, detect_fn: DetectFn | None = None) -> None:
        self._pool = pool
        # Injectable for tests / offline runs — defaults to the real
        # production detect path, built lazily per ascore() call.
        self._detect_fn = detect_fn

    def _build_detect_fn(self, site_config: Any) -> DetectFn:
        if self._detect_fn is not None:
            return self._detect_fn

        from services.self_review import detect_contradictions

        async def _detect(*, draft: str, title: str, topic: str, model: str) -> str | None:
            # The model under test is applied by overriding the DETECT pin
            # only. Evaluating the detector must not perturb the reviser: the
            # two halves were split precisely because they want different
            # models (glad-labs-stack#3435).
            scoped = _ScopedSiteConfig(site_config, {"writer_self_review_review_model": model})
            review, _stats = await detect_contradictions(
                draft, title, topic, pool=self._pool, site_config=scoped,
            )
            return review

        return _detect

    async def ascore(
        self, *, model: str, golden_set: GoldenSet, site_config: Any
    ) -> MetricResult:
        detect = self._build_detect_fn(site_config)
        started = time.monotonic()

        clean_total = clean_passed = 0
        bad_total = bad_detected = 0
        unusable = 0

        for case in golden_set.cases:
            p = case.payload
            expected = p.get("expected")
            try:
                review = await detect(
                    draft=str(p.get("draft", "")),
                    title=str(p.get("title", "")),
                    topic=str(p.get("topic", "")),
                    model=model,
                )
                errored = False
            except Exception as exc:  # noqa: BLE001 — one bad case must not abort the run
                logger.warning(
                    "[model_eval.self_review] detect raised on kind=%s: %s",
                    p.get("kind"), exc,
                )
                review, errored = None, True

            if errored:
                unusable += 1
                # Counted in the denominator but never credited: an error is
                # not evidence the draft was clean.
                if expected == "pass":
                    clean_total += 1
                else:
                    bad_total += 1
                continue

            detected = review is not None
            if expected == "pass":
                clean_total += 1
                clean_passed += int(not detected)
            else:
                bad_total += 1
                bad_detected += int(detected)

        detection_rate = (bad_detected / bad_total) if bad_total else 0.0
        clean_pass_rate = (clean_passed / clean_total) if clean_total else 0.0
        balanced = (detection_rate + clean_pass_rate) / 2

        return MetricResult(
            slot="writer_self_review_review_model",
            model=model,
            metric_name=self.primary_metric,
            value=round(balanced, 4),
            n_cases=len(golden_set.cases),
            latency_ms=int((time.monotonic() - started) * 1000),
            detail={
                "detection_rate": round(detection_rate, 3),
                "false_positive_rate": round(1.0 - clean_pass_rate, 3),
                "detected": bad_detected,
                "contradiction_n": bad_total,
                "clean_n": clean_total,
                "unusable": unusable,
                "golden_version": golden_set.version,
            },
        )


class _ScopedSiteConfig:
    """``site_config`` with a few keys overridden, for the duration of one score.

    The eval must be able to point the detector at an arbitrary model without
    writing to ``app_settings`` — a bakeoff that mutated prod config would
    change the running pipeline's behaviour mid-run, and a crash would leave
    the override behind.
    """

    def __init__(self, base: Any, overrides: dict[str, Any]) -> None:
        self._base = base
        self._overrides = overrides

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return self._base.get(key, default)

    def __getattr__(self, name: str) -> Any:
        # get_bool / get_int / get_secret and anything else pass through
        # untouched — only `get` needs to see the overrides.
        return getattr(self._base, name)
