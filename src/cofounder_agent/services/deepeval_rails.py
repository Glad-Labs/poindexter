"""DeepEval integration as a parallel content reviewer (#197 / #329).

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

Three rails ship today (Lane D, sub-issue 1 of #329):

- ``evaluate_brand_fabrication`` — pure-CPU regex wrapper around
  ``content_validator``'s fabrication pattern sets. Binary score.
- ``evaluate_g_eval`` — LLM-judge that grades content against an
  operator-defined criterion (default: "the post is well-grounded,
  internally consistent, and does not invent facts"). Graded 0–1.
- ``evaluate_faithfulness`` — DeepEval's built-in
  ``FaithfulnessMetric``: every claim in the content must be
  attributable to the supplied retrieval context. Graded 0–1.

The two LLM-judge rails respect ``app_settings.deepeval_judge_model``
and route through OpenAI-compatible providers via DeepEval's standard
configuration. They share the same fail-soft contract as the brand
metric — ``ImportError`` / runtime failure returns
``(True, 1.0, "deepeval-skipped")`` so the rail can never take down
the pipeline.

Custom-metric pattern
---------------------

The ``BrandFabricationMetric`` below shows the canonical shape:
subclass ``BaseMetric``, implement ``measure(test_case)``, return a
score in [0, 1]. Future custom metrics (citation grounding,
brand-voice adherence) follow the same template.
"""

from __future__ import annotations

import os
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# DeepEval's telemetry writes to ``.deepeval/.deepeval_telemetry.txt`` in
# the process cwd. In the worker container the cwd is the read-only app
# root, so every metric measure raises ``OSError: Read-only file system``.
# Opt out by default — the metrics carry no operator-visible benefit
# (anonymous usage data sent to deepeval.com) and the resulting OSError
# was the surface symptom that swallowed every g_eval call.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")


# Marker prefixes for app_settings-resolved judge-model strings. Anything
# starting with one of these maps to the corresponding DeepEval model
# class; bare strings without a prefix are treated as OpenAI model names
# (matches DeepEval's default ``initialize_model`` behaviour).
_OLLAMA_MODEL_PREFIXES = ("ollama/", "ollama:")


def _resolve_local_llm_base_url(site_config: Any | None) -> str:
    """Return the Ollama base URL configured in app_settings, or the
    Docker-internal default that works in every container in the stack.

    Reads ``ollama_base_url`` (the canonical setting consumed by every
    other Ollama caller in the codebase) — falling back to the
    ``host.docker.internal`` default keeps tests + bootstrap working
    without a SiteConfig in scope.
    """
    if site_config is None:
        return "http://host.docker.internal:11434"
    try:
        return (
            site_config.get(
                "ollama_base_url", "http://host.docker.internal:11434",
            )
            or "http://host.docker.internal:11434"
        )
    except Exception:  # noqa: BLE001 — defensive against test stubs
        return "http://host.docker.internal:11434"


