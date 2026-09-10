"""seo.generate_all_metadata — single structured LLM call for all SEO metadata.

Replaces the three-atom serial chain (seo.generate_title → seo.generate_description
→ seo.extract_keywords) in the canonical_blog graph_def with one call that
returns ``{title, description, keywords}`` as a JSON object.

The three original atoms are retained as standalone importable units; this atom
simply collapses them into one LLM round-trip so the pipeline saves ~2 min/post.

Fallback behaviour mirrors the individual atoms: on LLM failure or a missing
JSON field, each field falls back to its programmatic derivation independently.
On JSON parse failure, ALL three fields fall back to programmatic derivations
(logged as a single degradation event).

Topic-echo guard (2026-07-24): the seo_title must never be the raw topic /
assignment directive parroted back (task 1afabaf9 shipped seo_title = "Expand
coverage of the Insights category — only Insights (3)"). An echoed title gets
one corrective LLM retry, then the canonical-title/H1 fallback; only when no
non-echo candidate exists does the echo ship — with a ``warn``
``seo_title_topic_echo`` finding so the operator retitles before approving.

Issue: Glad-Labs/poindexter#734.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from modules.content.atoms import _seo_common as sc
from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from utils.json_extract import extract_json_object
from utils.title_utils import derive_seo_title

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="seo.generate_all_metadata",
    type="atom",
    version="1.0.0",
    description=(
        "Single structured LLM call that produces seo_title + seo_description + "
        "seo_keywords in one round-trip, replacing the three serial seo.* atoms "
        "(saves ~2 min/post). Each field degrades independently to its programmatic "
        "fallback on parse or LLM failure."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="finished draft"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="tags", type="list[str]", description="tags; tags[0] is the primary keyword", required=False),
        FieldSpec(name="seo_title", type="str", description="pre-existing seo_title if any (used as fallback input to description/keywords)", required=False),
    ),
    outputs=(
        FieldSpec(name="seo_title", type="str", description="<=60 char SEO title"),
        FieldSpec(name="seo_description", type="str", description="<=160 char meta description"),
        FieldSpec(name="seo_keywords", type="str", description="comma-joined keywords"),
        FieldSpec(name="seo_keywords_list", type="list[str]", description="structured keywords"),
        FieldSpec(name="stages", type="dict", description="sets 4_seo_metadata_generated"),
    ),
    requires=("content",),
    produces=("seo_title", "seo_description", "seo_keywords", "seo_keywords_list", "stages"),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    retry=RetryPolicy(max_attempts=2, backoff_s=2.0, retry_on=("HTTPError", "TimeoutException", "ConnectError")),
    parallelizable=False,
)

_MIN_KEYWORDS = 3
_MAX_KEYWORDS = 10


def _parse_keywords(raw: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,\n]", raw or ""):
        kw = part.strip().lstrip("-*0123456789. ").strip().lower()
        if kw:
            out.append(kw)
    return out


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from ``text`` — delegate to the canonical
    :func:`utils.json_extract.extract_json_object` ladder (kept under this
    name because the atom's tests reference it here)."""
    return extract_json_object(text)


def _build_keywords(raw_kw: str, state: dict[str, Any], seo_title: str) -> list[str]:
    """Parse + sanitise keywords from the structured response, backfilling
    from the frequency extractor to honour the 3-keyword floor."""
    content = (state.get("content") or "").lower()
    haystack = content + " " + seo_title.lower()
    seen: set[str] = set()
    kws: list[str] = []
    for kw in _parse_keywords(raw_kw):
        if kw in seen:
            continue
        if all(tok in haystack for tok in kw.split()):  # anti-hallucination
            seen.add(kw)
            kws.append(kw)
        if len(kws) >= _MAX_KEYWORDS:
            break
    if len(kws) < _MIN_KEYWORDS:
        for kw in sc.fallback_keywords(state, count=_MAX_KEYWORDS):
            k = kw.lower()
            if k not in seen:
                seen.add(k)
                kws.append(k)
            if len(kws) >= _MIN_KEYWORDS:
                break
    if not kws:
        kws = [k.lower() for k in sc.fallback_keywords(state)]
    return kws[:_MAX_KEYWORDS]


