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
``(True, None, "deepeval-skipped")`` so the rail can never take down
the pipeline.

Fail-soft is not fail-silent (poindexter#876)
---------------------------------------------

Every skip path returns ``score=None`` — NOT MEASURED. ``passed=True`` is
retained (an advisory rail that cannot run must never veto) but there is no
number, because a number here is a fabricated measurement. All three rails
previously returned ``1.0`` on skip, which ``multi_model_qa._check_deepeval_*``
rescaled to a **100.0 review**: a rail that never ran was recorded as a perfect
pass. The brand metric makes the trap concrete — it is binary, so clean content
genuinely scores ``1.0``, and "clean" was therefore indistinguishable from
"deepeval isn't installed".

This is not hypothetical. #601: g_eval was ``OPENAI_API_KEY``-erroring on every
call and **scored 100 advisory on every run for ~7 days** before anyone noticed.
That fix addressed the cause (wire the judge to Ollama); this one removes the
mechanism, so the next cause — a renamed model, a dropped extra, a judge outage
— surfaces immediately instead.

Callers MUST branch on ``score is None`` and record NO review. Genuine failures
also emit a ``qa_rail_degraded`` finding (``_surface_deepeval_degraded``, the
kind ``ragas_eval`` established in poindexter#847); benign inapplicability
(empty content, no retrieval context) returns ``None`` without a finding.

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


def _build_dispatcher_judge_model(judge_model: str, *, pool: Any) -> Any:
    """Wrap the judge in a DeepEval model that routes through the dispatcher.

    Poindexter#826: DeepEval's stock ``OllamaModel`` owns its own HTTP
    transport, so judge calls bypassed ``dispatch_complete`` — no
    ``cost_logs`` rows, no Langfuse trace, and the judge silently ignored
    the per-model ``api_base`` overrides (GPU-pinned instances). This
    adapter re-establishes the seam at the layer DeepEval provides
    (``DeepEvalBaseLLM``): every judge call becomes a normal dispatcher
    call (``phase='qa_deepeval_judge'``).

    Async-only by design — the rails invoke metrics via ``a_measure`` on
    the worker's event loop (the asyncpg pool is loop-bound, so a sync
    ``measure()`` inside ``asyncio.to_thread`` could not reach the
    dispatcher). ``generate`` raises to make a sync regression loud.

    Returns ``None`` when deepeval isn't importable — callers fall back
    to the legacy model resolution.
    """
    try:
        from deepeval.models.base_model import DeepEvalBaseLLM
    except ImportError:
        return None

    import json as _json
    import re as _re

    from services.llm_providers.dispatcher import dispatch_complete

    class _DispatcherJudgeLLM(DeepEvalBaseLLM):
        def __init__(self):
            self._pool = pool
            super().__init__(judge_model)

        def load_model(self):
            return self

        def get_model_name(self) -> str:
            return f"dispatcher:{judge_model}"

        async def a_generate(self, prompt: str, schema: Any = None) -> Any:
            kwargs: dict[str, Any] = {}
            if schema is not None:
                # json_object keeps weaker local judges inside JSON mode
                # (LiteLLM maps it to Ollama's format=json) — same
                # constrained-decoding fix as the Ragas rail (#1910).
                kwargs["response_format"] = {"type": "json_object"}
            completion = await dispatch_complete(
                pool=self._pool,
                messages=[{"role": "user", "content": prompt}],
                model=judge_model,
                tier="standard",
                phase="qa_deepeval_judge",
                temperature=0.2,
                **kwargs,
            )
            text = (getattr(completion, "text", "") or "").strip()
            if schema is None:
                return text
            cleaned = text
            fence = _re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, _re.DOTALL)
            if fence:
                cleaned = fence.group(1)
            return schema.model_validate(_json.loads(cleaned))

        def generate(self, prompt: str, schema: Any = None) -> Any:
            raise RuntimeError(
                "_DispatcherJudgeLLM is async-only — run the metric via "
                "a_measure (poindexter#826 routes deepeval judges through "
                "the LiteLLM dispatcher on the event loop)"
            )

    return _DispatcherJudgeLLM()


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
        ``COMPANY_IMPOSSIBLE`` / ``BRAND_CONTRADICTION`` pattern
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
                (cv.COMPANY_IMPOSSIBLE, "company_claim"),
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


def evaluate_brand_fabrication(
    content: str, topic: str = "",
) -> tuple[bool, float | None, str]:
    """Run the brand-fabrication metric and return ``(passed, score, reason)``.

    Never raises — DeepEval errors are caught + surfaced as
    ``(True, None, "deepeval-skipped")`` so the rail can't take down
    the pipeline. Caller correlates the score with the legacy
    ``content_validator`` result via the audit log.

    ``score=None`` means NOT MEASURED (poindexter#876) — callers MUST branch on
    it rather than record the value. This metric is binary, so a *clean* post
    genuinely scores ``1.0``; when the skip paths also returned ``1.0``, "clean"
    and "never ran" were the same value, and the caller rescaled both to a
    ``100.0`` review. ``passed=True`` is retained so an advisory skip cannot
    veto.
    """
    if not content or not isinstance(content, str):
        return True, None, "empty content"

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
        _surface_deepeval_degraded(
            "deepeval-not-installed", str(e), rail="brand_fabrication",
        )
        return True, None, "deepeval-not-installed"
    except Exception as e:
        logger.warning("[deepeval] Unexpected error in brand metric: %s", e, exc_info=True)
        _surface_deepeval_degraded(
            f"deepeval-error: {type(e).__name__}", str(e), rail="brand_fabrication",
        )
        return True, None, f"deepeval-error: {type(e).__name__}"


def _surface_deepeval_degraded(reason: str, detail: str, *, rail: str) -> None:
    """Make a deepeval rail's inability to run VISIBLE (no silent skip).

    The ``evaluate_*`` rails must not raise (a rail can't take down the
    pipeline), so on a missing package or runtime error they return a passing
    verdict. As of poindexter#876 they return ``score=None`` with it, so the
    un-run check is no longer recorded as a perfect measurement — but "absent"
    still needs to be loud, or a permanently-dead rail just looks like a rail
    nobody reviews. Emit a typed finding so the degradation routes to Discord /
    the Findings board instead of hiding in the logs
    (feedback_no_silent_defaults).

    ``rail`` scopes the source + dedup key. This was brand-only; g_eval and
    faithfulness emitted NOTHING, which is how #601 hid for ~7 days (g_eval
    OPENAI_API_KEY-erroring on every call while reporting a perfect 100).

    Kind is ``qa_rail_degraded`` — the convention ``ragas_eval`` established
    (poindexter#847). Dedup key follows its ``qa_rail_degraded:<rail>:<detail>``
    shape so one chronic failure pages once rather than once per post; the
    findings_alert_router owns collapsing repeat fires.

    Call ONLY for genuine failures. A benign inapplicability (empty content, no
    retrieval context) also returns ``score=None`` but is not a finding — the
    rail correctly had nothing to measure, and paging on it is noise.
    """
    try:
        from utils.findings import emit_finding

        emit_finding(
            source=f"deepeval_rails.evaluate_{rail}",
            kind="qa_rail_degraded",
            severity="warn",
            title=f"DeepEval {rail} rail could not run",
            body=(
                f"The deepeval {rail} rail could not run ({reason}: {detail}). "
                "It recorded NO review for this post rather than a score — the "
                "rail is simply absent from the QA pass. It is advisory, so "
                "this does not block publish, but the signal is missing until "
                "deepeval is healthy. Investigate the deepeval install / "
                "runtime / judge model."
            ),
            dedup_key=f"qa_rail_degraded:deepeval_{rail}:{reason}",
            extra={"rail": f"deepeval_{rail}", "reason": reason},
        )
    except Exception:  # noqa: BLE001 — finding emission is best-effort
        logger.debug("[deepeval] emit_finding unavailable", exc_info=True)


_G_EVAL_CRITERION_KEY = "qa.deepeval_g_eval_criterion"

# Inline bootstrap fallback — must stay identical to the
# ``## qa.deepeval_g_eval_criterion`` body in skills/content/content-qa/SKILL.md
# AND to the seeded ``app_settings.deepeval_g_eval_criterion`` value, so all
# three sources grade against the same rubric. The resolver strips the SKILL.md
# loader's trailing newline, so this constant carries none; the shared drift
# guard in tests/unit/services/test_prompt_fallback_drift.py enforces agreement.
_DEFAULT_G_EVAL_CRITERION = (
    "The output is well-grounded in the input topic, internally "
    "consistent across paragraphs, and does not invent specific facts, "
    "names, statistics, or quotes that lack support."
)


def _resolve_g_eval_criterion() -> str:
    """Resolve the g-eval grounding rubric via UnifiedPromptManager
    (``qa.deepeval_g_eval_criterion``), inline fallback on any lookup
    failure per ``feedback_prompts_must_be_db_configurable``.

    Strips the loader's trailing newline: the criterion is a bare
    single-sentence rubric handed to DeepEval's GEval (and mirrored by
    the seeded ``deepeval_g_eval_criterion`` app_setting, which is also
    newline-free), not a rendered prompt body.
    """
    try:
        from services.prompt_manager import get_prompt_manager

        return get_prompt_manager().get_prompt(_G_EVAL_CRITERION_KEY).strip()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[deepeval] prompt_manager lookup for %r failed (%s) — "
            "using inline fallback",
            _G_EVAL_CRITERION_KEY, exc,
        )
        return _DEFAULT_G_EVAL_CRITERION


