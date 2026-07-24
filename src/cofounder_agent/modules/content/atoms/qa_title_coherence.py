"""qa.title_coherence — does the TITLE honestly represent the article body?

The 2026-07-24 failure class: titling runs early (graph position 3) and was
topic-grounded, so a vague or directive-shaped topic produced titles about the
topic's most likely READING instead of the actual draft — task 1149dfc8
(topic "The Console Transition") shipped the gaming-hardware title "Mastering
the Leap to Next-Gen Gaming Hardware" over an operator-console-rebuild essay,
and task 1afabaf9 carried generic mush over an insights-category post. The
generation-side fixes ground the title prompt in the draft; this rail is the
detection layer for whatever still slips through — including a title drifting
from the body after a ``qa.rewrite`` rescue revision, because the rail sits
inside the QA loop and re-judges on every pass.

Mechanism — an LLM verdict, NOT embedding cosine. Embedding similarity was
calibrated against the live corpus (2026-07-24) and FALSIFIED for this job:
title↔body cosine measures topical overlap, and the real failures are
topically overlapping framing mismatches. The bad gaming title scored 0.629
against its console-rebuild body (above the legit-corpus median of 0.612,
because the body is saturated with "console/generation/transition" vocabulary)
and the raw directive scored 0.717, while legitimate abstract titles ("The
Question We Actually Had to Answer") scored as low as 0.42 — every real
failure sits inside or above the legit band. A local-model judge reading the
digest separated the same cases 10/10 (all three known-bad flagged at
confidence 100, all seven legit/fixed titles passed).

Complements ``qa.topic_delivery`` (body↔topic: did the writer deliver the
assignment?) — this rail is title↔body (does the headline describe what was
actually written?). The console failure passed topic_delivery (the body DID
deliver the console topic); only the title was wrong.

Model routing: ``qa_title_coherence_model`` → ``pipeline_seo_model`` →
``pipeline_local_writer_model``. Deliberately NEVER the writer pin by default —
``pipeline_writer_model`` may be a metered cloud canary, and a QA rail must not
silently bill it (the same reasoning as the ``pipeline_seo_model`` pin).

Advisory-first: seeded ``qa_gates.title_coherence.required_to_pass=false`` so
it SCORES on every run (QA Rails dashboard) but does not veto until an
operator graduates it. Fail-open per the qa_rail_degraded convention: a rail
that could not measure appends NO review and emits a ``qa_rail_degraded``
finding — never an in-domain score. Master switch
``qa_title_coherence_enabled`` (default true).

Chain position: after ``qa.content_originality``, before ``qa.web_factcheck``.
"""

from __future__ import annotations

import logging
from typing import Any

from modules.content.atoms._pool import resolve_pool
from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from modules.content.multi_model_qa import MultiModelQA, ReviewerResult
from plugins.atom import AtomMeta, FieldSpec
from utils.json_extract import extract_json_object

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.title_coherence",
    type="atom",
    version="1.0.0",
    description=(
        "LLM verdict on whether the TITLE honestly represents the article "
        "body — catches wrong-domain titles, directive/assignment-label "
        "leaks, and unrecognizably generic titles. Local-model judge on a "
        "draft digest. Advisory-first (DB-driven via qa_gates.title_coherence)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(name="title", type="str", description="canonical display title under review"),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="title-coherence review result",
        ),
    ),
    requires=("content", "title"),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("one local LLM judge call",),
    parallelizable=True,
)

# Digest size handed to the judge. 3000 chars + the section-heading skeleton
# was the calibrated setup that separated the known-bad from the legit corpus
# 10/10. DB-tunable via qa_title_coherence_digest_chars.
_DEFAULT_DIGEST_CHARS = 3000


def _is_enabled(site_config: Any) -> bool:
    try:
        raw = site_config.get("qa_title_coherence_enabled", "true")
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional master switch — default the advisory rail ON (it
        # only scores, never vetoes) when a stubbed/misconfigured site_config
        # raises, rather than letting a config-read blip crash the QA block.
        return True
    return str(raw).lower() in ("true", "1", "yes")


def _resolve_model(site_config: Any) -> str | None:
    """``qa_title_coherence_model`` → ``pipeline_seo_model`` →
    ``pipeline_local_writer_model``; None only when all three are empty (the
    dispatcher then resolves its own default). The chain deliberately skips
    ``pipeline_writer_model`` — a cloud writer canary must not be silently
    billed by a QA rail."""
    for key in (
        "qa_title_coherence_model",
        "pipeline_seo_model",
        "pipeline_local_writer_model",
    ):
        try:
            value = (site_config.get(key, "") or "").strip()
        except Exception:  # noqa: BLE001 — stubbed site_config
            value = ""
        if value:
            return value.removeprefix("ollama/")
    return None


def _parse_verdict(raw: str) -> tuple[bool, int] | None:
    """``(title_represents_article, confidence 0-100)`` from the judge's JSON,
    or ``None`` when the response is not a usable measurement (missing/invalid
    verdict or confidence — a judge that can't follow the two-field format
    shouldn't be trusted for the verdict either)."""
    parsed = extract_json_object(raw or "")
    if not parsed:
        return None
    verdict = parsed.get("title_represents_article")
    if isinstance(verdict, str):
        lowered = verdict.strip().lower()
        if lowered in ("true", "yes"):
            verdict = True
        elif lowered in ("false", "no"):
            verdict = False
        else:
            return None
    if not isinstance(verdict, bool):
        return None
    raw_conf = parsed.get("confidence")
    # bool is an int subclass — a "confidence": true is not a measurement.
    if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float, str)):
        return None
    try:
        confidence = int(raw_conf)
    except (TypeError, ValueError):
        return None
    return verdict, max(0, min(100, confidence))