def _finish(title: str, description: str, keywords: list[str], state: dict[str, Any]) -> dict[str, Any]:
    stages = dict(state.get("stages") or {})
    stages["4_seo_metadata_generated"] = True
    return {
        "seo_title": title,
        "seo_description": description,
        "seo_keywords": ", ".join(keywords),
        "seo_keywords_list": keywords,
        "stages": stages,
    }


# Corrective instruction appended to the prompt when the model echoed the
# assignment topic back as the seo_title (the 2026-07-24 failure: seo_title =
# "Expand coverage of the Insights category — only Insights (3)").
_ECHO_RETRY_SUFFIX = (
    "\n\n⚠️ Your previous response copied the TOPIC assignment label verbatim "
    "as the title. The TOPIC is an internal work directive, never a headline. "
    "Write the title from the ARTICLE text itself — what it claims, covers, "
    "and concludes."
)


def _emit_echo_finding(state: dict[str, Any], *, title: str, topic: str, healed: bool) -> None:
    """Surface a topic-echo event on the Findings board.

    ``healed=True`` (retry/fallback produced a non-echo title) is an ``info``
    breadcrumb; ``healed=False`` (the echoed string is what ships to the
    operator's approval queue) is a ``warn`` so it reaches Discord — the
    operator must retitle before approving.
    """
    from utils.findings import emit_finding

    task_id = str(state.get("task_id") or "unknown")
    emit_finding(
        source="seo.generate_all_metadata",
        kind="seo_title_topic_echo",
        title=(
            "seo_title echoed the raw topic"
            + ("" if healed else " — operator must retitle")
        ),
        body=(
            f"Task {task_id}: the metadata LLM returned the assignment topic "
            f"as the seo_title.\n\ntopic: {topic!r}\nseo_title: {title!r}\n\n"
            + (
                "A corrective retry / canonical-title fallback replaced it."
                if healed
                else "No non-echo candidate existed (corrective retry still "
                "echoed and no canonical title/H1 was available) — the echoed "
                "string is the seo_title on this draft. Retitle before "
                "approving."
            )
        ),
        severity="info" if healed else "warn",
        dedup_key=f"seo_title_topic_echo:{task_id}",
        extra={"task_id": task_id, "healed": healed},
    )


def _non_echo_fallback_title(state: dict[str, Any], topic: str) -> str | None:
    """Best non-echo programmatic title candidate: the canonical display title
    (``state['title']``), else the draft's H1. ``None`` when neither exists or
    both are themselves topic echoes."""
    from services.title_generation import extract_h1_title

    canonical = str(state.get("canonical_title") or state.get("title") or "").strip()
    if canonical:
        candidate = derive_seo_title(canonical, max_len=60)
        if candidate and not sc.is_topic_echo(candidate, topic):
            return candidate
    h1 = extract_h1_title(state.get("content") or "")
    if h1:
        candidate = derive_seo_title(h1, max_len=60)
        if candidate and not sc.is_topic_echo(candidate, topic):
            return candidate
    return None