async def _resolve_judge_model(site_config: Any) -> str:
    """Pick the LLM judge model for the LLM-graded DeepEval metrics.

    Reads ``app_settings.deepeval_judge_model`` directly and fails loud
    (``notify_operator(critical=True)`` + raise) when unset, per
    ``feedback_no_silent_defaults`` — an empty pin is a configuration bug
    that should page ops, not be absorbed by the rail's advisory fail-soft
    contract. The ``cost_tier.budget`` tier and the cross-step
    ``pipeline_writer_model`` fallback were removed in favour of the
    per-rail pin.

    2026-05-12 cleanup (issue #487): the hardcoded ``glm-4.7-5090``
    fallback that used to live here baked Matt's specific custom model
    name into a public OSS file — forks installing Poindexter wouldn't have
    that model and would get a confusing "model not found" error from
    DeepEval at run time. Operators who want a judge set
    ``deepeval_judge_model`` explicitly.

    Raises:
        ValueError when ``deepeval_judge_model`` is unset — fail loud so the
        operator notices a broken install before the QA rail silently
        approves everything.
    """
    if site_config is None:
        raise ValueError(
            "deepeval._resolve_judge_model: site_config is required to "
            "resolve the judge model (no hardcoded fallback by design)"
        )

    explicit = (site_config.get("deepeval_judge_model", "") or "").strip()
    if explicit:
        return explicit

    # deepeval_judge_model empty — page ops. Mirrors ragas_eval's
    # critical-notify contract so a misconfigured judge model surfaces on
    # Telegram / Discord instead of just a WARN log in the rail's "skip"
    # branch. The cost_tier.* and cross-step pipeline_writer_model fallbacks
    # were removed.
    try:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(
            "deepeval_rails: deepeval_judge_model is empty — set it "
            "(the cost_tier.* fallback was removed)",
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
        "set ``deepeval_judge_model``."
    )


