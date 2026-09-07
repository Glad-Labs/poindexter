"""Title searchability — does a title carry something a person would type?

Earned 2026-09-07. Search Console for the August 2026 cohort: 18 posts, 3.4
impressions each in their first three weeks, zero clicks — a tenth of the
May/June baseline. The subject mix was not the discriminator (internal-story
posts averaged 5 impressions, external-topic posts 3). The titles were:

    "The Gap Nobody Names"            "The Stuck Task"
    "The five days nobody was watching"
    "Why 'Make More Money' Isn't a Goal -- It's an Antigoal Waiting to Be Defined"

None of these contains a noun anyone searches for, so Google has no query to
match them to. The best-performing August title was "Chatterbox swallows
minus signs" (11 impressions) — because *Chatterbox* is a name people type.
Every page that earned a click in the trailing 60 days names a concrete thing:
"DDR5 6400 vs 8000 on Ryzen 9", "RTX 5090 ... 32GB", "GGUF Q4_K_M", "FastAPI".

This module is the deterministic half of the fix. It does not judge whether a
title is *good*; it answers one narrow question — does the title contain at
least one **searchable entity** — where an entity is any of:

1. a token carrying a digit (``5090``, ``DDR5``, ``Q4_K_M``, ``4-bit``,
   ``2026``, ``53.7%``);
2. a proper noun / product name. Internal capitals (``FastAPI``,
   ``LangGraph``, ``CadQuery``) and ALL-CAPS (``RAG``, ``OWASP``) count
   wherever they sit. A plainly Capitalised word counts only in a
   **sentence-case** title ("Chatterbox swallows minus signs", "Postgres
   vacuum debt") — there the capital is evidence of a name. In a
   **Title Case** title ("The Stuck Task", "Why Solo Developers Should Embrace
   Docker Containers") every word is capitalised by convention, so a plain
   capital proves nothing and ``Docker`` must be admitted by rule 3 instead;
3. a content word from the article's own keyword set — the primary keyword
   and the task's tags (the raw topic only when neither exists, since a
   directive-shaped topic would launder anything) — so a lowercase technical
   term the article is about (``quantization``, ``embeddings``) counts even
   without a capital.

Rule 3 keeps the gate honest for lowercase-heavy technical prose; rules 1-2
are what "Chatterbox" and "5090" satisfy. A title that satisfies none is
*unsearchable* — not necessarily bad, but invisible — and the caller decides
whether to regenerate or merely record a finding.

Pure functions, no I/O: the atom owns settings + regeneration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Words that are capitalised in titles for reasons other than being a name.
# Title Case titles ("The Memory Scaling Question") would otherwise read as
# a string of entities. Sentence-initial words are handled positionally;
# this list catches the rest of the function-word vocabulary.
_STOPWORDS = frozenset(
    """
    a an the and or but nor so yet for of to in on at by with from as into
    onto upon over under between among through during before after above
    below up down out off about against toward towards within without
    is are was were be been being am do does did done have has had having
    can could may might must shall should will would
    i you he she it we they me him her us them my your his its our their
    this that these those what which who whom whose where when why how
    not no yes if then than too very just only also even still already
    all any both each few more most other some such own same
    here there now then once again ever never always
    one two three four five six seven eight nine ten
    new old big small long short high low good bad great best worst
    thing things way ways time times day days week weeks year years
    nobody somebody everybody anyone everyone someone none
    isn't aren't wasn't weren't don't doesn't didn't won't wouldn't can't
    couldn't shouldn't mustn't it's that's there's here's what's who's
    make makes made get gets got go goes went come comes came
    """.split()
)

# Separators that start a new "sentence" inside a title, after which the next
# word is capitalised by convention rather than because it is a name.
_SUBTITLE_SPLIT_RE = re.compile(r"\s*(?::|—|–|\s-\s|\|)\s*")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+'-]*")
_DIGIT_RE = re.compile(r"\d")


@dataclass(frozen=True)
class SearchabilityReport:
    """What the gate found. ``ok`` is the decision; the lists are the why."""

    ok: bool
    entities: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    keyword_terms: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "entities": list(self.entities),
            "reasons": list(self.reasons),
            "keyword_terms": list(self.keyword_terms),
        }


def _strip_token(tok: str) -> str:
    return tok.strip(".,;:!?'\"()[]{}")


def keyword_terms(
    *,
    primary_keyword: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
    topic: str = "",
    min_len: int = 4,
) -> tuple[str, ...]:
    """The article's own vocabulary that counts as an entity when it shows up
    in the title (rule 3). Lowercased, de-duplicated, stopwords and short
    fragments dropped. Order: primary keyword, tags; topic only as the last
    resort when both are empty."""
    seen: list[str] = []
    # The topic is the assignment label — often a directive sentence ("Expand
    # coverage of the Insights category") whose every content word would
    # otherwise launder an unsearchable title through rule 3. It is consulted
    # only when the article supplied no keyword and no tags at all.
    sources = [primary_keyword or "", *(tags or [])]
    if not any(str(x).strip() for x in sources):
        sources = [topic or ""]
    for source in sources:
        for raw in _TOKEN_RE.findall(str(source)):
            tok = _strip_token(raw).lower()
            if len(tok) < min_len and not _DIGIT_RE.search(tok):
                continue
            if tok in _STOPWORDS or tok in seen:
                continue
            seen.append(tok)
    return tuple(seen)


def _has_internal_caps(tok: str) -> bool:
    """FastAPI / LangGraph / iPhone / RAG / OWASP — a name regardless of position."""
    core = tok.replace("-", "").replace(".", "").replace("_", "")
    return len(core) >= 2 and any(c.isupper() for c in core[1:])


def _is_title_case(segments: list[list[str]]) -> bool:
    """True when the title capitalises its content words by convention.

    Looks at every non-initial content word (alpha, ≥3 chars, not a
    stopword) across all segments; Title Case when at least two exist and
    ≥60% of them are capitalised. A sentence-case title has (almost) none.
    """
    content: list[str] = []
    for toks in segments:
        for tok in toks[1:]:
            if tok[:1].isalpha() and len(tok) >= 3 and tok.lower() not in _STOPWORDS:
                content.append(tok)
    if len(content) < 2:
        return False
    capitalised = sum(1 for t in content if t[0].isupper())
    return capitalised / len(content) >= 0.6


def _is_proper_noun(tok: str, *, sentence_initial: bool, title_case: bool) -> bool:
    """Rule 2 — see the module docstring."""
    if not tok or not tok[0].isalpha():
        return False
    if tok.lower() in _STOPWORDS:
        return False
    if _has_internal_caps(tok):
        return True
    if not tok[0].isupper() or title_case:
        return False
    # Sentence case: a Capitalised content word is a name — including the
    # first word ("Chatterbox swallows…"), except a sentence-initial gerund
    # or short verb-shaped opener ("Building a…", "Choosing a…"), which is
    # grammar, not a name.
    if sentence_initial and (tok.lower().endswith("ing") or len(tok) < 4):
        return False
    return True


def find_searchable_entities(
    title: str,
    *,
    keyword_terms: tuple[str, ...] | list[str] = (),
) -> SearchabilityReport:
    """Return every entity the title carries, with the rule that admitted it."""
    title = (title or "").strip()
    if not title:
        return SearchabilityReport(ok=False, reasons=("empty title",))

    kw = tuple(t.lower() for t in keyword_terms)
    entities: list[str] = []
    reasons: list[str] = []

    def _add(tok: str, why: str) -> None:
        if tok not in entities:
            entities.append(tok)
            reasons.append(why)

    segments = [
        [t for t in (_strip_token(r) for r in _TOKEN_RE.findall(seg)) if t]
        for seg in _SUBTITLE_SPLIT_RE.split(title)
    ]
    title_case = _is_title_case(segments)
    for toks in segments:
        for i, tok in enumerate(toks):
            low = tok.lower()
            if _DIGIT_RE.search(tok) and len(tok) >= 2:
                _add(tok, "digit")
            elif _is_proper_noun(tok, sentence_initial=(i == 0), title_case=title_case):
                _add(tok, "proper_noun")
            elif low in kw:
                _add(tok, "keyword")

    return SearchabilityReport(
        ok=bool(entities),
        entities=tuple(entities),
        reasons=tuple(reasons),
        keyword_terms=kw,
    )


def has_searchable_entity(
    title: str,
    *,
    primary_keyword: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
    topic: str = "",
) -> SearchabilityReport:
    """Convenience: build the keyword set and evaluate ``title`` in one call."""
    return find_searchable_entities(
        title,
        keyword_terms=keyword_terms(
            primary_keyword=primary_keyword, tags=tags, topic=topic,
        ),
    )


def render_entity_directive(
    *,
    primary_keyword: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
    topic: str = "",
    rejected_title: str = "",
    heading_terms: list[str] | tuple[str, ...] | None = None,
) -> str:
    """The corrective block appended to the title prompt on regeneration.

    Names the concrete things the article can be found by and says why the
    previous candidate failed. Positive directive (name the thing), not a
    ban list; the specific nouns come from the article, never invented.
    """
    named: list[str] = []
    for term in [primary_keyword or "", *(tags or []), *(heading_terms or [])]:
        term = str(term).strip()
        if term and term.lower() not in {n.lower() for n in named}:
            named.append(term)
    lines = [
        "SEARCHABILITY: a reader finds this article by typing a concrete thing "
        "into a search box — a product, model, tool, library, standard, number, "
        "or the exact technical term the article is about. Put that thing in "
        "the title, spelled the way people type it.",
    ]
    if rejected_title:
        lines.append(
            f"The previous candidate {rejected_title!r} named nothing "
            "searchable — it reads as a mood, not a subject."
        )
    if named:
        lines.append(
            "Concrete terms this article is actually about (use the ones the "
            "article supports): " + "; ".join(named[:8]) + "."
        )
    elif topic:
        lines.append(f"The assignment topic was: {topic!r}.")
    return "\n".join(lines)


__all__ = [
    "SearchabilityReport",
    "find_searchable_entities",
    "has_searchable_entity",
    "keyword_terms",
    "render_entity_directive",
]
