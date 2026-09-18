"""qa.person_mention — should this named human be in this post at all?

Every other content rail asks whether the draft is TRUE. This one exists
because a true statement can still be one we must not publish: on 2026-08-09 a
draft reached ``awaiting_approval`` at Q94 having named a private individual —
someone with no connection to the business, surfaced by web research purely
because they share a surname with our product — and characterised how they do
their job from a rating site. The claims were verified accurate, which is
exactly why no fabrication, citation, or fact-check rail could ever fire on
them. There was no seam where "should this person be here?" got asked
(poindexter#1009). Only the human approval gate caught it.

**Two mechanisms, split by what each is good at** (the calibration below):

1. **Deterministic — reputation data attached to a named person.** Rating and
   review-site vocabulary in the same sentence as a person the extractor
   found. Needs no judge, and fires regardless of whether the person is
   public: scraped reputation data about a named human is a bad look in
   commercial content either way. Keyed to EXTRACTED people, never to raw
   capitalised bigrams — "Our Poindexter … researches, writes, reviews" pairs
   a capitalised phrase with "reviews" and is not about a person at all.
2. **LLM — public figure, or private individual?** The genuinely fuzzy call.
   Public figures in their public capacity are the job and must keep passing
   (Ray Dalio and Martin Gardner are cited legitimately in the same batch).

**Calibrated before wiring** (2026-09-01, 10-case set, local judge). Every
cheaper shape was noise, so the two-stage structure here is load-bearing:

| shape | balanced acc | how it failed |
| --- | --- | --- |
| one call: find AND judge | 0.50 | flagged all 10, including a passage with no person in it |
| two-stage, plain extraction | 0.42 | returned `[]` for text naming Ray Dalio |
| two-stage, few-shot extraction | **0.83** | specificity 1.00; the remaining miss was an unparsed judgment |

Three consequences are baked in here and should not be "simplified" away:

- **Detection is split from judgment.** Asking one call to find candidates and
  weigh them lets "is there a risk here?" dominate. Extract first, then judge
  ONE person per call against a narrow question.
- **"Nobody is named" is a CODE path, not a model answer.** An empty extraction
  returns early. The first prompt ended with *"if none, respond with exactly:
  []"* and the model then answered `[]` for passages full of names while
  inventing names for the empty one — a prompt bug that reads exactly like a
  model limitation. It is fixed with two worked examples, one populated and
  one empty.
- **An unparseable judgment fails CLOSED.** The known-bad case once passed
  purely because the judge's response could not be parsed and "no verdict" was
  read as "no objection". Degraded is a third outcome: offenders are still
  reported, but the rail will not certify a PASS it could not measure.

Model routing: ``qa_person_mention_model`` → ``pipeline_seo_model`` →
``pipeline_local_writer_model`` — never ``pipeline_writer_model``, which may be
a metered cloud canary a QA rail must not silently bill. A thinking model
returns an EMPTY response through this surface, so the judge is asked to answer
directly (``think=False``) with a token budget that fits the verdict (400
truncated 3 of 10 judgments during calibration).

Advisory-first: seeded ``qa_gates.person_mention.required_to_pass=false``, so it
scores and surfaces offenders without vetoing until an operator graduates it.
Master switch ``qa_person_mention_enabled`` (default true).

Chain position: after ``qa.unlinked_attribution``, before ``qa.consistency``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from poindexter.modules.content.atoms._pool import resolve_pool
from poindexter.modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from poindexter.modules.content.multi_model_qa import MultiModelQA, ReviewerResult
from poindexter.plugins.atom import AtomMeta, FieldSpec
from poindexter.utils.json_extract import extract_json_object

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.person_mention",
    type="atom",
    version="1.0.0",
    description=(
        "Should this named human be in this post? Deterministic check for "
        "rating/review-site data attached to a named person, plus a two-stage "
        "local-LLM pass that classifies each extracted name as a public figure "
        "in public capacity or a private individual. Advisory-first (DB-driven "
        "via qa_gates.person_mention)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="person-mention review result",
        ),
    ),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("one extraction LLM call, plus one judge call per named person",),
    parallelizable=True,
)

# Penalty per offender. Advisory, so this shapes the all-rail score and the
# operator's read rather than the gate. DB-tunable.
_DEFAULT_PENALTY = 25.0

# Names judged per draft. Named individuals are rare in this corpus (a scan of
# 199 published posts found essentially one legitimate mention), so this is a
# runaway guard, not a working limit — a draft naming twenty people is itself
# the finding.
_DEFAULT_MAX_PEOPLE = 8

# Characters of draft handed to the extractor.
_DEFAULT_DIGEST_CHARS = 4000

# Judge budget. 400 truncated 3 of 10 judgments during calibration.
_DEFAULT_MAX_TOKENS = 900

# Reputation/review-site vocabulary. NOUNS about how a person is rated — not
# "reviews" as a verb, which ordinary prose about our own pipeline uses
# constantly ("the engine researches, writes, reviews, and publishes").
_REPUTATION_RE = re.compile(
    r"\b(?:"
    # "N out of 5" needs a rating word: "4 out of 5 candidates were
    # system-introspection topics" is a counting idiom, and it was the only
    # false positive across 207 published posts.
    r"star\s+rating|rating\s+of|rated\s+\d"
    r"|(?:rated|rating|score[sd]?|stars?|average)\b.{0,15}?\d(?:\.\d)?\s*(?:/|out\s+of)\s*(?:5|five)\b"
    r"|\d(?:\.\d)?\s*/\s*5\s+(?:stars?|rating)\b"
    r"|\d(?:\.\d)?[- ]star\b"
    r"|customer\s+reviews?|online\s+reviews?|user\s+reviews?|review\s+score"
    r"|reviewed\s+by\s+(?:patients|customers|clients)"
    r"|complaints?\s+(?:record|history|filed)"
    r"|Yelp|Trustpilot|Glassdoor|Healthgrades|Avvo|ZocDoc|RateMyProfessors?"
    r"|Better\s+Business\s+Bureau|Google\s+reviews?"
    r")\b",
    re.IGNORECASE,
)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text) if s.strip()]


def find_reputation_mentions(content: str, people: list[str]) -> list[str]:
    """People named in a sentence that also carries reputation-site data.

    Independent of the public-figure question by design: scraped reputation
    data about a named human does not belong in commercial content even when
    the person is arguably public.
    """
    offenders: list[str] = []
    for sentence in _sentences(content):
        hit = _REPUTATION_RE.search(sentence)
        if not hit:
            continue
        for person in people:
            if person and person.lower() in sentence.lower() and person not in offenders:
                offenders.append(person)
    return [
        f'names "{p}" alongside rating or review-site data — reputation-scraped '
        "detail about a person does not belong in the post regardless of who they are"
        for p in offenders
    ]


def person_context(content: str, person: str, *, window: int = 2) -> str:
    """The sentences around the first mention, for the classify prompt."""
    sentences = _sentences(content)
    for i, sentence in enumerate(sentences):
        if person.lower() in sentence.lower():
            lo = max(0, i - 1)
            return " ".join(s.strip() for s in sentences[lo : i + window])
    return content[:400]


def parse_people(raw: str) -> list[str] | None:
    """Extracted names, or None when the response is not a usable answer.

    None is DEGRADED, never "nobody was named" — an extractor we could not read
    must not be recorded as having found nothing.
    """
    parsed = extract_json_object(raw or "")
    if not parsed or not isinstance(parsed.get("people"), list):
        return None
    names: list[str] = []
    for item in parsed["people"]:
        if not isinstance(item, str):
            continue
        name = " ".join(item.split()).strip(" .,;:\"'")
        if name and name not in names:
            names.append(name)
    return names


def parse_status(raw: str) -> tuple[str, int] | None:
    """``(status, confidence)`` where status is ``public``/``private``, or None
    when the judge's answer is not a usable measurement."""
    parsed = extract_json_object(raw or "")
    if not parsed:
        return None
    status = parsed.get("status")
    if not isinstance(status, str):
        return None
    status = status.strip().lower()
    if status not in ("public", "private"):
        return None
    raw_conf = parsed.get("confidence")
    # bool is an int subclass — "confidence": true is not a measurement.
    if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float, str)):
        return None
    try:
        confidence = int(float(raw_conf))
    except (TypeError, ValueError):
        return None
    return status, max(0, min(100, confidence))