async def _judge(
    *, title: str, content_digest: str, state: dict[str, Any],
    site_config: Any, pool: Any,
) -> str:
    """Render the ``qa.title_coherence`` prompt and ask the resolved local
    model for the verdict. Thin indirection so tests monkeypatch here."""
    from services.llm_text import ollama_chat_text
    from services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(
        "qa.title_coherence", title=title, content=content_digest,
    )
    return await ollama_chat_text(
        prompt,
        model=_resolve_model(site_config),
        site_config=site_config,
        pool=pool,
        tier="budget",
        task_id=state.get("task_id"),
        phase="qa_title_coherence",
        # A reasoning model pinned here would deliberate inside the token
        # budget; the verdict is short structured copy — answer directly.
        think=False,
    )


def _degraded(reason: str) -> None:
    """The rail could not measure: no review is appended (a check that did not
    run must never render as a score) and the disappearance is made loud via
    the shared ``qa_rail_degraded`` finding kind."""
    logger.warning("[qa.title_coherence] no measurement — %s", reason)
    from utils.findings import emit_finding

    emit_finding(
        source="qa.title_coherence",
        kind="qa_rail_degraded",
        title="title_coherence rail could not run",
        body=(
            f"{reason}\n\nNo review was appended for this post — the rail is "
            "absent from the QA pass rather than scored. Repeated occurrences "
            "mean the rail is effectively off: check Ollama reachability and "
            "the qa_title_coherence_model / pipeline_seo_model / "
            "pipeline_local_writer_model chain."
        ),
        severity="warn",
        dedup_key="qa_rail_degraded:title_coherence",
        extra={"rail": "title_coherence", "reason": reason},
    )


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    title = (state.get("title") or "").strip()
    site_config = state.get("site_config")
    if not content or not title or site_config is None:
        return {}
    if not _is_enabled(site_config):
        return {}

    from services.title_generation import build_title_grounding_digest

    try:
        digest_chars = site_config.get_int(
            "qa_title_coherence_digest_chars", _DEFAULT_DIGEST_CHARS
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        digest_chars = _DEFAULT_DIGEST_CHARS
    content_digest = build_title_grounding_digest(content, max_chars=digest_chars)

    pool = resolve_pool(state, atom="qa.title_coherence")
    try:
        raw = await _judge(
            title=title, content_digest=content_digest, state=state,
            site_config=site_config, pool=pool,
        )
    except Exception as exc:  # noqa: BLE001 — a rail must never crash a run
        _degraded(f"judge call raised {type(exc).__name__}: {exc}")
        return {}

    verdict = _parse_verdict(raw)
    if verdict is None:
        _degraded(f"unparseable judge response: {(raw or '')[:160]!r}")
        return {}

    matches, confidence = verdict
    parsed = extract_json_object(raw) or {}
    reason = str(parsed.get("reason") or "").strip() or (
        "title represents the article" if matches else "title does not represent the article"
    )
    # Score is 0-100, higher = better title↔body fit: the judge's confidence
    # when it approved, its inverse when it flagged.
    score = float(confidence if matches else 100 - confidence)

    review = ReviewerResult(
        reviewer="title_coherence",
        approved=matches,
        score=round(score, 1),
        feedback=f"[title: {title[:80]}] {reason}",
        provider="title_coherence_gate",
    )

    # Advisory status is DB-driven via qa_gates.title_coherence.required_to_pass;
    # apply it here (qa.aggregate vetoes on approved=False AND advisory=False,
    # and does NOT read required_to_pass back onto the review). Mirrors
    # qa.self_consistency / qa.content_originality.
    settings_service = state.get("settings_service")
    qa = MultiModelQA(
        pool=pool,
        settings_service=settings_service,
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "title_coherence")

    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