def _build_deepeval_judge_model(
    judge_model: str, *, site_config: Any | None = None,
) -> Any:
    """Translate an app_settings judge-model string into a DeepEval model.

    Per ``feedback_no_paid_apis`` the default policy is local-only, and
    DeepEval's stock string-based model resolver assumes OpenAI — passing
    ``"gemma3:27b"`` as a string crashes inside
    ``deepeval.metrics.utils.initialize_model`` with
    ``OPENAI_API_KEY is not configured``. The fix: detect the ``ollama/``
    prefix Matt's app_settings ship by convention and wrap the model in
    DeepEval's ``OllamaModel`` so the metric talks to the local
    Ollama server.

    Returns either an ``OllamaModel`` instance or the bare string
    (for stock OpenAI-compatible behaviour) so callers can pass the
    return value directly into ``GEval(model=...)`` etc.

    Detected 2026-05-27: the `audit_log` rows for every published post
    showed ``[advisory] deepeval-error: DeepEvalError`` from
    ``deepeval_g_eval`` — the rail was scoring 100 advisory on every
    article because the OpenAI-key check raised before the LLM judge
    could run, and the rail's defensive ``except Exception`` returned
    ``(True, 1.0, "deepeval-error: DeepEvalError")``.
    """
    if not judge_model:
        return judge_model

    lowered = judge_model.lower()
    if lowered.startswith(_OLLAMA_MODEL_PREFIXES):
        try:
            from deepeval.models import OllamaModel
        except ImportError:
            logger.warning(
                "[deepeval] OllamaModel unavailable (deepeval too old?) — "
                "falling back to bare string %r; expect OPENAI_API_KEY "
                "error from the judge metric",
                judge_model,
            )
            return judge_model

        model_name = judge_model
        for prefix in _OLLAMA_MODEL_PREFIXES:
            if lowered.startswith(prefix):
                model_name = judge_model[len(prefix):]
                break

        return OllamaModel(
            model=model_name,
            base_url=_resolve_local_llm_base_url(site_config),
            temperature=0.2,
        )

    return judge_model


# ---------------------------------------------------------------------------
# Custom metric wrapping our existing fabrication patterns
# ---------------------------------------------------------------------------


def _build_brand_fabrication_metric():
    """Lazy-build the metric class. Imported lazily so callers without
    deepeval installed don't crash on module import."""
    from deepeval.metrics import BaseMetric
    from deepeval.test_case import LLMTestCase

    from modules.content.api import content_validator as cv

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
            return bool(self.success)

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


_DEFAULT_G_EVAL_CRITERION = (
    "The output is well-grounded in the input topic, internally "
    "consistent across paragraphs, and does not invent specific facts, "
    "names, statistics, or quotes that lack support."
)


