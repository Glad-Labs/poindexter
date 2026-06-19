"""qa.vision — the vision/preview QA gate as one composable rail atom.

Restores the two vision-model gates that stopped running on the live path
when the #355 atom-cutover replaced ``MultiModelQA.review()`` with the
``qa.*`` atom chain. ``review()`` ran two vision checks inline (sections 2d
and 2h) — the cutover ported the text rails (``qa.critic`` / ``qa.deepeval``
/ ``qa.ragas``) and the programmatic net (``qa.programmatic``)
but NOT the vision legs, so both went cold (Glad-Labs/poindexter#563):

1. **Image relevance** (``_check_image_relevance`` → reviewer ``image_relevance``,
   aliased to the ``vision_gate`` qa_gates row). Checks inline images actually
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

Fail-loud (``feedback_no_silent_defaults``): when ``qa_preview_screenshot_enabled``
is true but no ``preview_url`` is resolvable from state, the atom logs a
warning AND pages the operator instead of silently skipping — that silent
skip is exactly how the gate stayed cold for ~3 weeks unnoticed.
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
    try:
        image_review = await qa._check_image_relevance(title, topic, content)
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
    elif await _preview_screenshot_enabled(settings_service):
        # Fail loud (feedback_no_silent_defaults): the operator opted into
        # preview-screenshot QA but no preview_url reached this rail, so the
        # gate would silently no-op — exactly the cold state #563 flagged.
        # Surface it instead of swallowing.
        task_id = str(state.get("task_id") or "?")[:8]
        msg = (
            "qa.vision: qa_preview_screenshot_enabled=true but no preview_url "
            f"reached the QA rail for task {task_id} — the rendered-preview "
            "screenshot gate is skipping. Ensure stage.verify_task surfaces "
            "preview_url (preview_token + preview_base_url) before the qa.* block."
        )
        logger.warning("[qa.vision] %s", msg)
        try:
            from services.integrations.operator_notify import notify_operator

            await notify_operator(msg, critical=False, site_config=site_config)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[qa.vision] operator notify failed (non-critical): %s", exc)

    if not reviews:
        return {}
    return {"qa_rail_reviews": reviews}


__all__ = ["ATOM_META", "run"]
