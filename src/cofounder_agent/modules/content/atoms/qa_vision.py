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
2. **Rendered-preview screenshot** (``_check_rendered_preview`` → reviewer
   ``rendered_preview``). Screenshots the post's ``/preview/{token}`` URL and
   feeds the PNG to a vision model to catch layout breaks, missing CSS,
   overflowing tables, broken images. Opt-in via ``qa_preview_screenshot_enabled``;
   needs a ``preview_url``.

This atom mirrors ``qa.programmatic`` (which restored the dropped
``programmatic_validator`` gate the same way) and ``qa.ragas`` (which reads a
soft ``research_context`` input). The ``preview_url`` is a soft input read off
the shared state — produced early by ``stage.verify_task`` (the preview token
is generated at the top of the pipeline and surfaced as ``preview_url`` so it
reaches this rail, which runs BEFORE ``finalize_task``). The two vision
reviews are appended to the ``qa_rail_reviews`` channel; both carry
``provider='vision_gate'`` so ``_qa_rail_common`` weights them at
``gate_weight`` and a non-advisory failing review vetoes in ``qa.aggregate``.

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

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec
from services.logger_config import get_logger

logger = get_logger(__name__)

_DEFAULT_PREVIEW_BASE_URL = "http://localhost:8002"

ATOM_META = AtomMeta(
    name="qa.vision",
    type="atom",
    version="1.0.0",
    description=(
        "Vision/preview QA rail — image-relevance + rendered-preview "
        "screenshot checks via a vision model; advisory is DB-driven via "
        "qa_gates.vision_gate.required_to_pass. Reads a soft preview_url."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(
            name="preview_url",
            type="str",
            description="rendered /preview/{token} URL (soft — built from preview_token if absent)",
            required=False,
        ),
    ),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="vision reviews"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls a vision-capable ollama model; screenshots preview via chromium",),
    parallelizable=True,
)


def _resolve_preview_url(state: dict[str, Any], site_config: Any) -> str | None:
    """Resolve a preview URL from state.

    Prefers an explicit ``preview_url`` channel; falls back to building one
    from ``preview_token`` + the operator-configured ``preview_base_url``.
    Returns None when neither is present (no token has been minted yet).
    """
    explicit = (state.get("preview_url") or "").strip()
    if explicit:
        return explicit

    token = (state.get("preview_token") or "").strip()
    if not token:
        return None

    base = _DEFAULT_PREVIEW_BASE_URL
    if site_config is not None:
        try:
            base = site_config.get("preview_base_url", _DEFAULT_PREVIEW_BASE_URL)
        except Exception:  # noqa: BLE001
            base = _DEFAULT_PREVIEW_BASE_URL
    return f"{str(base).rstrip('/')}/preview/{token}"


async def _preview_screenshot_enabled(settings_service: Any) -> bool:
    """Read qa_preview_screenshot_enabled (default false) — used only to
    decide whether an absent preview_url is a fail-loud condition."""
    if settings_service is None:
        return False
    try:
        raw = await settings_service.get("qa_preview_screenshot_enabled")
    except Exception:  # noqa: BLE001
        return False
    return str(raw or "false").strip().lower() in ("true", "1", "yes")


async def _emit_deliberate_pass(
    state: dict[str, Any],
    content: str,
    site_config: Any,
    settings_service: Any,
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
      vacuity, no page. (If preview-screenshot QA is enabled yet no preview_url
      reached the rail, page about the broken wiring — the only operator-
      actionable signal in this branch.)
    - **Images present** (case D): the image-relevance leg couldn't assess them
      (vision model unreachable / unparseable). Operator policy is fail-open +
      page — the post proceeds, the operator is alerted to fix the model.
    """
    from modules.content.multi_model_qa import (
        ReviewerResult,
        extract_inline_image_urls,
    )

    image_urls = extract_inline_image_urls(content)
    preview_unavailable = (
        not _resolve_preview_url(state, site_config)
        and await _preview_screenshot_enabled(settings_service)
    )
    task_id = str(state.get("task_id") or "?")[:8]

    page_msg = ""
    if image_urls:
        reason = (
            f"could not assess {len(image_urls)} inline image(s) — vision "
            "model/infra unavailable; passing open"
        )
        page_msg = (
            f"qa.vision (task {task_id}): {reason}. The image-relevance check "
            "returned no result — confirm the vision model (qa_vision_model) is "
            "loaded and reachable on the worker."
        )
    elif preview_unavailable:
        reason = (
            "no inline images, and preview-screenshot QA is enabled but no "
            "preview_url reached the rail; passing open"
        )
        page_msg = (
            "qa.vision: qa_preview_screenshot_enabled=true but no preview_url "
            f"reached the QA rail for task {task_id} — the rendered-preview "
            "screenshot gate is skipping. Ensure stage.verify_task surfaces "
            "preview_url (preview_token + preview_base_url) before the qa.* block."
        )
    else:
        reason = "no inline images to assess — vision gate satisfied by vacuity"

    if page_msg:
        logger.warning("[qa.vision] %s", page_msg)
        try:
            from services.integrations.operator_notify import notify_operator

            await notify_operator(page_msg, critical=False, site_config=site_config)
        except Exception as exc:  # noqa: BLE001
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
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from modules.content.multi_model_qa import MultiModelQA

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

    # 2. Rendered-preview screenshot — needs a preview_url threaded into state.
    preview_url = _resolve_preview_url(state, site_config)
    if preview_url:
        try:
            preview_review = await qa._check_rendered_preview(title, topic, preview_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[qa.vision] rendered-preview check raised: %s", exc)
            preview_review = None
        if preview_review is not None:
            MultiModelQA._mark_advisory_if_configured(
                preview_review, gate_states, "vision_gate",
            )
            reviews.append(reviewer_to_dict(preview_review))

    if reviews:
        return {"qa_rail_reviews": reviews}

    # Neither leg produced a review. Emit a DELIBERATE, advisory, non-vetoing
    # review (never a silent {}) so a REQUIRED vision_gate is satisfied by
    # presence rather than failed closed on a vacuous run — that empty-{} return
    # is exactly how the gate stayed cold and became un-graduatable
    # (feedback_no_silent_defaults; Glad-Labs/poindexter#563).
    return await _emit_deliberate_pass(state, content, site_config, settings_service)


__all__ = ["ATOM_META", "run"]