async def _resolve_judge_model(site_config: Any) -> str:
    """Pick the LLM judge model for the LLM-graded DeepEval metrics.

    Resolution order:

    1. ``app_settings.deepeval_judge_model`` — explicit per-rail override.
    2. ``resolve_tier_model(pool, 'standard')`` — the same tier the
       reviewers use, routed through the dispatcher API so model-name
       normalisation stays consistent across all reviewer rails. Falls
       through to a direct ``cost_tier.standard.model`` read when no
       pool is available (tests, bootstrap).
    3. ``app_settings.pipeline_writer_model`` — last-ditch fallback;
       the writer model exists in every install by construction.
    4. ``notify_operator(critical=True)`` + raise — per
       ``feedback_no_silent_defaults``, every resolution path being
       empty is a configuration bug that should page ops, not be
       absorbed by the rail's advisory fail-soft contract.

    2026-05-12 cleanup (issue #487): the hardcoded ``glm-4.7-5090``
    fallback that used to live here baked Matt's specific custom
    model name into a public OSS file — forks installing Poindexter
    wouldn't have that model and would get a confusing "model not
    found" error from DeepEval at run time. The cost-tier path is
    operator-managed and works everywhere.

    2026-05-23 (Glad-Labs/poindexter#455 Phase 1): aligned to the
    ``ragas_eval._resolve_judge_model`` shape — dispatcher cost-tier
    API + loud notify on total resolution failure. Function is now
    async so it can await the tier resolution + notify side-effect.

    Raises:
        ValueError when every resolution path is unset — fail loud so
        the operator notices a broken install before the QA rail
        silently approves everything.
    """
    if site_config is None:
        raise ValueError(
            "deepeval._resolve_judge_model: site_config is required to "
            "resolve the judge model (no hardcoded fallback by design)"
        )

    try:
        explicit = (site_config.get("deepeval_judge_model", "") or "").strip()
        if explicit:
            return explicit
    except Exception as e:  # noqa: BLE001 — defensive against test stubs
        logger.warning(
            "[deepeval] site_config.get('deepeval_judge_model') raised %s — "
            "falling through to cost-tier resolution",
            e,
        )

    pool = getattr(site_config, "_pool", None)
    tier_exc: Exception | None = None
    if pool is not None:
        from services.llm_providers.dispatcher import resolve_tier_model
        try:
            tier_model = (await resolve_tier_model(pool, "standard")).strip()
            if tier_model:
                # 2026-05-27: keep the ``ollama/`` prefix so
                # ``_build_deepeval_judge_model`` can route the metric
                # through ``OllamaModel`` instead of falling back to
                # OpenAI (the cause of every ``deepeval-error:
                # DeepEvalError`` audit_log row).
                return tier_model
        except (RuntimeError, ValueError, AttributeError) as exc:
            tier_exc = exc
            logger.debug(
                "[deepeval] resolve_tier_model('standard') failed (%s); "
                "trying direct setting + writer model", exc,
            )
    if tier_exc is None:
        try:
            tier = (
                site_config.get("cost_tier.standard.model", "") or ""
            ).strip()
            if tier:
                return tier
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[deepeval] cost-tier lookup failed (%s); trying writer model", e,
            )

    try:
        writer = (
            site_config.get("pipeline_writer_model", "") or ""
        ).strip()
        if writer:
            return writer
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[deepeval] writer-model lookup failed (%s) — no judge model "
            "can be resolved", e,
        )

    # Every path empty — page ops. Mirrors ragas_eval's critical-notify
    # contract so a misconfigured judge model surfaces on Telegram /
    # Discord instead of just a WARN log in the rail's "skip" branch.
    try:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(
            f"deepeval_rails: judge model unresolvable — set "
            f"``deepeval_judge_model`` OR ``cost_tier.standard.model`` "
            f"OR ``pipeline_writer_model``. tier_exc={tier_exc!r}",
            critical=True,
            site_config=site_config,
        )
    except Exception as notify_exc:
        # poindexter#455 — used to be silent. The notify path failing
        # means the operator wouldn't hear the critical "deepeval can't
        # find a judge model" alert AND wouldn't see why the notify
        # failed. Log it so the alternate stderr / log channels at
        # least carry the picture.
        logger.warning(
            "[deepeval] notify_operator failed while reporting missing "
            "judge model — operator will only see the alert in logs: "
            "%s: %s",
            type(notify_exc).__name__, notify_exc,
        )
    raise ValueError(
        "deepeval_rails: no judge model resolvable from app_settings — "
        "set ``deepeval_judge_model`` OR ``cost_tier.standard.model`` "
        "OR ``pipeline_writer_model``."
    )


def evaluate_g_eval(
    content: str,
    topic: str = "",
    *,
    criterion: str = _DEFAULT_G_EVAL_CRITERION,
    judge_model: str = "",
    threshold: float = 0.7,
    site_config: Any | None = None,
) -> tuple[bool, float, str]:
    """Run DeepEval's G-Eval (LLM-judge) against ``content``.

    G-Eval is a chain-of-thought LLM-judge metric: the judge model
    decides on its own evaluation steps from the criterion, scores
    the output along those steps, and emits a 0.0–1.0 grade. It's
    the closest DeepEval analogue to our existing critic gate, so
    we treat it as advisory rather than a hard veto.

    Returns ``(passed, score, reason)`` — ``passed = score >= threshold``.
    Never raises: import failures or judge errors return safe defaults.
    """
    if not content or not isinstance(content, str):
        return True, 1.0, "empty content"

    try:
        from deepeval.metrics import GEval as _GEvalMetric
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
    except ImportError as e:
        logger.warning("[deepeval] deepeval not installed (%s) — skipping g-eval", e)
        return True, 1.0, "deepeval-not-installed"

    # Wrap ``ollama/...`` judge-model strings in a real OllamaModel so
    # DeepEval doesn't fall back to its OpenAI client and raise
    # ``DeepEvalError: OPENAI_API_KEY is not configured`` (the 2026-05-27
    # audit's "g_eval always errors" finding).
    resolved_model = _build_deepeval_judge_model(judge_model, site_config=site_config)

    try:
        metric = _GEvalMetric(
            name="ContentGroundedness",
            criteria=criterion,
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
            ],
            model=resolved_model,
            threshold=threshold,
        )
        case = LLMTestCase(input=topic or "blog post", actual_output=content)
        score = float(metric.measure(case))
        reason = (getattr(metric, "reason", None) or "")[:300]
        passed = bool(getattr(metric, "success", score >= threshold))
        return passed, score, reason
    except Exception as e:
        logger.warning("[deepeval] g-eval error: %s", e, exc_info=True)
        return True, 1.0, f"deepeval-error: {type(e).__name__}"


