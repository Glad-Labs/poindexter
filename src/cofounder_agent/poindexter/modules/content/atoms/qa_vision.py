"""qa.vision — the vision/preview QA gate as one composable rail atom.

Restores the two vision-model gates that stopped running on the live path
when the #355 atom-cutover replaced ``MultiModelQA.review()`` with the
``qa.*`` atom chain. ``review()`` ran two vision checks inline (sections 2d
and 2h) — the cutover ported the text rails (``qa.critic`` / ``qa.deepeval``
/ ``qa.ragas``) and the programmatic net (``qa.programmatic``)
but NOT the vision legs, so both went cold (Glad-Labs/poindexter#563):

1. **Image relevance** (``_check_image_relevance`` → reviewer ``image_relevance``,
   aliased to the ``vision_gate`` qa_gates row). Checks the featured/hero image
   (``state['featured_image_url']``) plus the inline body images actually
   match the content. Opt-in via ``qa_vision_check_enabled``; needs no preview.
2. **Rendered-preview screenshot** (``_check_rendered_preview_outcome`` →
   reviewer ``rendered_preview``). Renders THIS draft as the operator's preview
   page (``services.preview_page``, the renderer ``GET /preview/{token}``
   serves) and feeds a screenshot to a vision model to catch layout breaks,
   missing CSS, overflowing tables, broken images. Opt-in via
   ``qa_preview_screenshot_enabled``.

   The leg renders in-process and never fetches the page. This rail runs
   before ``content.persist_task`` writes the draft or its ``preview_token``,
   so the served URL answers "Post not found" here. From 2026-07-08 to 07-18,
   when the URL was reachable, 33 of 34 reviews scored that 404 page. After
   that, the URL (``preview_base_url``, the operator's tailnet link) was
   unreachable from the container and the leg returned a silent ``None`` on
   every run. A leg that produces no verdict now emits the shared
   ``qa_rail_degraded`` finding with ``rail="rendered_preview"`` (see
   ``docs/architecture/preview-links.md``).

This atom mirrors ``qa.programmatic`` (which restored the dropped
``programmatic_validator`` gate the same way) and ``qa.ragas`` (which reads a
soft ``research_context`` input). The two vision reviews are appended to the
``qa_rail_reviews`` channel; both carry ``provider='vision_gate'`` so
``_qa_rail_common`` weights them at ``gate_weight`` and a non-advisory failing
review vetoes in ``qa.aggregate``.

Always emits a review (``feedback_no_silent_defaults``; #563): when neither leg
produces one, the atom returns a DELIBERATE, advisory, non-vetoing pass via
``_emit_deliberate_pass`` rather than a silent ``{}``. That empty return is
exactly how a *required* ``vision_gate`` fails 100% of posts closed — the
qa.aggregate vacuous-pass guard reads "no review" as "required rail absent".
The deliberate pass distinguishes "nothing to assess" (no inline images → pass
by vacuity, no page) from "couldn't assess" (images present but the vision
model was unreachable → fail open + page the operator, per operator policy).
"""

from __future__ import annotations

from typing import Any

from poindexter.modules.content.atoms._pool import resolve_pool
from poindexter.modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from poindexter.plugins.atom import AtomMeta, FieldSpec
from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

ATOM_META = AtomMeta(
    name="qa.vision",
    type="atom",
    version="1.1.0",
    description=(
        "Vision/preview QA rail — image-relevance + rendered-preview "
        "screenshot checks via a vision model; advisory is DB-driven via "
        "qa_gates.vision_gate.required_to_pass. The preview leg renders the "
        "in-flight draft in-process."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(
            name="preview_url",
            type="str",
            description=(
                "unused: the preview leg renders the draft in-process because "
                "the served /preview/{token} page does not exist yet at QA "
                "time. Kept declared so stored graph_defs keep their contract."
            ),
            required=False,
        ),
    ),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="vision reviews"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=(
        "calls a vision-capable ollama model; renders the draft's preview page "
        "and screenshots it via chromium",
    ),
    parallelizable=True,
)


