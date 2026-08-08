"""Writer self-review pass — catch cross-section contradictions before QA.

Lifted from content_router_service.py during Phase E2 (issue #170's
prevention layer). A review call asks the model to check its own draft for
internal contradictions; if any are found, a follow-up call asks the model
to fix only those specific issues.

Both calls reuse the writer via ``writer_self_review_model`` and route
through :func:`services.llm_providers.dispatcher.dispatch_complete` when a
``pool`` is available (same 404 class as glad-labs-stack#2199) — so a cloud
model (e.g. ``anthropic/claude-sonnet-5``) reaches LiteLLM/Anthropic
(cost-tracked + cost_guard-gated) instead of being POSTed to local Ollama,
which 404s on a cloud model name. With no pool (tests / bootstrap) the local
``ollama_native`` provider is called directly — local-only by design.

Gated by app_settings ``enable_writer_self_review`` (default ``false``).
When disabled, returns ``(draft, {"enabled": False, ...})`` unchanged.

The WriterSelfReviewStage + the inline self-review inside
GenerateContentStage both call this helper.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from services.integrations.operator_notify import notify_operator
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)


# Inline fallbacks — used when UnifiedPromptManager is unavailable
# (bootstrap / tests / Langfuse + YAML both missing). Per
# feedback_prompts_must_be_db_configurable the live edit surface is
# Langfuse → YAML; these constants only protect the cold-start path.
_REVIEW_PROMPT_FALLBACK = (
    "You are reviewing your own draft for internal contradictions.\n\n"
    "TITLE: {title}\n"
    "TOPIC: {topic}\n\n"
    "DRAFT:\n{draft}\n\n"
    "Read every section. Identify any claim in one section that contradicts "
    "a claim, code example, or recommendation in another section. "
    "Ignore stylistic variation; focus on factual or logical conflicts.\n\n"
    "If you find contradictions, output a numbered list of specific corrections "
    "needed (one per line, format: 'SECTION X conflicts with SECTION Y: <details>'). "
    "If you find none, reply with exactly: PASS"
)

_REVISE_PROMPT_FALLBACK = (
    "Here is your draft. Fix these specific contradictions and nothing else:\n\n"
    "CONTRADICTIONS TO FIX:\n{review_text}\n\n"
    "ORIGINAL DRAFT:\n{draft}\n\n"
    "Output only the revised draft. Keep the structure, length, and tone "
    "identical. Only change what's needed to resolve the contradictions. "
    "Preserve any [IMAGE: ...], [IMAGE-N: ...], and [HERO-IMAGE: ...] markers "
    "exactly as they appear — do not remove, renumber, reword, or relocate them."
)


def _resolve_prompt(key: str, *, fallback: str, **kwargs: Any) -> str:
    """Pull a prompt via UnifiedPromptManager; format the inline fallback
    if the manager is unreachable. Mirrors the standard resolve-then-fallback
    prompt pattern so operator-edited prompts in Langfuse win without forcing
    a restart.
    """
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(key, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[self_review] prompt_manager lookup for %r failed (%s) — "
            "using inline fallback",
            key, exc,
        )
        return fallback.format(**kwargs)


async def _resolve_self_review_model(
    pool: Any, *, site_config: SiteConfig,
) -> str:
    """Resolve the writer-self-review model from ``writer_self_review_model``.

    Reads the dedicated ``app_settings[writer_self_review_model]`` pin and fails
    loud (notify + raise) when unset, per feedback_no_silent_defaults.md. The
    ``cost_tier.*`` tier fallback was removed; ``pool`` is retained for
    signature stability with the call site.

    Phase-2 DI (#272): ``site_config`` is a required keyword arg — the
    module global + ``set_site_config`` shim was retired.
    """
    _sc = site_config
    model = _sc.get("writer_self_review_model")
    if model:
        return str(model)

    await notify_operator(
        "writer_self_review: writer_self_review_model is empty — self-review "
        "skipped (set writer_self_review_model)",
        critical=False,
    )
    raise RuntimeError(
        "writer_self_review: no model resolvable — set writer_self_review_model"
    )


def _emit_rejected_finding(model: str, reason: str) -> None:
    """Best-effort visibility when a self-review revision is discarded.

    Silence here is what let the same producer bug recur six times between
    2026-07-04 and 2026-08-07 before anyone looked (poindexter#1000) — the
    stage degraded to a no-op on some runs and corrupted the draft on
    others, and neither outcome surfaced anywhere.
    """
    try:
        from utils.findings import emit_finding

        emit_finding(
            source="services.self_review",
            kind="self_review_revision_rejected",
            title=f"writer_self_review revision from {model!r} was discarded",
            body=(
                f"The contradiction-revise pass returned output that failed "
                f"the minimal-edit contract ({reason}), so the ORIGINAL draft "
                f"was kept. A revise pass is supposed to edit sentences, not "
                f"restructure the article or narrate its own reasoning — this "
                f"usually means the reviser model leaked its brief/"
                f"deliberation instead of emitting the revised draft "
                f"(Glad-Labs/poindexter#1000). Recurrence means "
                f"`writer_self_review_model` ({model!r}) is a poor fit for "
                f"this instruction-following task; consider repointing it or "
                f"disabling `enable_writer_self_review`."
            ),
            severity="warn",
            dedup_key=f"self_review_revision_rejected:{model}",
            extra={"model": model, "reason": reason},
        )
    except Exception:  # noqa: BLE001 — observability must never break the stage
        # silent-ok: the finding is best-effort; the draft is already safe
        pass


async def self_review_and_revise(
    draft: str, title: str, topic: str, *, pool: Any = None,
    site_config: SiteConfig,
) -> tuple[str, dict]:
    """Ask the writer model to catch + fix cross-section contradictions.

    Returns ``(possibly_revised_draft, stats_dict)`` where stats includes:

    - ``enabled`` (bool) — False when the feature flag is off; True otherwise.
    - ``contradictions_found`` (int) — count the detector returned.
    - ``revised`` (bool) — True only when we accepted the revision.

    Phase-2 DI (#272): ``site_config`` is a required keyword arg — the
    module global + ``set_site_config`` shim was retired. The instance is
    threaded down to ``_resolve_self_review_model`` so the whole call chain
    shares one config.
    """
    _sc = site_config
    stats: dict = {"enabled": False, "contradictions_found": 0, "revised": False}

    enabled = (
        str(_sc.get("enable_writer_self_review", "false")).lower() == "true"
    )
    if not enabled:
        return draft, stats

    stats["enabled"] = True
    if not draft or len(draft) < 500:
        return draft, stats  # too short for meaningful cross-section review

    # Per-step pin. Operators tune app_settings.writer_self_review_model —
    # no code edit per niche. _resolve_self_review_model reads it directly
    # and fails loud via notify_operator when unset, per
    # feedback_no_silent_defaults.md.
    try:
        resolved_model = await _resolve_self_review_model(pool, site_config=_sc)
    except RuntimeError as exc:
        logger.warning("[SELF_REVIEW] could not resolve model: %s", exc)
        return draft, stats
    review_model = str(resolved_model).removeprefix("ollama/")

    review_prompt = _resolve_prompt(
        "qa.self_review.contradictions_review",
        title=title,
        topic=topic,
        draft=draft,
        fallback=_REVIEW_PROMPT_FALLBACK,
    )

    # v2.3: Provider Protocol instead of concrete OllamaClient. The
    # timeout knob survives via the new ``timeout_s`` kwarg (v2.1).
    timeout_s = _sc.get_int(
        "content_router_contradiction_timeout_seconds", 120,
    )

    # Routing (Sonnet-canary 404 fix; same class as glad-labs-stack#2199): the
    # local ``ollama_native`` provider is only needed for the no-pool fallback.
    # With a pool we defer provider selection to ``dispatch_complete`` (per
    # ``plugin.llm_provider.primary.standard``), which is what lets a cloud
    # ``writer_self_review_model`` route to LiteLLM rather than 404 against
    # local Ollama.
    local_provider = None
    if pool is None:
        from plugins.registry import get_all_llm_providers
        providers = {p.name: p for p in get_all_llm_providers()}
        local_provider = providers.get("ollama_native")
        if local_provider is None:
            logger.warning(
                "[SELF_REVIEW] ollama_native provider not registered; skipping",
            )
            return draft, stats

    async def _complete(
        prompt: str, *, temperature: float, max_tokens: int,
    ) -> Any:
        """One self-review completion, routed like the draft.

        Pool present → ``dispatch_complete`` (a cloud model routes to
        LiteLLM + cost_guard; a local one stays local + free). No pool →
        the local ``ollama_native`` provider resolved above. The LiteLLM
        provider drops Ollama-only params for cloud targets, so the shared
        call signature is safe on either backend.
        """
        messages = [{"role": "user", "content": prompt}]
        if pool is not None:
            # Production / in-graph path — the SAME dispatcher the draft uses.
            # A cloud model routes to LiteLLM (cost-tracked + cost_guard-gated);
            # a local model stays local + free. This is the #2199-class fix: the
            # old hardcoded ollama_native call POSTed a cloud model name to
            # local Ollama and 404'd.
            # No ``num_ctx`` here BY DESIGN — do not "fix" this by threading one.
            # ``dispatch_complete`` back-fills it for LOCAL dispatches that did
            # not supply one, via ``_resolve_default_num_ctx(phase, ...)`` ->
            # ``resolve_num_ctx`` -> ``writer_self_review_num_ctx`` ->
            # ``ollama_num_ctx`` -> 8192 (glad-labs-stack#2170). The ``phase``
            # below is what keys that lookup, so the per-phase override IS live.
            # Supplying num_ctx explicitly would suppress the back-fill — and the
            # back-fill is the ONLY thing that skips the param for a paid/cloud
            # model, so a cloud ``writer_self_review_model`` (the whole point of
            # the routing above) would then carry an Ollama-only param and drag
            # the VRAM clamp's local ``/api/show`` into every Anthropic call.
            # Pinned by ``tests/unit/services/test_self_review_num_ctx.py``.
            from services.llm_providers.dispatcher import dispatch_complete
            return await dispatch_complete(
                pool,
                messages,
                review_model,
                tier="standard",
                phase="writer_self_review",
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
            )
        if local_provider is None:  # pragma: no cover - pool is None ⟹ resolved above
            raise RuntimeError(
                "[SELF_REVIEW] ollama_native provider unavailable in no-pool path",
            )
        return await local_provider.complete(
            messages=messages,
            model=review_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )

    try:
        result = await _complete(
            review_prompt,
            temperature=0.2,
            max_tokens=_sc.get_int(
                "content_router_contradiction_review_max_tokens", 1500,
            ),
        )
        review_text = (result.text or "").strip()

        if not review_text or review_text.upper().startswith("PASS"):
            return draft, stats

        contradictions = [
            ln for ln in review_text.splitlines()
            if re.match(r"^\s*\d+[\.\)]\s+", ln)
        ]
        stats["contradictions_found"] = len(contradictions)
        if not contradictions:
            return draft, stats

        revise_prompt = _resolve_prompt(
            "qa.self_review.contradictions_revise",
            review_text=review_text,
            draft=draft,
            fallback=_REVISE_PROMPT_FALLBACK,
        )
        revised = await _complete(
            revise_prompt,
            temperature=0.3,
            max_tokens=_sc.get_int(
                "content_router_contradiction_revise_max_tokens", 8000,
            ),
        )
        revised_text = (revised.text or "").strip()

        # Scrub reasoning/scaffold the reviser may have prepended before the
        # article. This path NEVER re-enters content.normalize_draft (the
        # graph runs normalize_draft BEFORE writer_self_review), so the strip
        # has to happen here — the same reason qa.rewrite carries its own
        # (poindexter#897). poindexter#1000: without it, a gemma-4-31B revise
        # pass shipped 87 lines of "Role: Reviewer…/Wait, let me re-read…"
        # with the article fused onto the last bullet, and it rode all the
        # way to awaiting_approval at quality 94.
        if revised_text:
            # Substrate reaches content through the api adapter, never a deep
            # import (the modules/content/api.py boundary).
            from modules.content.api import strip_leaked_planning_scaffold
            from services.llm_providers.thinking_models import (
                strip_reasoning_artifacts,
            )

            cleaned = strip_leaked_planning_scaffold(
                strip_reasoning_artifacts(revised_text)
            ).strip()
            if len(cleaned) != len(revised_text):
                logger.info(
                    "[SELF_REVIEW] stripped %d chars of leaked reasoning/"
                    "scaffold from the revision",
                    len(revised_text) - len(cleaned),
                )
                revised_text = cleaned

        # Minimal-edit contract (poindexter#1000). A contradiction fix edits
        # sentences; it does not restructure or triple the article. Bound the
        # revision on BOTH sides — the old guard had only a floor, so a 3x
        # deliberation dump (13,567 chars from a ~4,400-char draft) sailed
        # through — and reject outright when the result reads as a planning /
        # deliberation dump. Rejecting keeps the ORIGINAL draft, which is the
        # correct outcome regardless of what the reviser returned.
        min_ratio = _sc.get_float("writer_self_review_min_length_ratio", 0.7)
        max_ratio = _sc.get_float("writer_self_review_max_length_ratio", 1.3)
        ratio = (len(revised_text) / len(draft)) if draft else 0.0
        reject_reason: str | None = None
        if ratio < min_ratio:
            reject_reason = f"too short (ratio {ratio:.2f} < {min_ratio})"
        elif ratio > max_ratio:
            reject_reason = f"too long (ratio {ratio:.2f} > {max_ratio})"
        else:
            from modules.content.api import has_planning_dump

            dump = has_planning_dump(revised_text)
            if dump:
                reject_reason = "scaffold/deliberation dump (" + ", ".join(dump[:3]) + ")"

        if reject_reason is None:
            stats["revised"] = True
            logger.info(
                "[SELF_REVIEW] Revised draft: %d contradictions found, %d chars in/%d out",
                len(contradictions), len(draft), len(revised_text),
            )
            return revised_text, stats

        stats["rejected_reason"] = reject_reason
        logger.warning(
            "[SELF_REVIEW] Revision REJECTED — %s; keeping original (%d chars, "
            "model=%s)",
            reject_reason, len(draft), review_model,
        )
        _emit_rejected_finding(review_model, reject_reason)
    except Exception as e:
        logger.warning("[SELF_REVIEW] Self-review failed (non-fatal): %s", e)

    return draft, stats