async def evaluate_g_eval(
    content: str,
    topic: str = "",
    *,
    criterion: str | None = None,
    judge_model: str = "",
    threshold: float = 0.7,
    site_config: Any | None = None,
    pool: Any = None,
) -> tuple[bool, float | None, str]:
    """Run DeepEval's G-Eval (LLM-judge) against ``content``.

    G-Eval is a chain-of-thought LLM-judge metric: the judge model
    decides on its own evaluation steps from the criterion, scores
    the output along those steps, and emits a 0.0–1.0 grade. It's
    the closest DeepEval analogue to our existing critic gate, so
    we treat it as advisory rather than a hard veto.

    An explicit ``criterion`` (e.g. the ``deepeval_g_eval_criterion``
    app_settings override read by ``multi_model_qa``) wins; when
    None/empty, the rubric resolves from the SKILL.md catalog via
    :func:`_resolve_g_eval_criterion`.

    Async as of poindexter#826: the metric runs via ``a_measure`` so the
    judge calls go through the LiteLLM dispatcher on the event loop when
    a ``pool`` is wired (production — cost_logs + Langfuse + api_base
    overrides). Without a pool it falls back to the legacy direct
    ``OllamaModel`` wrap (tests / bootstrap), the same
    dispatcher-or-direct shape as ``llm_text`` / ``topic_ranking``.

    Returns ``(passed, score, reason)`` — ``passed = score >= threshold``.
    Never raises: import failures or judge errors return ``score=None``
    (NOT MEASURED — poindexter#876; callers branch on it and record no review)
    with ``passed=True`` so an advisory skip still cannot veto.
    """
    if not content or not isinstance(content, str):
        return True, None, "empty content"

    try:
        from deepeval.metrics import GEval as _GEvalMetric
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
    except ImportError as e:
        logger.warning("[deepeval] deepeval not installed (%s) — skipping g-eval", e)
        _surface_deepeval_degraded("deepeval-not-installed", str(e), rail="g_eval")
        return True, None, "deepeval-not-installed"

    if not criterion:
        criterion = _resolve_g_eval_criterion()

    # Dispatcher-backed judge when a pool is reachable (poindexter#826);
    # else wrap ``ollama/...`` judge-model strings in a real OllamaModel so
    # DeepEval doesn't fall back to its OpenAI client and raise
    # ``DeepEvalError: OPENAI_API_KEY is not configured`` (the 2026-05-27
    # audit's "g_eval always errors" finding).
    resolved_model = None
    if pool is not None:
        resolved_model = _build_dispatcher_judge_model(judge_model, pool=pool)
    if resolved_model is None:
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
        score = float(await metric.a_measure(case))
        reason = (getattr(metric, "reason", None) or "")[:300]
        passed = bool(getattr(metric, "success", score >= threshold))
        return passed, score, reason
    except Exception as e:
        logger.warning("[deepeval] g-eval error: %s", e, exc_info=True)
        _surface_deepeval_degraded(
            f"deepeval-error: {type(e).__name__}", str(e), rail="g_eval",
        )
        return True, None, f"deepeval-error: {type(e).__name__}"