async def _preview_screenshot_enabled(settings_service: Any) -> bool:
    """Read qa_preview_screenshot_enabled (default false): whether the
    rendered-preview leg runs at all."""
    if settings_service is None:
        return False
    try:
        raw = await settings_service.get("qa_preview_screenshot_enabled")
    except Exception:  # noqa: BLE001 - silent-ok: False matches this
        # setting's own default. A failed read skips the leg for this run,
        # exactly like the unset case; the image leg still runs.
        return False
    return str(raw or "false").strip().lower() in ("true", "1", "yes")


def _render_draft_preview(state: dict[str, Any], content: str) -> str:
    """The operator's preview page for the draft in ``state``, as HTML.

    Built from the same fields ``GET /api/posts/preview/{token}`` returns for a
    task (``COALESCE(title, topic)``, the stored content, excerpt, hero image,
    quality score), through the same renderer, so the screenshot is the page
    the operator will open once the draft is persisted.
    """
    from poindexter.services.preview_page import draft_preview_post, render_preview_page

    return render_preview_page(
        draft_preview_post(
            title=str(state.get("title") or state.get("topic") or ""),
            content_markdown=content,
            excerpt=str(state.get("excerpt") or ""),
            featured_image_url=str(state.get("featured_image_url") or ""),
            quality_score=state.get("quality_score"),
        )
    )


def _report_preview_failure(state: dict[str, Any], detail: str) -> None:
    """Make a rendered-preview leg that produced no verdict VISIBLE.

    Before this, every failure mode (dead URL, 404 page, capture error, empty
    model answer) returned the same silent ``None`` as "switched off", and the
    leg recorded 0 reviews in 53 runs over 30 days with nobody told. The draft
    still proceeds on the other rails. Same convention as the other rails that
    cannot measure (``qa.web_factcheck``, ``qa.title_coherence``): no review of
    its own (``_qa_rail_common.not_applicable_review`` is for rails that ran
    and found nothing to judge), plus the shared ``qa_rail_degraded`` finding
    with ``rail="rendered_preview"``.
    """
    task_id = str(state.get("task_id") or "")
    short = task_id[:8] or "?"
    logger.warning(
        "[qa.vision] rendered-preview leg produced no verdict for task %s: %s",
        short, detail,
    )
    try:
        from poindexter.utils.findings import emit_finding

        emit_finding(
            source="qa.vision",
            kind="qa_rail_degraded",
            title="rendered_preview rail could not run",
            body=(
                "qa_preview_screenshot_enabled is on, but the rendered-preview "
                f"check gave no verdict for task {short}: {detail}\n\n"
                "No rendered_preview review was appended, so nothing looked at "
                "how the draft renders; every other rail still judged it. The "
                "leg screenshots the draft rendered in-process "
                "(services.preview_page), so the cause is in chromium, the "
                "vision model (qa_preview_vision_model) or its answer, never "
                "in a URL."
            ),
            severity="warn",
            dedup_key="qa_rail_degraded:rendered_preview",
            extra={"rail": "rendered_preview", "reason": detail[:500], "task_id": task_id},
        )
    except Exception as exc:  # noqa: BLE001 — finding emission must not gate QA
        logger.warning("[qa.vision] finding emission failed: %s", exc)


