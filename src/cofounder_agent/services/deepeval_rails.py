"""DeepEval integration as a parallel content reviewer (#197).

Doesn't replace the multi-reviewer orchestration in
``services/multi_model_qa.py`` — that's our domain logic with vision
checks, web fact-check, reviewer-pool degradation handling. DeepEval
is added as a *parallel* signal:

1. Operator gets hands-on with the framework conventions
   (LLMTestCase, BaseMetric, G-Eval, evaluation runs) which show
   up across modern LLM-app stacks.
2. The DeepEval pre-built metrics (FaithfulnessMetric,
   HallucinationMetric, AnswerRelevancyMetric, ToxicityMetric, etc)
   become available for one-off batch evals against published-post
   archives or A/B test runs without rebuilding the harness.

Activation
----------

``app_settings.deepeval_enabled = true`` runs the DeepEval rail
alongside the existing multi_model_qa pass. Default ``false``.

Custom-metric pattern
---------------------

The example below registers a single ``BrandFabricationMetric``
that wraps the existing fabrication patterns. The shape — subclass
``BaseMetric``, implement ``measure(test_case)``, return a
score in [0, 1] — is the canonical "wrap an existing domain check
as a DeepEval metric" example for the Glad Labs codebase. Future
custom metrics (citation grounding, brand-voice adherence) follow
the same shape.
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom metric wrapping our existing fabrication patterns
# ---------------------------------------------------------------------------


def _build_brand_fabrication_metric():
    """Lazy-build the metric class. Imported lazily so callers without
    deepeval installed don't crash on module import."""
    from deepeval.metrics import BaseMetric
    from deepeval.test_case import LLMTestCase

    from services import content_validator as cv

    class BrandFabricationMetric(BaseMetric):
        """DeepEval metric: 1.0 = clean, 0.0 = fabrication detected.

        Wraps the curated ``FAKE_*`` / ``HALLUCINATED_*`` /
        ``GLAD_LABS_IMPOSSIBLE`` / ``BRAND_CONTRADICTION`` pattern
        sets in content_validator. Score is binary (1.0 or 0.0)
        because brand fabrication is pass/fail, not graded —
        either a fake quote is in the text or it isn't.
        """

        def __init__(self, threshold: float = 0.5):
            self.threshold = threshold
            self.score = 0.0
            self.success = False
            self.reason: str | None = None

        @property
        def __name__(self):
            return "BrandFabrication"

        def measure(self, test_case: LLMTestCase) -> float:
            text = test_case.actual_output or ""
            issues: list[str] = []

            for pattern_set, label in [
                (cv.FAKE_NAME_PATTERNS, "fake_person"),
                (cv.FAKE_STAT_PATTERNS, "fake_stat"),
                (cv.GLAD_LABS_IMPOSSIBLE, "glad_labs_claim"),
                (cv.FAKE_QUOTE_PATTERNS, "fake_quote"),
                (cv.FABRICATED_EXPERIENCE_PATTERNS, "fabricated_experience"),
                (cv.HALLUCINATED_LINK_PATTERNS, "hallucinated_link"),
                (cv.BRAND_CONTRADICTION_PATTERNS, "brand_contradiction"),
            ]:
                hits = cv._check_patterns(
                    text, pattern_set, "critical", label, label + ": '{matched}'",
                )
                for issue in hits:
                    issues.append(f"{issue.category}: {issue.description[:80]}")

            if issues:
                self.score = 0.0
                self.reason = (
                    f"{len(issues)} fabrication(s) detected: "
                    + "; ".join(issues[:3])
                    + ("" if len(issues) <= 3 else f" (+{len(issues)-3} more)")
                )
            else:
                self.score = 1.0
                self.reason = "No fabrication patterns matched"

            self.success = self.score >= self.threshold
            return self.score

        async def a_measure(self, test_case: LLMTestCase) -> float:
            # DeepEval's async path. Our patterns are pure-CPU so we
            # delegate to the sync version directly.
            return self.measure(test_case)

        def is_successful(self) -> bool:
            return self.success

    return BrandFabricationMetric


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def make_test_case(
    *, content: str, topic: str, expected: str | None = None
) -> Any:
    """Convenience: build a DeepEval LLMTestCase with our conventions.

    The DeepEval ``input`` field maps to the topic prompt, ``actual_output``
    to the generated content, ``expected_output`` to the ideal/baseline
    reference (None for open-ended generation).
    """
    from deepeval.test_case import LLMTestCase
    return LLMTestCase(
        input=topic,
        actual_output=content,
        expected_output=expected,
    )


def evaluate_brand_fabrication(content: str, topic: str = "") -> tuple[bool, float, str]:
    """Run the brand-fabrication metric and return ``(passed, score, reason)``.

    Never raises — DeepEval errors are caught + surfaced as
    ``(True, 1.0, "deepeval-skipped")`` so the rail can't take down
    the pipeline. Caller correlates the score with the legacy
    ``content_validator`` result via the audit log.
    """
    if not content or not isinstance(content, str):
        return True, 1.0, "empty content"

    try:
        metric_cls = _build_brand_fabrication_metric()
        metric = metric_cls(threshold=0.5)
        case = make_test_case(content=content, topic=topic)
        score = metric.measure(case)
        return metric.is_successful(), score, metric.reason or ""
    except ImportError as e:
        logger.warning(
            "[deepeval] deepeval not installed (%s) — skipping rail", e,
        )
        return True, 1.0, "deepeval-not-installed"
    except Exception as e:
        logger.warning("[deepeval] Unexpected error in brand metric: %s", e, exc_info=True)
        return True, 1.0, f"deepeval-error: {type(e).__name__}"


def is_enabled(site_config: Any) -> bool:
    """Operator gate. ``app_settings.deepeval_enabled = true`` to run."""
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("deepeval_enabled", False))
    except Exception:
        try:
            v = site_config.get("deepeval_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception:
            return False


__all__ = [
    "evaluate_brand_fabrication",
    "is_enabled",
    "make_test_case",
]
