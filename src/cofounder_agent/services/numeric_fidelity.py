"""Numeric fidelity — is every number in the draft traceable to its sources?

Every other anti-hallucination layer we run is a *judgement*: a regex that
recognises fabrication shapes, or an LLM asked whether the draft is faithful.
This one is arithmetic. Given the research corpus the writer was handed, a
number in the prose either reconciles with a number in that corpus or it does
not, and the answer is checkable without asking anyone.

That makes it the highest-precision net we have for the single most damaging
fabrication class — the invented statistic. A reader forgives a clumsy
sentence; they do not forgive "235 tokens/second" when the source says 55.

**Deliberately narrow, because precision is the whole value.** A rail that
flags legitimate prose gets switched off, and a switched-off rail catches
nothing. So only *checkable forms* are extracted — percentages, currency,
quantities carrying a known unit, thousands-separated integers, and
multipliers. A bare ``3`` in "three ways to..." is not a measurement and is
never flagged. Identifiers that merely contain digits (``qwen2.5:7b``,
``RTX 5090``, ``Wan2.2``) are excluded structurally, not by blocklist: a digit
run glued to a preceding letter is a name, not a quantity.

**Attribution is the scoring gate, and that came from measurement, not
taste.** Run over 12 real published posts, "every checkable number must
appear in the research corpus" flagged 33% of what it found, and every flag
was wrong: a hypothetical in quotes ("rate limit resets every 60 seconds"), a
rhetorical target ("$10K MRR"), and a claim about our own repo, which is
``qa.self_claim``'s corpus, not this one. Digit-bearing sentences in general
prose are overwhelmingly years, image URLs and figures of speech — the single
genuine measurement in that sample was *"responses from 23,262 developers"*.

That is the shape worth checking: a number the draft presents as **sourced
fact**. So a claim is SCORED only when its sentence carries an attribution
signal — "according to", "the survey found", a citation link, a named report.
Everything else is still extracted and reported, but cannot fail the rail.
Unattributed numbers are the author's own framing; attributed ones are
promises about someone else's data, and those are the ones a reader can catch
us getting wrong.

**Matching respects the precision the author wrote.** A draft saying "235"
reconciles against a source value of 235.1, because rounding a source figure
for prose is normal writing, not fabrication. The rule is
``round(source, decimals_written) == claimed``.

**Derivation is OFF by default, and the measurement is why.** Prose routinely
states a figure the corpus implies rather than contains — "an 80% tax" from
124.7 and 25.3 — so the library can satisfy a claim from four relations over
*pairs* of corpus numbers, recording the winning relation in the verdict.

That feature does not survive contact with a real corpus. Against a 35-value
fact block, **81 of 99 invented percentages (1%..99%) were "explained" by some
pair** — including a fabricated 91% matched as ``1 - 243/2756``, a call count
over a millisecond figure. The arithmetic is sound and the explanation is
recorded, but with N corpus values there are ~3N² candidate results competing
for ~100 two-digit buckets, so a collision is near-certain and the rail goes
blind to exactly the fabrication it exists to catch.

Measured cost of switching it off: across 40 published posts, **one** claim was
satisfied only by derivation. One extra false positive per 40 posts buys back
an 82% blind spot. ``allow_derived=True`` remains available for a small,
unit-homogeneous corpus; it is not sound over free text, and making it sound
needs unit-tagged operands, not a bigger pair budget.

Pure and I/O-free by design, so the extraction rules, the rounding contract
and the derivation search are all unit-testable without a database, a browser
or an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Digit runs fused to a preceding letter are identifiers, not quantities:
# qwen2.5, phi4, Wan2.2, GPT-4o, H100. Checked against the character
# immediately before the match so no name blocklist has to be maintained.
_IDENTIFIER_PREFIX = re.compile(r"[A-Za-z]$")

# Uncheckable regions, stripped before extraction. Numbers inside code, URLs
# and link targets are syntax, not claims.
_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_MD_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+|www\.\S+")
_HTML_TAG = re.compile(r"<[^>]+>")
# ISO dates, y-m-d and common written dates — time references, not measurements.
_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    re.IGNORECASE,
)
# Bare 4-digit years anywhere in prose — "in 2026", "the 2024 edition",
# "published in 1569". A preposition-anchored pattern missed "the 2024
# edition", which is how a year reached the claim list in the first run.
# A genuine measurement of exactly 2024 units is possible and is the accepted
# cost of a precision-first rail.
_YEAR = re.compile(r"(?<![\d,.])(?:1[5-9]|20)\d{2}(?![\d,.])")
# Semantic versions — "v0.131.0", "Python 3.13". qa.self_claim owns version truth.
_SEMVER = re.compile(r"\bv?\d+\.\d+(?:\.\d+)+\b")

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# Magnitude suffixes must be consumed WITH the number. "$10K MRR" parsed as
# "$10" is not a smaller claim, it is a different one — and it read as an
# unsupported fabrication in the first corpus run.
_MAGNITUDE = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9, "t": 1e12}
_MAG_ALT = "k|m|bn|b|t"

# Default checkable units. DB-tunable (qa_numeric_fidelity_units) because the
# vocabulary a niche measures in is per-install, not a universal constant.
DEFAULT_UNITS: tuple[str, ...] = (
    "tok/s", "tokens/s", "tokens/second", "tokens per second", "tokens",
    "ms", "milliseconds", "seconds", "sec", "minutes", "hours", "days",
    "gb", "mb", "tb", "kb", "gib", "mib",
    "w", "watts", "wh", "kwh", "kw",
    "words", "posts", "calls", "rows", "requests", "queries", "files",
    "fps", "hz", "ghz", "mhz", "gb/s", "mb/s",
    "x", "×",
)


# Signals that a sentence presents its number as SOURCED FACT rather than the
# author's own framing. DB-tunable (qa_numeric_fidelity_attribution_markers)
# because the vocabulary a niche cites in is per-install.
DEFAULT_ATTRIBUTION_MARKERS: tuple[str, ...] = (
    "according to", "reported by", "reports", "report", "survey", "surveyed",
    "study", "studies", "research", "researchers", "data from", "dataset",
    "benchmark", "benchmarked", "measured", "found that", "found", "shows that",
    "analysis", "analysed", "analyzed", "published", "documentation", "docs say",
    "per the", "cited", "statistics", "census", "poll", "respondents",
)
# A markdown link or a bare citation in the same sentence is itself an
# attribution — the author is pointing somewhere for the number.
_SENTENCE_LINK = re.compile(r"\[[^\]]+\]\([^)]*\)")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def _sentence_bounds(text: str) -> list[tuple[int, int]]:
    """Character spans of each sentence, so a claim can be tied to its own."""
    bounds: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_SPLIT.finditer(text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    return bounds


def is_attributed(sentence: str, markers: tuple[str, ...] | list[str]) -> bool:
    low = sentence.lower()
    if _SENTENCE_LINK.search(sentence):
        return True
    return any(mk in low for mk in markers)


@dataclass(frozen=True)
class NumericClaim:
    """One checkable numeric assertion lifted out of the prose."""

    raw: str
    value: float
    kind: str          # percent | currency | quantity | grouped | multiplier
    unit: str
    decimals: int      # decimal places AS WRITTEN — sets the rounding contract
    context: str
    attributed: bool = False   # presented as sourced fact -> scored

    def describe(self) -> str:
        return f"`{self.raw}`" + (f" ({self.context})" if self.context else "")


@dataclass
class ClaimVerdict:
    claim: NumericClaim
    status: str        # exact | derived | unsupported
    explanation: str = ""


@dataclass
class FidelityResult:
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    corpus_numbers: int = 0

    @property
    def checkable(self) -> int:
        """Attributed claims only — the scored population."""
        return len([v for v in self.verdicts if v.claim.attributed])

    @property
    def extracted(self) -> int:
        return len(self.verdicts)

    @property
    def unsupported(self) -> list[ClaimVerdict]:
        """Only an ATTRIBUTED claim can fail. An unattributed number is the
        author's own framing, not a promise about someone else's data."""
        return [
            v for v in self.verdicts
            if v.status == "unsupported" and v.claim.attributed
        ]

    @property
    def derived(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if v.status == "derived"]


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _decimals(token: str) -> int:
    cleaned = token.replace(",", "").strip()
    return len(cleaned.split(".")[1]) if "." in cleaned else 0


def strip_uncheckable(text: str) -> str:
    """Blank out regions whose digits are syntax rather than claims.

    Replaces with spaces rather than deleting so surrounding context (used for
    the operator-facing report) keeps its shape.
    """
    out = text
    for pattern in (
        _FENCED_CODE, _INLINE_CODE, _MD_LINK_TARGET, _BARE_URL,
        _HTML_TAG, _DATE, _YEAR, _SEMVER,
    ):
        out = pattern.sub(lambda m: " " * len(m.group(0)), out)
    return out


def _unit_alternation(units: tuple[str, ...] | list[str]) -> str:
    # Longest-first so "tokens/second" wins over "tokens".
    ordered = sorted({u.strip() for u in units if u and u.strip()}, key=len, reverse=True)
    return "|".join(re.escape(u) for u in ordered)


def extract_claims(
    text: str,
    *,
    units: tuple[str, ...] | list[str] = DEFAULT_UNITS,
    markers: tuple[str, ...] | list[str] = DEFAULT_ATTRIBUTION_MARKERS,
    context_chars: int = 60,
    max_claims: int = 400,
) -> list[NumericClaim]:
    """Lift checkable numeric claims out of ``text``, flagging which are
    presented as sourced fact. Pure."""
    scrubbed = strip_uncheckable(text or "")
    unit_alt = _unit_alternation(units)
    pattern = re.compile(
        rf"(?P<currency>[$£€]\s?(?P<cur_n>{_NUM})\s?(?P<cur_mag>{_MAG_ALT})?\b)"
        rf"|(?P<percent>(?P<pct_n>{_NUM})\s*(?:%|percent\b))"
        rf"|(?P<quantity>(?P<qty_n>{_NUM})\s*(?P<unit>{unit_alt})\b)"
        rf"|(?P<grouped>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    bounds = _sentence_bounds(scrubbed)
    marker_tuple = tuple(m.lower() for m in markers)

    def sentence_at(idx: int) -> str:
        for lo, hi in bounds:
            if lo <= idx < hi:
                return text[lo:hi]
        return ""

    claims: list[NumericClaim] = []
    for m in pattern.finditer(scrubbed):
        if len(claims) >= max_claims:
            break
        start = m.start()
        # A digit run fused to a preceding letter is an identifier (qwen2.5),
        # not a measurement.
        if start > 0 and _IDENTIFIER_PREFIX.search(scrubbed[start - 1]):
            continue

        magnitude = 1.0
        if m.group("currency"):
            token, kind, unit = m.group("cur_n"), "currency", m.group("currency")[0]
            suffix = (m.group("cur_mag") or "").lower()
            magnitude = _MAGNITUDE.get(suffix, 1.0)
        elif m.group("percent"):
            token, kind, unit = m.group("pct_n"), "percent", "%"
        elif m.group("quantity"):
            token, unit = m.group("qty_n"), m.group("unit").lower()
            kind = "multiplier" if unit in ("x", "×") else "quantity"
        else:
            token, kind, unit = m.group("grouped"), "grouped", ""

        value = _to_float(token)
        if value is None:
            continue
        value *= magnitude

        lo = max(0, start - context_chars)
        hi = min(len(text), m.end() + context_chars)
        claims.append(
            NumericClaim(
                raw=m.group(0).strip(),
                value=value,
                kind=kind,
                unit=unit,
                # A magnitude suffix moves the decimal point: "$1.5M" is exact
                # at 0 dp, not 1, so the rounding contract must follow it.
                decimals=max(0, _decimals(token) - (len(str(int(magnitude))) - 1)),
                context=" ".join(text[lo:hi].split()),
                attributed=is_attributed(sentence_at(start), marker_tuple),
            ),
        )
    return claims


def extract_corpus_numbers(
    *texts: str,
    units: tuple[str, ...] | list[str] = DEFAULT_UNITS,
    max_numbers: int = 400,
) -> list[float]:
    """Every VALUE the ground truth states.

    Looser than claim extraction — a source may express a figure in any shape,
    and a false negative here would flag a true claim as fabricated. But not
    *unbounded*: digits fused to letters are identifiers, and admitting them
    manufactures arithmetic operands out of model names. ``phi4:14b`` once
    contributed 4 and 14 to the corpus, and the derivation search duly
    "explained" a fabricated 91% as ``1 - 14/162.8``. A name is not a value.

    A digit run touching a letter is therefore skipped unless the trailing
    letters are a known unit (``32GB`` is a quantity; ``14b`` is a name).
    """
    unit_set = {u.strip().lower() for u in units if u and u.strip()}
    seen: dict[float, None] = {}
    token_re = re.compile(_NUM)
    for text in texts:
        if not text:
            continue
        for m in token_re.finditer(text):
            start, end = m.start(), m.end()
            # Fused to a preceding letter -> identifier (qwen2.5, GPT4).
            if start > 0 and _IDENTIFIER_PREFIX.search(text[start - 1]):
                continue
            # Fused to trailing letters -> identifier unless they are a unit.
            trailing = re.match(r"[A-Za-z/]+", text[end:])
            if trailing and trailing.group(0).lower().rstrip("/") not in unit_set:
                continue
            value = _to_float(m.group(0))
            if value is None:
                continue
            seen.setdefault(value, None)
            if len(seen) >= max_numbers:
                return list(seen)
    return list(seen)


def _matches(claimed: float, source: float, decimals: int) -> bool:
    """Does ``source`` reconcile with ``claimed`` at the written precision?

    Rounding a source figure for prose is normal writing — "235" for 235.1 is
    honest. Rounding is therefore applied to the SOURCE at the claim's own
    decimal count, never the other way around.
    """
    try:
        return round(source, decimals) == round(claimed, decimals)
    except (TypeError, ValueError, OverflowError):
        return False


def _direct_status(claim: NumericClaim, corpus: list[float]) -> str | None:
    for source in corpus:
        if _matches(claim.value, source, claim.decimals):
            return f"stated in source ({_fmt(source)})"
    if claim.kind == "percent":
        # Sources often carry a fraction where prose carries a percentage.
        for source in corpus:
            if _matches(claim.value, source * 100.0, claim.decimals):
                return f"source fraction {_fmt(source)} = {_fmt(source * 100)}%"
    return None


def _fmt(value: float) -> str:
    return f"{value:g}"


def _derived_status(
    claim: NumericClaim, corpus: list[float], *, max_pairs: int,
) -> str | None:
    """Four relations over pairs, capped and explained. See module docstring
    for why the search is deliberately not open-ended."""
    if claim.kind not in ("percent", "multiplier"):
        return None
    tried = 0
    for a in corpus:
        for b in corpus:
            if a == b or b == 0:
                continue
            tried += 1
            if tried > max_pairs:
                return None
            if claim.kind == "multiplier":
                if _matches(claim.value, a / b, claim.decimals):
                    return f"{_fmt(a)} / {_fmt(b)}"
                continue
            ratio = a / b
            if _matches(claim.value, ratio * 100.0, claim.decimals):
                return f"{_fmt(a)} / {_fmt(b)} = {_fmt(ratio * 100)}%"
            if _matches(claim.value, (1.0 - ratio) * 100.0, claim.decimals):
                return f"1 - {_fmt(a)}/{_fmt(b)} = {_fmt((1 - ratio) * 100)}%"
            if _matches(claim.value, ((a - b) / b) * 100.0, claim.decimals):
                return f"({_fmt(a)} - {_fmt(b)}) / {_fmt(b)} = {_fmt((a - b) / b * 100)}%"
    return None


def verify(
    content: str,
    corpus_texts: list[str],
    *,
    units: tuple[str, ...] | list[str] = DEFAULT_UNITS,
    markers: tuple[str, ...] | list[str] = DEFAULT_ATTRIBUTION_MARKERS,
    allow_derived: bool = False,
    score_unattributed: bool = False,
    max_pairs: int = 20000,
    max_claims: int = 400,
    max_corpus_numbers: int = 400,
) -> FidelityResult:
    """Check numeric claims in ``content`` against ``corpus_texts``.

    ``score_unattributed`` is the strict mode for a post built on a fact set we
    generated ourselves (a benchmark sweep, a chart spec): there, EVERY number
    should reconcile, because the corpus is the complete ground truth rather
    than background reading. On ordinary prose it stays False — see the module
    docstring for the measured reason.
    """
    claims = extract_claims(
        content, units=units, markers=markers, max_claims=max_claims,
    )
    if score_unattributed:
        claims = [
            NumericClaim(
                raw=c.raw, value=c.value, kind=c.kind, unit=c.unit,
                decimals=c.decimals, context=c.context, attributed=True,
            )
            for c in claims
        ]
    corpus = extract_corpus_numbers(
        *corpus_texts, units=units, max_numbers=max_corpus_numbers,
    )
    result = FidelityResult(corpus_numbers=len(corpus))

    for claim in claims:
        direct = _direct_status(claim, corpus)
        if direct:
            result.verdicts.append(ClaimVerdict(claim, "exact", direct))
            continue
        derived = (
            _derived_status(claim, corpus, max_pairs=max_pairs)
            if allow_derived else None
        )
        if derived:
            result.verdicts.append(ClaimVerdict(claim, "derived", derived))
            continue
        result.verdicts.append(
            ClaimVerdict(claim, "unsupported", "no source value reconciles"),
        )
    return result


__all__ = [
    "DEFAULT_ATTRIBUTION_MARKERS",
    "DEFAULT_UNITS",
    "ClaimVerdict",
    "FidelityResult",
    "NumericClaim",
    "extract_claims",
    "extract_corpus_numbers",
    "is_attributed",
    "strip_uncheckable",
    "verify",
]
