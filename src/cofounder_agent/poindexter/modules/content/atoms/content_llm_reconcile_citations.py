"""content.llm_reconcile_citations — grounded-LLM citation repair (#765 follow-up).

Runs AFTER the deterministic ``content.reconcile_citations``, so it only sees the
residual: named-source mentions whose attribution frame the regex grammar can't
match. It asks a structured-extraction LLM for ``{text_span, url}`` link pairs and
``{ungrounded}`` names, then applies ONLY the pairs that survive deterministic
verification — the LLM never edits the prose. Ungrounded mentions become a finding
+ an advisory ``qa_rail_reviews`` entry (never a hard veto by default).

Safety model — "LLM proposes, deterministic code verifies + applies":

- a proposed link is applied only when its ``url`` is verbatim one of the corpus
  URLs (cannot hallucinate a target), its ``text`` occurs verbatim in the draft
  at a span not already inside a markdown link (cannot mangle prose; idempotent);
- the LLM's ``ungrounded`` list is surfaced, never used to mutate prose.

Fail-open: disabled / no corpus / no candidate / LLM error / bad JSON -> no-op
(the deterministic pass already ran; the post always completes).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from modules.content.atoms._citation_match import (
    _STOPWORD_TOKENS,
    CorpusSource,
    _domain_handles,
    _markdown_link_text_spans,
    _overlaps,
    parse_corpus,
)
from modules.content.atoms._pool import resolve_pool
from plugins.atom import AtomMeta, FieldSpec
from services.llm_text import ollama_chat_text, resolve_structured_model
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


def candidate_corpus_sources(
    content: str, sources: list[CorpusSource],
) -> list[CorpusSource]:
    """Corpus sources plausibly cited but unlinked: a brand handle/word appears
    in ``content`` while the source URL does not. Cheap gate to avoid an LLM call
    on drafts with nothing to reconcile.

    Deliberately PERMISSIVE (>=3-char non-stopword title words as needles, so a
    short-word multi-word brand like "Big Sky" is still caught): a false positive
    only wastes one cheap local call, but a false negative silently skips a real
    repair. Correctness beats saving a call.
    """
    if not content or not sources:
        return []
    low = content.lower()
    out: list[CorpusSource] = []
    for src in sources:
        if src.url in content:
            continue  # already linked / present verbatim
        handles = {h for h in _domain_handles(src) if "." not in h}  # sld only
        title_tokens = {
            t for t in re.findall(r"[a-z][a-z0-9]{2,}", src.text)
            if t not in _STOPWORD_TOKENS
        }
        needles = handles | title_tokens
        if any(n in low for n in needles):
            out.append(src)
    return out


def _first_free_occurrence(
    content: str, text: str, taken: list[tuple[int, int]],
) -> int | None:
    """First index of ``text`` in ``content`` whose span overlaps none of
    ``taken`` (existing markdown-link text spans + already-chosen spans)."""
    idx = content.find(text)
    while idx != -1:
        if not _overlaps(idx, idx + len(text), taken):
            return idx
        idx = content.find(text, idx + 1)
    return None


def apply_verified_links(
    content: str, pairs: list[dict], corpus_urls: set[str],
) -> tuple[str, list[dict]]:
    """Apply LLM-proposed link pairs, keeping only the safe ones.

    A pair ``{"text", "url"}`` is applied only when: ``url`` is verbatim in
    ``corpus_urls`` (no hallucinated targets); ``text`` occurs verbatim in
    ``content`` at a span NOT already inside a markdown link (no prose mangling,
    idempotent). First unlinked occurrence; edits applied right-to-left so earlier
    offsets stay valid. Returns ``(new_content, applied)`` in document order.
    """
    if not content or not pairs:
        return content, []
    link_spans = _markdown_link_text_spans(content)
    edits: list[tuple[int, int, str, str]] = []
    used: list[tuple[int, int]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        text = (pair.get("text") or "").strip()
        url = (pair.get("url") or "").strip()
        if not text or url not in corpus_urls:
            continue
        start = _first_free_occurrence(content, text, link_spans + used)
        if start is None:
            continue
        end = start + len(text)
        used.append((start, end))
        edits.append((start, end, text, url))
    if not edits:
        return content, []
    new = content
    for start, end, text, url in sorted(edits, key=lambda e: e[0], reverse=True):
        new = f"{new[:start]}[{text}]({url}){new[end:]}"
    applied = [
        {"text": t, "url": u}
        for _s, _e, t, u in sorted(edits, key=lambda e: e[0])
    ]
    return new, applied


ATOM_META = AtomMeta(
    name="content.llm_reconcile_citations",
    type="atom",
    version="1.0.0",
    description=(
        "Grounded-LLM citation repair (#765): after the deterministic pass, asks "
        "a structured-extraction model for {text,url} link pairs + ungrounded "
        "names; applies only corpus-verified verbatim spans (the LLM never edits "
        "prose); ungrounded -> finding + advisory qa_rail_reviews. Fail-open."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to repair"),
        FieldSpec(
            name="research_context", type="str",
            description="research corpus the writer used", required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="content", type="str",
            description="body with corpus-verified links applied",
        ),
        FieldSpec(
            name="qa_rail_reviews", type="list[dict]",
            description="advisory ungrounded-citation review",
        ),
    ),
    requires=("content",),
    produces=("content", "qa_rail_reviews"),
    # Lab/router observability only; the concrete model resolves via the
    # citation_reconcile_llm_model pin (empty -> structured_extraction_model).
    capability_tier="budget",
    cost_class="compute",
    idempotent=True,  # already-linked spans are skipped
    side_effects=(
        "calls the LLM to match named sources to the corpus",
        "emits findings for ungrounded named sources",
    ),
    parallelizable=False,
)

_PROMPT_KEY = "atoms.content.llm_reconcile_citations"
_PROMPT_FALLBACK = """\
You are a citation auditor. Below is an article and a list of research SOURCES
(name and URL). Find every place the article refers to one of these SOURCES **as
the source of a claim or framing** (e.g. "X says", "according to X", "what X
calls", "an X piece argues") but does NOT already link it.

Return ONLY compact JSON, no prose, no code fence:
{{"links":[{{"text":"<exact verbatim phrase from the article naming the source>","url":"<the matching SOURCE url, copied verbatim>"}}],
 "ungrounded":["<name of any source the article attributes a claim to that is NOT in the SOURCES list>"]}}

Rules:
- Use ONLY urls copied verbatim from the SOURCES list. Never invent a url.
- "text" MUST be an exact substring of the article (do not paraphrase).
- Only source-attribution mentions — ignore names mentioned in passing.
- If nothing matches, return {{"links":[],"ungrounded":[]}}.

SOURCES:
{sources}

ARTICLE:
{content}
"""


def _resolve_prompt(*, sources: str, content: str) -> str:
    """Langfuse/SKILL.md-configurable prompt, inline fallback for bootstrap/test."""
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _PROMPT_KEY, sources=sources, content=content,
        )
    except Exception as exc:  # noqa: BLE001 — registry unreachable (bootstrap/test)
        logger.warning(
            "[llm_reconcile_citations] prompt lookup failed (%s) — inline fallback",
            exc,
        )
        return _PROMPT_FALLBACK.format(sources=sources, content=content)


def _parse_llm_json(raw: str) -> dict:
    """Defensive parse: take the outer ``{...}`` and json.loads it. Returns ``{}``
    on any failure (fail-open — the reasoning/fence artifacts are stripped by
    ollama_chat_text upstream, this is belt-and-suspenders)."""
    if not raw:
        return {}
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _sources_block(sources: list[CorpusSource]) -> str:
    return "\n".join(f"- {s.title or s.url}: {s.url}" for s in sources)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = state.get("content") or ""
    site_config = state.get("site_config")
    if not content.strip() or site_config is None:
        return {}
    try:
        if not site_config.get_bool("citation_reconcile_llm_enabled", True):
            return {}
        max_chars = site_config.get_int("citation_reconcile_llm_max_content_chars", 24000)
    except Exception as exc:  # noqa: BLE001 — config read must never break the pipeline
        logger.warning(
            "[llm_reconcile_citations] config read failed (%s) — skipping", exc,
        )
        return {}
    if len(content) > max_chars:
        return {}

    sources = parse_corpus(state.get("research_context") or "")
    candidates = candidate_corpus_sources(content, sources)
    if not candidates:
        return {}  # nothing plausibly unlinked -> no LLM call (cost gate)

    # One structured-extraction call. Fail-open on any error — the deterministic
    # content.reconcile_citations already ran, so this is an advisory enhancement.
    try:
        pin = (site_config.get("citation_reconcile_llm_model", "") or "").strip() or None
        model = resolve_structured_model(pin, site_config=site_config)
        prompt = _resolve_prompt(sources=_sources_block(sources), content=content)
        raw = await ollama_chat_text(
            prompt,
            model=model,
            site_config=site_config,
            pool=resolve_pool(state, atom="content.llm_reconcile_citations"),
            tier="budget",
            timeout_setting="citation_reconcile_llm_timeout_seconds",
            timeout_default=60.0,
            task_id=state.get("task_id"),
            phase="llm_reconcile_citations",
            think=False,
        )
    except Exception as exc:  # noqa: BLE001 — advisory enhancement, never break the pipeline
        logger.warning("[llm_reconcile_citations] LLM call failed (%s) — no-op", exc)
        return {}

    parsed = _parse_llm_json(raw)
    corpus_urls = {s.url for s in sources}
    new_content, applied = apply_verified_links(
        content, parsed.get("links") or [], corpus_urls,
    )
    ungrounded = [
        str(n).strip() for n in (parsed.get("ungrounded") or []) if str(n).strip()
    ]

    result: dict[str, Any] = {}
    if new_content != content:
        result["content"] = new_content
        logger.info(
            "[llm_reconcile_citations] linked %d source(s) (task=%s): %s",
            len(applied), str(state.get("task_id") or "?")[:8],
            ", ".join(a["text"] for a in applied[:5]),
        )
    if ungrounded:
        result["qa_rail_reviews"] = [_build_ungrounded_review(ungrounded)]
        emit_finding(
            source="modules.content.atoms.content_llm_reconcile_citations",
            kind="unlinked_named_sources",
            title=f"{len(ungrounded)} named source(s) cited with no corpus match",
            body=(
                "The writer attributed a claim to named source(s) not present in "
                "the research corpus — verify or soften before publishing:\n- "
                + "\n- ".join(ungrounded)
            ),
            severity="warn",
            dedup_key=f"unlinked_named_sources:{state.get('task_id') or '?'}",
            extra={"task_id": state.get("task_id"), "sources": ungrounded},
        )
    return result


def _build_ungrounded_review(ungrounded: list[str]) -> dict:
    """Advisory ReviewerResult (as a channel dict) for ungrounded named sources.

    Intentionally ALWAYS advisory — there is deliberately NO gate-config lookup
    that could flip it to a hard veto. This rail only emits a review when it
    DETECTS ungrounded mentions; it is silent on clean drafts and on the
    disabled / no-candidate / LLM-error fail-open paths. A *required* gate
    combined with a sometimes-silent rail would trip qa.aggregate's vacuous-pass
    guard (``missing_required_gates``) and hard-reject every clean post. So the
    rail is advisory by construction; the seeded ``qa_gates.citation_grounding``
    row (``required_to_pass=false``) exists for run-counter telemetry + dashboard
    visibility (via ``qa_gates_db_writer.record_chain_run``), not graduation."""
    from modules.content.atoms._qa_rail_common import reviewer_to_dict
    from modules.content.multi_model_qa import ReviewerResult

    penalty = 6
    score = float(max(60, 100 - penalty * len(ungrounded)))
    return reviewer_to_dict(ReviewerResult(
        reviewer="citation_grounding",
        approved=False,
        score=score,
        feedback=(
            f"{len(ungrounded)} named source(s) with no corpus match: "
            + "; ".join(ungrounded[:5])
        ),
        provider="citation_grounding",
        advisory=True,  # advisory by construction — see docstring
    ))


__all__ = [
    "ATOM_META",
    "apply_verified_links",
    "candidate_corpus_sources",
    "run",
]
