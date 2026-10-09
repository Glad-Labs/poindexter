"""Ground a draft's first-person claims in our own records (prototype).

``qa.self_claim`` checks claims about our system against STRUCTURED records:
versions, settings keys, file paths, the stack list, the host's specs. An
invented anecdote has no structured record to check: "we moved the library to
a slower PCIe 4 drive and noticed nothing" and "when we looked at moving to
vLLM, the benchmarks said no" both reached the approval queue in October 2026
with the rail reading "no claims to check" and 100. A detector that flagged
first-person claims missing from the post's ``research_context`` was measured
and rejected (24% of published posts, almost all TRUE claims): the research
bundle is about the topic, not about what we did.

What does record what we did is the embedded corpus: work sessions, memory
notes, the brain, issues and the audit log. Both fabrications above were
settled by hand in minutes by searching it. This module does that per claim:

1. :func:`extract_experiential_claims` — first-person sentences that report
   something we did, saw or measured (deterministic).
2. :func:`retrieve_evidence` — hybrid retrieval (pgvector + tsvector, fused
   with RRF) over the PRIMARY tables only. Published ``posts`` are excluded:
   a post is a claim, not evidence, and a fabrication that shipped once would
   otherwise confirm the next draft. Evidence must predate the draft
   (``before``): our own review sessions quote the draft verbatim, and an
   echo of the claim is not a record of the event. Near-verbatim echoes are
   dropped as well.
3. :func:`judge_claim` — one small JSON call (``qa.self_claim_grounding``):
   supported / contradicted / no_evidence, with the deciding record.

``qa.self_claim`` calls it as layer 9 (``qa_self_claim_grounding_mode``,
default ``note``: unbacked claims are listed in the review feedback, nothing
else changes). Measure any change with ``scripts/eval_self_claim_grounding.py``
before trusting it: on 2026-10-09 it caught 4 of 4 invented anecdotes and
passed 4 of 4 true ones in a labelled draft, and flagged 3 of the 20 newest
published posts (from 14 of 20 before extraction and retrieval were tightened).
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

PROMPT_KEY = "qa.self_claim_grounding"

# Where what we did is written down. ``posts`` is deliberately absent (above).
# The audit log and the brain were measured out: their machine events crowd
# the top of every ranking and never describe what we did (prototype run 1,
# 2026-10-08: audit/brain rows were the top record for most false flags).
DEFAULT_EVIDENCE_TABLES: tuple[str, ...] = ("claude_sessions", "memory", "issues")
DEFAULT_TOP_K = 8
_CANDIDATES_PER_LIST = 25
_RRF_K = 60
_EXCERPT_CHARS = 900
# A snippet whose best-matching window is this similar to the claim is the
# claim being quoted, not a record of the event.
_ECHO_RATIO = 0.82

# Case-sensitive on purpose: "US model makers" is a country, and "I" is only
# ever the capital pronoun (round 2 extracted a Register headline via "US").
_FIRST_PERSON_RE = re.compile(
    r"\b(?:[Ww]e|[Ww]e've|[Ww]e'd|[Ww]e're|[Oo]ur|[Oo]urs|us|I|I've|I'd|I'm|[Mm]y)\b",
)
# Quoted speech is someone else talking: "our tool doesn't compete with novels"
# was a hypothetical company's line, not ours.
_QUOTED_RE = re.compile(r"\"[^\"]*\"|“[^”]*”")
# Verbs that report an event we took part in. Past tense on purpose: "we run
# Ollama" is a standing fact (qa.self_claim's capability layer owns it);
# "we moved", "we tried", "it took" report something that happened once.
_EVENT_VERBS = (
    "added", "benchmarked", "broke", "built", "caught", "changed", "chased",
    "checked", "chose", "compared", "copied", "cut", "decided", "deleted",
    "deployed", "did", "discovered", "dropped", "fixed", "found", "got", "hit", "learned", "looked", "lost", "measured", "migrated",
    "missed", "moved", "noticed", "passed", "picked", "pointed", "ran",
    "rejected", "removed", "replaced", "rewrote", "saw", "shipped", "skipped",
    "spent", "started", "stopped", "switched", "swapped", "tested", "took",
    "traced", "tracked", "tried", "turned", "upgraded", "watched", "went",
    "wired", "wrote",
)
_VERBS = "|".join(_EVENT_VERBS)
# The event verb must be OURS: "we moved", "we recently fixed", "our topic
# source ran", "it took us". A sentence where someone else acts and "we" only
# comments ("the team at Hugging Face just shipped a fix ... a pattern we keep
# running into") reports no event of ours; that shape was a third of the
# published-post flags on 2026-10-09.
_OUR_EVENT_RE = re.compile(
    rf"\b(?:[Ww]e|I)(?:'ve|'d)?\s+(?:[\w-]+\s+){{0,2}}?(?:{_VERBS})\b"
    rf"|\b[Oo]ur\s+(?:[\w-]+\s+){{0,4}}?(?:{_VERBS})\b"
    rf"|\b(?:{_VERBS})\s+(?:us|me)\b",
)
# A disclaimer ("we haven't benchmarked them ourselves") or an aside comparing
# the reader to us ("if you've put a router in front, the way we did") is not
# an anecdote a record could hold or an invention worth flagging.
_DISCLAIMER_RE = re.compile(
    r"\b(?:we|i)\s+(?:haven't|have not|hadn't|didn't|did not|never|don't|do not|"
    r"can't|cannot|couldn't|won't|wouldn't)\b"
    r"|\b(?:the way|just as|like)\s+we\s+did\b",
    re.IGNORECASE,
)
# Reader-inclusive "we" ("if we", "let's") is advice, not a report. "When"
# is not here: "When we looked at moving to vLLM, the benchmarks said no" is a
# past-tense report (the event verb already filters "when we need ...").
_HYPOTHETICAL_RE = re.compile(
    r"^\s*(?:if|unless|suppose|imagine|let's|let us)\b", re.IGNORECASE,
)
# Pointers at our own writing ("we wrote up the 27B variant", "we covered it in
# ...") are backed by the linked post, and posts are not evidence here.
_OWN_WRITING_RE = re.compile(
    r"\b(?:we|i)\s+(?:also\s+)?(?:wrote|covered|published|posted)\s+(?:up|about|more|on|it|this|that)\b"
    r"|\bour\s+(?:piece|post|article|write-?up|postmortem)\s+(?:on|about)\b",
    re.IGNORECASE,
)
# A link to one of our own posts: the sentence summarises that post, and the
# post was checked when it was written ("(see Fixing the GPU lock and taming
# the internal RAG sweep)"). Five of the twenty published-post flags on
# 2026-10-09 were sentences like this.
_INTERNAL_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((?:/posts/|https?://(?:www\.)?gladlabs\.io/)[^)]*\)",
)
# Something a record could hold: a number, a code-ish token (phi4:14b,
# poetry.lock, PR #4231) or a mid-sentence proper noun (Steam, vLLM, Ollama).
# "We'd built the engine and forgotten to bolt it to the car" has none, and a
# judge can only answer "no evidence" for it (prototype run 1: most of the 90%).
_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|"
    "thirty|hundred|thousand|dozen|half|twice|double|doubled|triple"
)
_ANCHOR_RE = re.compile(
    r"\d|[A-Za-z]+[_.:/#][A-Za-z0-9]|(?<=[a-z,;:] )[A-Z][A-Za-z0-9]+|\b[a-z]+[A-Z][A-Za-z]*\b"
    rf"|\b(?:{_NUMBER_WORDS})\b",
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_WORD_RE = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")


@dataclass
class Claim:
    sentence: str
    context: str  # the sentence before it, for the judge

    @property
    def text(self) -> str:
        return f"{self.context} {self.sentence}".strip()


@dataclass
class Evidence:
    ref: str  # "<source_table>:<source_id>"
    created_at: datetime | None
    excerpt: str
    rrf: float


@dataclass
class Grounding:
    claim: Claim
    verdict: str  # supported | contradicted | no_evidence | vague | error
    record: str = ""
    missing: str = ""
    quote: str = ""
    # The judge's own verdict when the quote check overruled it.
    judge_verdict: str = ""
    evidence: list[Evidence] = field(default_factory=list)


def _plain_text(markdown: str) -> str:
    text = re.sub(r"<img[^>]*>", " ", markdown or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [text](url) -> text
    text = re.sub(r"<https?://[^>]+>", " ", text)
    text = re.sub(r"^\s*#+\s.*$", " ", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def extract_experiential_claims(content: str) -> list[Claim]:
    """First-person sentences that report an event (``we moved``, ``it took``).

    A sentence qualifies when it has a first-person word and an event verb,
    and does not open as a hypothetical. The sentence before it is carried as
    context: claims often lean on it ("We did exactly that with a 680 GiB
    library." means nothing alone).
    """
    own_posts = {
        " ".join(m.split()) for m in _INTERNAL_LINK_RE.findall(content or "") if len(m.split()) >= 2
    }
    sentences = [s.strip() for s in _SENTENCE_RE.split(_plain_text(content)) if s.strip()]
    claims: list[Claim] = []
    for i, sentence in enumerate(sentences):
        unquoted = _QUOTED_RE.sub(" ", sentence)
        if not _FIRST_PERSON_RE.search(unquoted) or not _OUR_EVENT_RE.search(unquoted):
            continue
        if (
            _HYPOTHETICAL_RE.match(sentence)
            or _OWN_WRITING_RE.search(sentence)
            or _DISCLAIMER_RE.search(sentence)
            or any(title in sentence for title in own_posts)
        ):
            continue
        if not _ANCHOR_RE.search(sentence):
            continue
        claims.append(Claim(sentence=sentence, context=sentences[i - 1] if i else ""))
    return claims


def _terms(text: str) -> list[str]:
    stop = {
        "the", "and", "for", "that", "with", "this", "was", "were", "are", "our",
        "we", "you", "your", "its", "it", "then", "than", "but", "not", "into",
        "from", "had", "has", "have", "about", "what", "when", "which", "who",
        "they", "them", "their", "there", "here", "just", "only", "one", "all",
    }
    return [w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 2 and w not in stop]


def is_echo(claim_sentence: str, snippet: str, *, ratio: float = _ECHO_RATIO) -> bool:
    """Does ``snippet`` quote the claim rather than record the event?"""
    claim = " ".join(_terms(claim_sentence))
    words = _terms(snippet)
    n = len(claim.split())
    if n < 5 or len(words) < n:
        return False
    for start in range(0, len(words) - n + 1):
        window = " ".join(words[start:start + n])
        if difflib.SequenceMatcher(None, claim, window).ratio() >= ratio:
            return True
    return False


_MAX_KEYWORD_TERMS = 16


def keyword_terms(sentence: str) -> list[tuple[str, int]]:
    """``(tsquery phrase, weight)`` for each content word of the claim.

    Anchor words weigh 2: a number, a code-ish token (``qwen3-vl``,
    ``model-eval``) or a word capitalised mid-sentence (``Corsair``,
    ``DeepEval``). Records are ranked by the summed weight of the words they
    contain, so the session that says "chatterbox restarted 507 times" outranks
    one that merely shares "restarted" and "board". An OR over every term,
    ranked by ``ts_rank_cd``, ranked long sessions full of common words first
    and missed three true events on 2026-10-09 whose records were in the corpus.
    """
    capitalised = {
        w.lower() for w in re.findall(r"(?<=[\w,;:)] )[A-Z][\w.'-]*", sentence or "")
    }
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for term in _terms(sentence):
        parts = re.findall(r"[a-z0-9]+", term)
        if not parts or term in seen:
            continue
        seen.add(term)
        anchor = (
            any(ch.isdigit() for ch in term) or len(parts) > 1 or term in capitalised
            or re.fullmatch(_NUMBER_WORDS, term) is not None
        )
        out.append((" <-> ".join(parts), 2 if anchor else 1))
    out.sort(key=lambda tw: -tw[1])
    return out[:_MAX_KEYWORD_TERMS]


async def retrieve_evidence(
    pool: Any,
    claim: Claim,
    *,
    site_config: Any,
    before: datetime | None,
    tables: tuple[str, ...] = DEFAULT_EVIDENCE_TABLES,
    top_k: int = DEFAULT_TOP_K,
) -> list[Evidence]:
    """The records nearest the claim, by vector and by keyword, fused (RRF)."""
    from poindexter.services.rag_excerpt import excerpt_around_query
    from poindexter.services.topic_ranking import embed_text

    vec = await embed_text(claim.sentence, site_config=site_config)
    vec_str = "[" + ",".join(str(v) for v in vec) + "]"
    cutoff = before or datetime.max
    where = "source_table = ANY($1::text[]) AND created_at < $2"
    async with pool.acquire() as conn:
        by_vector = await conn.fetch(
            "SELECT source_table, source_id, created_at, "
            "COALESCE(chunk_text, text_preview) AS body FROM embeddings "
            f"WHERE {where} ORDER BY embedding <=> $3::vector LIMIT $4",  # nosec B608 — constant SQL
            list(tables), cutoff, vec_str, _CANDIDATES_PER_LIST,
        )
        weighted = keyword_terms(claim.sentence)
        by_keyword = []
        if weighted:
            phrases = [p for p, _ in weighted]
            any_term = " | ".join(f"({p})" for p in phrases)
            by_keyword = await conn.fetch(
                "SELECT source_table, source_id, created_at, body FROM ("
                " SELECT e.source_table, e.source_id, e.created_at,"
                " COALESCE(e.chunk_text, e.text_preview) AS body,"
                " (SELECT COALESCE(sum(t.w), 0) FROM unnest($3::text[], $4::int[]) AS t(q, w)"
                "  WHERE e.text_search @@ to_tsquery('simple', t.q)) AS score,"
                " ts_rank_cd(e.text_search, to_tsquery('simple', $5)) AS rank"
                f" FROM embeddings e WHERE {where} AND e.text_search @@ to_tsquery('simple', $5)"  # nosec B608 — constant SQL
                ") s ORDER BY score DESC, rank DESC LIMIT $6",
                list(tables), cutoff, phrases, [w for _, w in weighted], any_term,
                _CANDIDATES_PER_LIST,
            )

    fused: dict[str, dict[str, Any]] = {}
    for rows in (by_vector, by_keyword):
        for rank, row in enumerate(rows, start=1):
            ref = f"{row['source_table']}:{row['source_id']}"
            entry = fused.setdefault(ref, {"row": row, "rrf": 0.0})
            entry["rrf"] += 1.0 / (_RRF_K + rank)

    out: list[Evidence] = []
    for ref, entry in sorted(fused.items(), key=lambda kv: -kv[1]["rrf"]):
        body = str(entry["row"]["body"] or "")
        if not body.strip() or is_echo(claim.sentence, body):
            continue
        out.append(Evidence(
            ref=ref,
            created_at=entry["row"]["created_at"],
            excerpt=excerpt_around_query(body, claim.sentence, _EXCERPT_CHARS),
            rrf=round(entry["rrf"], 5),
        ))
        if len(out) >= top_k:
            break
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[*_`]", "", text or "").lower().split())


_QUOTE_MATCH_RATIO = 0.85
# A contradiction must be about the same thing. All four "contradicted"
# verdicts on published posts (2026-10-09) quoted a real line from an unrelated
# record: a firefighter dry-run note "contradicted" a true model-eval fix.
_CONTRADICTION_MIN_SHARED_TERMS = 3


def _fuzzy_contains(needle: str, hay: str) -> bool:
    if needle in hay:
        return True
    n = len(needle)
    if n == 0 or len(hay) < n * 0.8:
        return False
    best = 0.0
    step = max(1, n // 6)
    for start in range(0, max(1, len(hay) - n + 1), step):
        window = hay[start:start + n + n // 5]
        best = max(best, difflib.SequenceMatcher(None, needle, window).ratio())
        if best >= _QUOTE_MATCH_RATIO:
            return True
    return False


_RECORD_ID_RE = re.compile(r"[a-z_]+:[^\]\s]+")


def quote_holds(quote: str, record: str, evidence: list[Evidence]) -> bool:
    """Is ``quote`` really in the record the judge cited?

    The grounded-LLM rule (``content.llm_reconcile_citations``): the model
    proposes, code verifies. A decisive verdict must point at words that exist
    in a record it was shown; the first prototype run had the judge mark
    "the benchmarks said no" supported by a record that was a ``git log``
    command. Accepts the record id with or without its brackets.
    """
    q = _norm(quote)
    if len(q.split()) < 3:
        return False
    # The judge echoes the id the way the block shows it, "[memory:x] (2026-08-27)";
    # an exact compare overruled a correct "supported" that way (2026-10-09).
    m = _RECORD_ID_RE.search(record or "")
    rid = m.group(0) if m else ""
    if rid and not any(ev.ref == rid for ev in evidence):
        rid = ""  # an id we never showed: look for the words in every record
    for ev in evidence:
        if rid and ev.ref != rid:
            continue
        # Fuzzy: the judge drops markdown, ellipses and the odd article when it
        # copies (run 1 overruled true support for "Glad Labs indie-devs" that way).
        if _fuzzy_contains(q, _norm(ev.excerpt)):
            return True
    return False


def shares_subject(quote: str, claim_sentence: str) -> bool:
    """Do the quote and the claim name enough of the same things for one to
    contradict the other?"""
    shared = set(_terms(quote)) & set(_terms(claim_sentence))
    return len(shared) >= _CONTRADICTION_MIN_SHARED_TERMS


def _evidence_block(evidence: list[Evidence]) -> str:
    if not evidence:
        return "(no records found)"
    lines = []
    for ev in evidence:
        when = ev.created_at.date().isoformat() if ev.created_at else "undated"
        lines.append(f"[{ev.ref}] ({when}) {' '.join(ev.excerpt.split())}")
    return "\n\n".join(lines)


async def judge_claim(
    claim: Claim,
    evidence: list[Evidence],
    *,
    site_config: Any,
    model: str,
    pool: Any = None,
    prompt_template: str | None = None,
) -> Grounding:
    """Ask the judge whether the records support the claim. Never raises."""
    from poindexter.services.topic_ranking import _ollama_chat_json

    if prompt_template is None:
        from poindexter.services.prompt_manager import get_prompt_manager

        prompt = get_prompt_manager().get_prompt(
            PROMPT_KEY, claim=claim.sentence, context=claim.context or "(none)",
            evidence=_evidence_block(evidence),
        )
    else:
        prompt = prompt_template.format(
            claim=claim.sentence, context=claim.context or "(none)",
            evidence=_evidence_block(evidence),
        )
    try:
        raw = await _ollama_chat_json(prompt, model=model, pool=pool, site_config=site_config)
        parsed = json.loads(raw or "")
        verdict = str(parsed.get("verdict") or "").strip().lower()
        if verdict not in ("supported", "contradicted", "no_evidence", "vague"):
            raise ValueError(f"unknown verdict {verdict!r}")
        record = str(parsed.get("record") or "")
        quote = str(parsed.get("quote") or "")
        judge_verdict = ""
        if verdict in ("supported", "contradicted") and not quote_holds(quote, record, evidence):
            # A decisive verdict without words to show for it is no verdict.
            judge_verdict, verdict = verdict, "no_evidence"
        elif verdict == "contradicted" and not shares_subject(quote, claim.sentence):
            judge_verdict, verdict = verdict, "no_evidence"
        return Grounding(
            claim=claim, verdict=verdict, record=record, quote=quote,
            missing=str(parsed.get("missing") or ""), evidence=evidence,
            judge_verdict=judge_verdict,
        )
    except Exception as exc:  # noqa: BLE001 — a judge failure is reported, never raised
        logger.warning("[self_claim_grounding] judge failed: %s: %s", type(exc).__name__, exc)
        return Grounding(claim=claim, verdict="error", missing=str(exc)[:200], evidence=evidence)


async def ground_draft(
    pool: Any,
    content: str,
    *,
    site_config: Any,
    model: str,
    before: datetime | None,
    tables: tuple[str, ...] = DEFAULT_EVIDENCE_TABLES,
    top_k: int = DEFAULT_TOP_K,
    prompt_template: str | None = None,
    max_claims: int | None = None,
) -> list[Grounding]:
    """Extract the draft's experiential claims and ground each one (the first
    ``max_claims`` of them: each costs an embedding and a judge call)."""
    results: list[Grounding] = []
    claims = extract_experiential_claims(content)
    if max_claims is not None:
        claims = claims[:max(0, max_claims)]
    for claim in claims:
        evidence = await retrieve_evidence(
            pool, claim, site_config=site_config, before=before, tables=tables, top_k=top_k,
        )
        results.append(await judge_claim(
            claim, evidence, site_config=site_config, model=model, pool=pool,
            prompt_template=prompt_template,
        ))
    return results


__all__ = [
    "DEFAULT_EVIDENCE_TABLES",
    "PROMPT_KEY",
    "Claim",
    "Evidence",
    "Grounding",
    "extract_experiential_claims",
    "ground_draft",
    "is_echo",
    "keyword_terms",
    "shares_subject",
    "quote_holds",
    "judge_claim",
    "retrieve_evidence",
]