def evaluate_faithfulness(
    content: str,
    retrieval_context: list[str] | None,
    *,
    judge_model: str = "",
    threshold: float = 0.8,
    site_config: Any | None = None,
) -> tuple[bool, float, str]:
    """Run DeepEval's ``FaithfulnessMetric`` on ``content``.

    Every claim in the output must be attributable to one of the
    strings in ``retrieval_context`` (typically: research bundle
    snippets seeded earlier in the pipeline). Score is the fraction
    of claims that are faithful to the context — 1.0 means every
    claim is grounded; lower scores flag potential fabrications.

    Returns ``(passed, score, reason)``. Returns
    ``(True, 1.0, "no-context")`` when ``retrieval_context`` is empty
    or None — the metric can't run without grounding text, so we
    skip rather than fail (the brand-fabrication rail catches the
    fabrication patterns at a different layer).
    """
    if not content or not isinstance(content, str):
        return True, 1.0, "empty content"
    if not retrieval_context:
        return True, 1.0, "no-context"

    try:
        from deepeval.metrics import FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
    except ImportError as e:
        logger.warning(
            "[deepeval] deepeval not installed (%s) — skipping faithfulness", e,
        )
        return True, 1.0, "deepeval-not-installed"

    resolved_model = _build_deepeval_judge_model(judge_model, site_config=site_config)
    try:
        metric = FaithfulnessMetric(
            threshold=threshold,
            model=resolved_model,
            include_reason=True,
        )
        case = LLMTestCase(
            input="",
            actual_output=content,
            retrieval_context=list(retrieval_context),
        )
        score = float(metric.measure(case))
        reason = (getattr(metric, "reason", None) or "")[:300]
        passed = bool(getattr(metric, "success", score >= threshold))
        return passed, score, reason
    except Exception as e:
        logger.warning("[deepeval] faithfulness error: %s", e, exc_info=True)
        return True, 1.0, f"deepeval-error: {type(e).__name__}"


def is_enabled(site_config: Any) -> bool:
    """Operator gate. ``app_settings.deepeval_enabled = true`` to run."""
    if site_config is None:
        return False
    try:
        return bool(site_config.get_bool("deepeval_enabled", False))
    except Exception as exc_primary:
        try:
            v = site_config.get("deepeval_enabled", "")
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        except Exception as exc_fallback:
            # poindexter#455 — symmetric to guardrails_rails.is_enabled.
            # Both fallback paths silently returned False, masking broken
            # SiteConfig wrappers as "rail turned off". Now logs both
            # exception types so the operator can see why deepeval went
            # dark.
            logger.warning(
                "[deepeval] is_enabled: both get_bool and get raised while "
                "reading deepeval_enabled — treating as disabled. "
                "Primary: %s: %s. Fallback: %s: %s",
                type(exc_primary).__name__, exc_primary,
                type(exc_fallback).__name__, exc_fallback,
            )
            return False


__all__ = [
    "evaluate_brand_fabrication",
    "evaluate_faithfulness",
    "evaluate_g_eval",
    "is_enabled",
    "make_test_case",
]