async def _run_rendered_preview_leg(
    qa: Any,
    state: dict[str, Any],
    *,
    content: str,
    title: str,
    topic: str,
    gate_states: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run the rendered-preview leg. Returns ``(review_dict, failure_detail)``.

    ``(review, None)`` when the model gave a verdict, ``(None, detail)`` when
    the leg is on but produced none (already reported), and ``(None, None)``
    when the leg is off.
    """
    from poindexter.modules.content.multi_model_qa import MultiModelQA

    if not await _preview_screenshot_enabled(state.get("settings_service")):
        return None, None

    try:
        html = _render_draft_preview(state, content)
    except Exception as exc:  # noqa: BLE001 — reported below, never swallowed
        from poindexter.utils.exception_format import describe_exception

        detail = f"could not render the draft's preview page: {describe_exception(exc)}"
        _report_preview_failure(state, detail)
        return None, detail

    try:
        review, status, detail = await qa._check_rendered_preview_outcome(
            title, topic, preview_html=html,
        )
    except Exception as exc:  # noqa: BLE001 — reported below, never swallowed
        from poindexter.utils.exception_format import describe_exception

        review, status, detail = None, "failed", f"check raised {describe_exception(exc)}"

    if review is not None:
        MultiModelQA._mark_advisory_if_configured(review, gate_states, "vision_gate")
        return reviewer_to_dict(review), None
    if status == "disabled":
        return None, None
    detail = detail or "no verdict and no reason given"
    _report_preview_failure(state, detail)
    return None, detail


async def _emit_deliberate_pass(
    state: dict[str, Any],
    content: str,
    site_config: Any,
    preview_failure: str | None = None,
) -> dict[str, Any]:
    """Emit a deliberate, advisory, non-vetoing vision review when neither leg
    produced one — so a REQUIRED ``vision_gate`` is satisfied by presence
    instead of failing the post closed on a vacuous run (#563).

    The review aliases to ``vision_gate`` (``reviewer="image_relevance"``) and is
    advisory: it registers as *present* for the qa.aggregate vacuous-pass guard
    but neither vetoes nor feeds a fabricated score into the weighted mean. The
    feedback reason — and whether the operator is paged — depend on WHY there
    was nothing to score:

    - **No inline images** (case C): genuinely nothing to assess → pass by
      vacuity, no page. When the rendered-preview leg is on and produced no
      verdict, the reason says so; that failure was already reported as a
      ``qa_rail_degraded`` finding (``rail="rendered_preview"``), so there is
      no second page.
    - **Images present** (case D): the image-relevance leg couldn't assess them
      (vision model unreachable / unparseable). Operator policy is fail-open +
      page — the post proceeds, the operator is alerted to fix the model.
    """
    from poindexter.modules.content.multi_model_qa import (
        ReviewerResult,
        extract_inline_image_urls,
    )

    image_urls = extract_inline_image_urls(content)
    task_id = str(state.get("task_id") or "?")[:8]

    page_msg = ""
    if image_urls:
        reason = (
            f"could not assess {len(image_urls)} inline image(s) — no vision "
            "verdict returned; passing open"
        )
        # Honest page: do NOT assert the model is down as the sole cause — it is
        # frequently healthy (it captions the same images seconds earlier) and
        # the real reason was previously logged only at DEBUG, which the worker
        # doesn't ship to Loki, so the operator was sent to check a model that
        # was fine. Enumerate the causes and point at the shippable [VISION_QA]
        # breadcrumb (now WARNING) that names the specific one
        # (vision_scorer_unavailable RCA 2026-07-12).
        page_msg = (
            f"qa.vision (task {task_id}): {reason}. The image-relevance vision "
            "call returned no usable result. The cause is one of: the vision "
            "model (qa_vision_model) is unreachable, a transient worker "
            "dispatch/handle gap, or an unparseable model response — check the "
            "worker logs for [VISION_QA] to see the specific reason."
        )
        # Typed finding so the pass-open is VISIBLE on the Findings surfaces
        # (dashboard panel + per-kind delivery policy) instead of living only
        # in this task's qa_feedback text. The gate still fails open — the
        # finding is the durable "fix the vision infra" signal. Routed per
        # findings.vision_scorer_unavailable.delivery.
        try:
            from poindexter.utils.findings import emit_finding

            emit_finding(
                source="qa_vision",
                kind="vision_scorer_unavailable",
                title=(
                    f"qa.vision passed open on task {task_id} — "
                    "vision scorer unavailable"
                ),
                body=page_msg,
                severity="warn",
                dedup_key=f"vision_scorer_unavailable:qa_vision:{task_id}",
                extra={
                    "surface": "qa_vision",
                    "task_id": str(state.get("task_id") or ""),
                    "image_count": len(image_urls),
                },
            )
        except Exception as exc:  # noqa: BLE001 — finding emission must not gate QA
            logger.warning("[qa.vision] finding emission failed: %s", exc)
    elif preview_failure:
        reason = (
            "no inline images to assess, and the rendered-preview leg produced "
            f"no verdict ({preview_failure[:160]}); passing open"
        )
    else:
        reason = "no inline images to assess — vision gate satisfied by vacuity"

    if page_msg:
        logger.warning("[qa.vision] %s", page_msg)
        try:
            from poindexter.services.integrations.operator_notify import notify_operator

            await notify_operator(page_msg, critical=False, site_config=site_config)
        except Exception as exc:  # noqa: BLE001
            # silent-ok: notify_operator is contractually non-raising and
            # ALREADY logs at ERROR when every delivery path fails (see
            # services/integrations/operator_notify.py). This particular
            # notification is explicitly non-critical: an FYI about a vision
            # review, not a gate decision.
            logger.debug("[qa.vision] operator notify failed (non-critical): %s", exc)

    review = ReviewerResult(
        reviewer="image_relevance",  # aliases to vision_gate in _REVIEWER_TO_GATE
        approved=True,
        score=100.0,
        feedback=f"[vision] {reason}",
        provider="vision_gate",
        advisory=True,  # present for the gate, but never vetoes or scores
    )
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    title = state.get("seo_title") or state.get("title") or state.get("topic") or ""
    topic = state.get("topic") or ""
    # Pool feeds cost-logging inside _vision_complete's dispatch. The shared
    # resolver prefers database_service.pool and falls back to site_config._pool
    # (the same live handle caption_images uses), logging a loud [POOL] warning if
    # the fallback ever engages. NB: the false "vision unavailable" pages this
    # rail emitted were NOT a dead pool — the model ran fine (GPU + thousands of
    # tokens spent); the real cause was qwen3-vl's <think> trace truncating the
    # JSON scores under a too-small num_predict, fixed in _maybe_bump_vision_
    # thinking_budget. This resolver is defense-in-depth (vision_scorer_unavailable
    # RCA + follow-up 2026-07-12).
    pool = resolve_pool(state, atom="qa.vision")
    settings_service = state.get("settings_service")

    from poindexter.modules.content.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config, platform=state.get("platform"))
    gate_states = await resolve_gate_states(qa)

    reviews: list[dict[str, Any]] = []

    # 1. Image relevance — restores the cold qa_gates.vision_gate counter.
    #    Returns None when qa_vision_check_enabled is false / no images / vision
    #    model unreachable; otherwise a ReviewerResult(reviewer="image_relevance").
    #    The featured/hero image is included alongside inline images so the
    #    same vision_gate rail scores it (it leads, never dropped by the cap).
    try:
        image_review = await qa._check_image_relevance(
            title, topic, content,
            featured_image_url=state.get("featured_image_url") or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[qa.vision] image-relevance check raised: %s", exc)
        image_review = None
    if image_review is not None:
        MultiModelQA._mark_advisory_if_configured(image_review, gate_states, "vision_gate")
        reviews.append(reviewer_to_dict(image_review))

    # 2. Rendered-preview screenshot of THIS draft, rendered in-process.
    preview_review, preview_failure = await _run_rendered_preview_leg(
        qa, state, content=content, title=title, topic=topic, gate_states=gate_states,
    )
    if preview_review is not None:
        reviews.append(preview_review)

    if reviews:
        return {"qa_rail_reviews": reviews}

    # Neither leg produced a review. Emit a DELIBERATE, advisory, non-vetoing
    # review (never a silent {}) so a REQUIRED vision_gate is satisfied by
    # presence rather than failed closed on a vacuous run — that empty-{} return
    # is exactly how the gate stayed cold and became un-graduatable
    # (feedback_no_silent_defaults; Glad-Labs/poindexter#563).
    return await _emit_deliberate_pass(state, content, site_config, preview_failure)


__all__ = ["ATOM_META", "run"]