async def _guard_topic_echo(
    state: dict[str, Any],
    *,
    title: str,
    topic: str,
    primary_keyword: str,
    content_digest: str,
    llm_retry: bool,
) -> str:
    """Never let the raw topic/directive ship as seo_title.

    When ``title`` echoes ``topic``: one corrective LLM retry (skipped on the
    degraded paths where the LLM already failed), then the canonical-title/H1
    fallback, and only if nothing non-echo exists keep the echo — loudly
    (``warn`` finding → Discord; the operator must retitle before approving).
    """
    if not sc.is_topic_echo(title, topic):
        return title

    if llm_retry:
        try:
            raw = await sc.run_seo_llm(
                state,
                "atoms.seo.generate_all_metadata",
                topic=topic,
                primary_keyword=primary_keyword,
                content=content_digest,
                max_attempts=1,
                prompt_suffix=_ECHO_RETRY_SUFFIX,
            )
            parsed = _extract_json(raw) or {}
            retry_raw_title = str(parsed.get("title") or "").strip()
            if retry_raw_title:
                retry_title = derive_seo_title(
                    sc.clean_oneline(retry_raw_title), max_len=60
                )
                if retry_title and not sc.is_topic_echo(retry_title, topic):
                    logger.info(
                        "[seo.generate_all_metadata] topic-echo corrected on "
                        "retry: %r → %r",
                        title, retry_title,
                    )
                    _emit_echo_finding(state, title=title, topic=topic, healed=True)
                    return retry_title
        except Exception as exc:  # noqa: BLE001 — the guard must never crash the atom
            logger.warning(
                "[seo.generate_all_metadata] topic-echo corrective retry "
                "failed (%s: %s) — falling back to canonical title/H1",
                type(exc).__name__, exc,
            )

    fallback = _non_echo_fallback_title(state, topic)
    if fallback:
        logger.warning(
            "[seo.generate_all_metadata] seo_title echoed the topic %r — "
            "replaced with canonical-title/H1 fallback %r",
            topic, fallback,
        )
        _emit_echo_finding(state, title=title, topic=topic, healed=True)
        return fallback

    logger.warning(
        "[seo.generate_all_metadata] seo_title echoes the topic %r and no "
        "non-echo candidate exists — shipping the echo, operator must retitle",
        topic,
    )
    _emit_echo_finding(state, title=title, topic=topic, healed=False)
    return title


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    from services.title_generation import (
        DEFAULT_TITLE_EXCERPT_CHARS,
        build_title_grounding_digest,
    )

    topic = state.get("topic") or ""
    # tags[0] → content-derived keyword → topic. Never the raw topic when the
    # article itself can supply a keyword (2026-07-24 topic-echo fix).
    primary_keyword = sc.resolve_primary_keyword(state)

    # Article grounding: opening excerpt + full-article section headings, the
    # same digest (and the same size knob) the display-title prompt uses.
    try:
        excerpt_chars = site_config.get_int(
            "title_content_excerpt_chars", DEFAULT_TITLE_EXCERPT_CHARS
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        excerpt_chars = DEFAULT_TITLE_EXCERPT_CHARS
    content_digest = build_title_grounding_digest(content, max_chars=excerpt_chars)

    try:
        raw = await sc.run_seo_llm(
            state,
            "atoms.seo.generate_all_metadata",
            topic=topic,
            primary_keyword=primary_keyword,
            content=content_digest,
            max_attempts=ATOM_META.retry.max_attempts,
            backoff_s=ATOM_META.retry.backoff_s,
        )
    except Exception as exc:  # noqa: BLE001 — degrade all fields, never propagate
        sc.degraded("all_metadata", exc)
        title = sc.fallback_title(state)
        title = await _guard_topic_echo(
            state, title=title, topic=topic, primary_keyword=primary_keyword,
            content_digest=content_digest, llm_retry=False,
        )
        desc = sc.fallback_description(state)
        kws = [k.lower() for k in sc.fallback_keywords(state)]
        return _finish(title, desc, kws, state)

    parsed = _extract_json(raw)

    if parsed is None:
        logger.warning(
            "[seo.generate_all_metadata] could not parse JSON from LLM output; "
            "degrading all fields to programmatic fallbacks. raw=%r",
            raw[:200],
        )
        sc.degraded("all_metadata_json_parse", Exception("JSON parse failed"))
        title = sc.fallback_title(state)
        title = await _guard_topic_echo(
            state, title=title, topic=topic, primary_keyword=primary_keyword,
            content_digest=content_digest, llm_retry=False,
        )
        desc = sc.fallback_description(state)
        kws = [k.lower() for k in sc.fallback_keywords(state)]
        return _finish(title, desc, kws, state)

    # --- title ---
    raw_title = str(parsed.get("title") or "").strip()
    if raw_title:
        title = derive_seo_title(sc.clean_oneline(raw_title), max_len=60)
    else:
        title = sc.fallback_title(state)
    # Topic-echo guard: the model must never ship the assignment label as the
    # seo_title (2026-07-24, task 1afabaf9). Corrective retry → canonical
    # title/H1 fallback → loud finding.
    title = await _guard_topic_echo(
        state, title=title, topic=topic, primary_keyword=primary_keyword,
        content_digest=content_digest, llm_retry=True,
    )

    # --- description ---
    raw_desc = str(parsed.get("description") or "").strip()
    desc = sc.clamp_words(raw_desc, 160) if raw_desc else sc.fallback_description(state)

    # --- keywords ---
    raw_kw = str(parsed.get("keywords") or "").strip()
    kws = _build_keywords(raw_kw, state, title) if raw_kw else [k.lower() for k in sc.fallback_keywords(state)]

    return _finish(title, desc, kws, state)


__all__ = ["ATOM_META", "run"]