def _is_enabled(site_config: Any) -> bool:
    try:
        raw = site_config.get("qa_person_mention_enabled", "true")
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional master switch — default the advisory rail ON (it
        # scores, never vetoes) rather than let a config blip drop the check.
        return True
    return str(raw).lower() in ("true", "1", "yes")


def _resolve_model(site_config: Any) -> str | None:
    """``qa_person_mention_model`` → ``pipeline_seo_model`` →
    ``pipeline_local_writer_model``. Deliberately skips ``pipeline_writer_model``:
    a cloud writer canary must not be silently billed by a QA rail."""
    for key in (
        "qa_person_mention_model",
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


def _int_setting(site_config: Any, key: str, default: int) -> int:
    try:
        return int(site_config.get(key, default) or default)
    except Exception:  # noqa: BLE001 — stubbed site_config / non-numeric value
        # silent-ok: a bad dial falls back to the calibrated default rather
        # than taking the rail down; the value is a budget, not a verdict.
        return default


async def _ask(
    key: str, *, state: dict[str, Any], site_config: Any, pool: Any, **fields: str,
) -> str:
    """Render a prompt-pack prompt and ask the resolved local model.
    Thin indirection so tests monkeypatch one place."""
    from poindexter.services.llm_text import ollama_chat_text
    from poindexter.services.prompt_manager import get_prompt_manager

    prompt = get_prompt_manager().get_prompt(key, **fields)
    return await ollama_chat_text(
        prompt,
        model=_resolve_model(site_config),
        site_config=site_config,
        pool=pool,
        tier="budget",
        task_id=state.get("task_id"),
        phase=key.replace(".", "_"),
        # A thinking model returns an EMPTY response through this surface and
        # puts its reasoning elsewhere — 9 of 10 unparsed during calibration.
        think=False,
        max_tokens=_int_setting(
            site_config, "qa_person_mention_max_tokens", _DEFAULT_MAX_TOKENS
        ),
    )


def _degraded(reason: str) -> None:
    """The rail could not measure. No PASS is certified and the disappearance
    is made loud via the shared ``qa_rail_degraded`` finding kind."""
    logger.warning("[qa.person_mention] no measurement — %s", reason)
    from poindexter.utils.findings import emit_finding

    emit_finding(
        source="qa.person_mention",
        kind="qa_rail_degraded",
        title="person_mention rail could not run",
        body=(
            f"{reason}\n\nNo pass was certified for this post — the rail is "
            "absent from the QA pass rather than scored. Repeated occurrences "
            "mean the only thing standing between a named private individual "
            "and publication is the human approval gate. Check Ollama "
            "reachability and the qa_person_mention_model / pipeline_seo_model "
            "/ pipeline_local_writer_model chain."
        ),
        severity="warn",
        dedup_key="qa_rail_degraded:person_mention",
        extra={"rail": "person_mention", "reason": reason},
    )


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None or not _is_enabled(site_config):
        return {}

    pool = resolve_pool(state, atom="qa.person_mention")
    digest = content[: _int_setting(
        site_config, "qa_person_mention_digest_chars", _DEFAULT_DIGEST_CHARS
    )]

    # --- Stage 1: who is named? ---
    try:
        raw = await _ask(
            "qa.person_mention.extract", state=state, site_config=site_config,
            pool=pool, content=digest,
        )
    except Exception as exc:  # noqa: BLE001 — a rail must never crash a run
        _degraded(f"extraction call raised {type(exc).__name__}: {exc}")
        return {}

    people = parse_people(raw)
    if people is None:
        _degraded(f"unparseable extraction response: {(raw or '')[:160]!r}")
        return {}

    # "Nobody is named" is a code path the model cannot get wrong.
    if not people:
        logger.info("[qa.person_mention] no people named — nothing to judge")
        return {}

    max_people = _int_setting(
        site_config, "qa_person_mention_max_people", _DEFAULT_MAX_PEOPLE
    )
    judged = people[:max_people]

    # --- Deterministic: reputation data attached to a named person ---
    offenders = find_reputation_mentions(content, judged)

    # --- Stage 2: one narrow question per person ---
    unparsed: list[str] = []
    for person in judged:
        try:
            verdict_raw = await _ask(
                "qa.person_mention.classify", state=state, site_config=site_config,
                pool=pool, person=person, context=person_context(content, person),
            )
        except Exception as exc:  # noqa: BLE001
            unparsed.append(f"{person} (call raised {type(exc).__name__})")
            continue
        verdict = parse_status(verdict_raw)
        if verdict is None:
            unparsed.append(person)
            continue
        status, confidence = verdict
        if status == "private":
            offenders.append(
                f'names "{person}", judged a private individual '
                f"(confidence {confidence}) — a real person is not colour for a post"
            )

    # Fail closed: without a readable verdict the rail may report what it DID
    # find, but it must never certify a pass it could not measure.
    if not offenders and unparsed:
        _degraded(
            "judge returned no usable verdict for "
            + ", ".join(unparsed[:3])
            + f" ({len(unparsed)} of {len(judged)} named people)"
        )
        return {}

    penalty = float(
        _int_setting(site_config, "qa_person_mention_offender_penalty", int(_DEFAULT_PENALTY))
    )
    score = max(0.0, 100.0 - penalty * len(offenders))
    if offenders:
        feedback = "Person mentions to review: " + "; ".join(offenders[:5])
        if unparsed:
            feedback += f" (plus {len(unparsed)} unreadable verdict(s))"
    else:
        feedback = (
            f"{len(judged)} named person(s) reviewed — all public figures in "
            "public capacity, no reputation-site detail attached."
        )

    review = ReviewerResult(
        reviewer="person_mention",
        approved=not offenders,
        score=round(score, 1),
        feedback=feedback,
        provider="person_mention_gate",
    )

    qa = MultiModelQA(
        pool=pool,
        settings_service=state.get("settings_service"),
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "person_mention")

    if offenders:
        logger.info(
            "[qa.person_mention] %d offender(s): %s",
            len(offenders), "; ".join(offenders[:3]),
        )
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