async def evaluate_faithfulness(
    content: str,
    retrieval_context: list[str] | None,
    *,
    judge_model: str = "",
    threshold: float = 0.8,
    site_config: Any | None = None,
    pool: Any = None,
) -> tuple[bool, float | None, str]:
    """Run DeepEval's ``FaithfulnessMetric`` on ``content``.

    Every claim in the output must be attributable to one of the
    strings in ``retrieval_context`` (typically: research bundle
    snippets seeded earlier in the pipeline). Score is the fraction
    of claims that are faithful to the context — 1.0 means every
    claim is grounded; lower scores flag potential fabrications.

    Async as of poindexter#826 — same dispatcher-or-direct judge shape
    as ``evaluate_g_eval`` (dispatcher via ``a_measure`` when ``pool``
    is wired; legacy ``OllamaModel`` otherwise).

    Returns ``(passed, score, reason)``. Returns
    ``(True, None, "no-context")`` when ``retrieval_context`` is empty
    or None — the metric can't run without grounding text, so we
    skip rather than fail (the brand-fabrication rail catches the
    fabrication patterns at a different layer). ``score=None`` means NOT
    MEASURED (poindexter#876); callers branch on it and record no review
    instead of a fabricated perfect score.

    ``no-context`` / ``empty content`` are benign INAPPLICABILITY — no finding
    is emitted, because the rail correctly had nothing to measure. A genuine
    failure (missing package, judge error) does emit one.
    """
    if not content or not isinstance(content, str):
        return True, None, "empty content"
    if not retrieval_context:
        return True, None, "no-context"

    try:
        from deepeval.metrics import FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
    except ImportError as e:
        logger.warning(
            "[deepeval] deepeval not installed (%s) — skipping faithfulness", e,
        )
        _surface_deepeval_degraded(
            "deepeval-not-installed", str(e), rail="faithfulness",
        )
        return True, None, "deepeval-not-installed"

    resolved_model = None
    if pool is not None:
        resolved_model = _build_dispatcher_judge_model(judge_model, pool=pool)
    if resolved_model is None:
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
        score = float(await metric.a_measure(case))
        reason = (getattr(metric, "reason", None) or "")[:300]
        passed = bool(getattr(metric, "success", score >= threshold))
        return passed, score, reason
    except Exception as e:
        logger.warning("[deepeval] faithfulness error: %s", e, exc_info=True)
        _surface_deepeval_degraded(
            f"deepeval-error: {type(e).__name__}", str(e), rail="faithfulness",
        )
        return True, None, f"deepeval-error: {type(e).__name__}"


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
