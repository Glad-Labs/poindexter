"""qa.self_claim — do the draft's claims about OUR OWN system match reality?

The highest-stakes fabrication class, because it is the most checkable
(poindexter#1007): the code is public, so a reader who believes the post
can open the repo and find that the thing does not exist. Three fabricated
or stale self-claims reached ``awaiting_approval`` at Q94–95 on 2026-08-09
— an invented retrieval mechanism, invented quality scores ("a Q of 85 or
87" when real scores are 70 and 94–98), and a version number two releases
stale. Every truth-oriented rail missed them: fabrication/citation rails
check the draft against its research bundle, and the corpus these claims
need is the repo + the live database.

This is the issue's "cheapest useful subset" — the DETERMINISTIC layers,
no LLM call:

1. **Version strings** claimed for our own release vs the running
   package version (``pyproject.toml``). Version numbers in prose rot
   within days; instance 3 rotted within two.
2. **Quality-score claims** about our own queue vs the real
   ``pipeline_tasks`` score distribution (±1 tolerance).
3. **Backticked settings-shaped keys** vs the live ``app_settings``
   table.
4. **Package-relative file paths** (``services/x.py`` …) vs the tree on
   disk.
5. **Named capabilities** — "we run/use/rely on X" and "our X tool" — vs
   the operating record (``services/operating_record.py``: the stack CSV,
   product names, ``plugin.*`` settings, ``cost_logs`` models). Added
   2026-09-15 after "We also run Jettison" reached the queue at QA 95: the
   name had been lifted from a *contrast* in one of our own published posts.
   Build-verbs ("we built X") are deliberately not extracted — they name our
   own features, which the record cannot enumerate.
6. **Install specs** in our-system context — system RAM, VRAM and GPU model
   names — vs the host the worker runs on. "128GB of system RAM" on a 64 GB
   box reached the queue at QA 97 the same week.

7. **First-person biography** — "my dad", "when I was ten", "growing up" —
   vs the operator's declared ``qa_self_claim_founder_facts``. Added
   2026-09-17 (poindexter#1055) after a draft invented the founder's
   childhood, a named 1997 side project with source filenames, and a Glad
   Labs teaching project, and reached ``awaiting_approval`` at Q94 with the
   critic scoring it 98 and **no rail objecting**. Unlike layers 1-6 this is
   a PROVENANCE check, not a record lookup — nothing can enumerate a
   childhood — and it deliberately runs outside the self-reference gate.

8. **Conducted-experiment claims** — "we ran the audit; X came out 18%
   faster than Y" — vs the figures in ``research_context``. Added 2026-09-18
   (poindexter#1050/#1052) after a draft narrated an internal engine benchmark
   that never happened, twice, at Q97.8. Neither neighbour owned it:
   ``qa.numeric_fidelity`` scores only numbers presented as SOURCED fact and
   defers our-own claims here, while this rail did record-resolution and no
   record enumerates experiments we ran. Deliberately narrow — see the layer's
   own comment for the three groundedness widths that were measured and
   rejected first.

The fuzzier "named mechanism vs repo symbol" layer (instance 1) needs the
grounded-LLM treatment (propose → verify the symbol resolves, the
``content.llm_reconcile_citations`` pattern) and is deliberately NOT here.

**A sibling detector was measured and rejected** (2026-09-17), which is worth
recording because it looks obvious: extract first-person build/measure claims
("we built X", "we measured Y") and flag those whose anchor is absent from
``research_context``. Against the real corpus it fired on **24% of 207
published posts**, and the sample was almost entirely TRUE claims — the
operating record that grounds "we shipped the retention CLI" simply is not in
a post's research bundle. That is the same blindness ``qa.numeric_fidelity``
paid for with pair-derivation. The measured biography layer fires on 0 of 207.

**Self-reference gate is load-bearing for precision**: every check runs
only when the draft is about our own system (product-name match from
``qa_self_claim_product_names`` + the operator's ``site_name``, or
first-person-plural system prose), and the version extractor additionally
requires our-system context in its local window — a post reviewing
another product's v2.3.1 must never be judged against OUR version.

A draft with NO falsifiable self-claims appends a scoreless
``not_applicable`` review (per the issue's acceptance: dev-diary prose
about the pipeline that asserts nothing checkable does not fire — and
``not_applicable`` carries no score into either mean, so it cannot). It
must not append NOTHING: this rail is ``required_to_pass`` and speaks on
~2% of drafts, so silence reads to ``qa.aggregate`` as an absent required
gate and hard-vetoes every post that does not talk about us
(poindexter#1060). DB-dependent layers (2, 3) skip silently without a
pool — the file/version layers still run; a skipped layer is reduced
coverage, never a fake verdict.

Advisory at birth (seeded ``qa_gates.self_claim.required_to_pass=false``),
**required since 2026-09-15** (migration ``20260915_014128``): with the
capability and install-spec layers the rail catches the fabrication class
that actually reaches the queue, so a confirmed-false self-claim vetoes and
the rescue cycle gets the offender list. The poindexter#454 lever demotes it
without a deploy. Master switch ``qa_self_claim_enabled`` (default true).

Chain position: after ``qa.title_coherence``, before ``qa.web_factcheck``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from poindexter.modules.content.atoms._pool import resolve_pool
from poindexter.modules.content.atoms._qa_rail_common import (
    not_applicable_review,
    resolve_gate_states,
    reviewer_to_dict,
)
from poindexter.plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.self_claim",
    type="atom",
    version="1.0.0",
    description=(
        "Deterministic verification of the draft's claims about our own "
        "system: version strings vs pyproject, quality-score claims vs "
        "pipeline_tasks, backticked settings keys vs app_settings, package "
        "file paths vs the tree, named capabilities (\"we run X\") and install "
        "specs (RAM/VRAM/GPU) vs the operating record, plus unsourced "
        "first-person biography vs qa_self_claim_founder_facts and "
        "conducted-experiment figures vs research_context (both advisory). "
        "Gate status DB-driven via qa_gates.self_claim (required since "
        "2026-09-15)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(name="topic", type="str", description="assignment topic", required=False),
        FieldSpec(
            name="research_context",
            type="str",
            description="corpus layer 8 reconciles conducted-experiment figures against",
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="self-claim review result",
        ),
    ),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("two read-only DB lookups when a pool is available",),
    parallelizable=True,
)

# Penalty per confirmed-false claim. Advisory, so this shapes the all-rail
# score + the operator's read, not the gate. DB-tunable via
# qa_self_claim_offender_penalty.
_DEFAULT_PENALTY = 25.0

# A version claim only counts as OURS when its neighbourhood talks about our
# system — "release v0.116.0" in a post about someone else's product is not
# our claim to check.
_VERSION_RE = re.compile(
    r"(?:release|version|currently at|running|shipped)\s+v?(\d+\.\d+\.\d+)",
    re.IGNORECASE,
)
_CONTEXT_WINDOW = 140

# "a Q of 85", "Q: 94", "quality score of 87", "Qs of 85 or 87" — two-digit
# claims about our own queue's scores.
_QSCORE_RE = re.compile(
    r"\bQs?\s*(?:of|at|:)\s*(\d{2})\b|\bquality\s+scores?\s+(?:of|at|around)\s+(\d{2})\b",
    re.IGNORECASE,
)

# Backticked snake_case tokens shaped like app_settings keys. The suffix list
# keeps ordinary code identifiers (function names, columns) out of scope.
_SETTINGS_TOKEN_RE = re.compile(r"`([a-z][a-z0-9_]{4,})`")
_SETTINGS_SUFFIXES = (
    "_enabled", "_threshold", "_model", "_url", "_seconds", "_minutes",
    "_hours", "_days", "_max", "_min", "_count", "_limit", "_mode",
)

# Package-relative source paths the post asserts exist.
_PATH_RE = re.compile(
    r"\b((?:services|modules|routes|plugins|utils|poindexter)/[\w./-]+?\.py)\b"
)


# Layer 5 — named capabilities. Only RUN-class verbs: "we run/use/rely on X"
# names something external the operating record can enumerate. Build-class
# verbs ("we built/shipped X") name our own features, which it cannot, so
# they are not extracted at all — a wrong "not in the record" is worse than
# a missed one.
_CAP_VERBS = (
    r"run|runs|running|ran|use|uses|using|used|operate|operates|operating|"
    r"maintain|maintains|deploy|deploys|deployed|rely\s+on|relies\s+on|"
    r"adopted|integrated|host|hosts|hosting|self-host|self-hosts"
)
_CAP_NAME = r"((?-i:[A-Z][\w.+-]*(?:\s+[A-Z][\w.+-]*){0,2}))"
_CAP_RE = re.compile(
    rf"\b(?:we|our\s+team|our\s+stack)\s+(?:also\s+|now\s+|still\s+|currently\s+|already\s+)?"
    rf"(?:{_CAP_VERBS})\s+(?:a\s+|an\s+|the\s+|our\s+own\s+|our\s+)?{_CAP_NAME}",
    re.IGNORECASE,
)
_OUR_TOOL_RE = re.compile(
    rf"\bour\s+{_CAP_NAME}\s+(?:tool|engine|generator|service|product|app|bot|agent|"
    r"sidecar|platform|module|plugin)\b",
    re.IGNORECASE,
)
_CAP_STOPWORDS = {
    "ai", "llm", "llms", "gpu", "gpus", "qa", "seo", "rag", "api", "apis", "ci",
    "i", "a", "an", "the", "it", "this", "that", "these", "those", "our", "we",
    "one", "two", "three", "several", "every", "each", "both",
}

# Layer 6 — install specs, checked only in our-system context.
_RAM_RE = re.compile(
    r"\b(\d{2,4})\s?GB\s+(?:of\s+)?(?:system\s+|host\s+|unified\s+)?RAM\b", re.IGNORECASE,
)
_VRAM_RE = re.compile(r"\b(\d{1,3})\s?GB\s+(?:of\s+)?VRAM\b", re.IGNORECASE)
_GPU_CLAIM_RE = re.compile(
    r"\b((?:GeForce\s+)?(?:RTX|GTX)\s?\d{4}(?:\s?(?:Ti|Super))?)\b", re.IGNORECASE,
)


_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")


# Layer 7 — first-person BIOGRAPHY. A different shape from layers 1-6: those
# resolve a claim against a record that can enumerate it (the repo, the host,
# app_settings). Nothing can enumerate the founder's childhood, so this layer
# checks PROVENANCE instead — a first-person biographical claim is unsourced
# unless an operator has declared the facts in ``qa_self_claim_founder_facts``.
#
# **research_context deliberately does NOT ground these** (poindexter#1055).
# The draft that earned this layer was built from an article about SOMEONE
# ELSE's father, so the corpus contained "dad" in abundance; grounding on the
# marker word would have passed the very fabrication the layer exists to
# catch. "My dad" is a claim about the AUTHOR, and only the author's own
# declared facts can source it.
#
# It also runs OUTSIDE the self-reference gate that layers 1-6 sit behind: an
# invented childhood is ungrounded whether or not the post is about our stack.
#
# Measured before wiring (2026-09-17, the numeric_fidelity discipline): these
# patterns fire on **0 of 207 published posts / 1.3M chars** of approved prose,
# while firing on the rejected fabrication (task 4a23f39e) in four places. The
# sibling detector that was measured and REJECTED is recorded in the issue —
# first-person build/measure claims ("we built X", "we measured Y") checked for
# groundedness in research_context still flagged 24% of approved posts, nearly
# all of them true, which is the pair-derivation blindness numeric_fidelity
# already paid for once.
_KIN = (
    r"dad|mom|mum|father|mother|brother|sister|wife|husband|son|daughter|"
    r"grandfather|grandmother|grandpa|grandma|uncle|aunt|cousin|parents"
)
_BIOGRAPHY_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("family", re.compile(rf"\bmy\s+(?:own\s+)?(?:{_KIN})\b", re.IGNORECASE)),
    (
        "childhood",
        re.compile(
            r"\bmy\s+(?:childhood|hometown|first\s+computer|first\s+PC|"
            r"school|teacher|upbringing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "age",
        re.compile(
            r"\bwhen\s+I\s+was\s+(?:a\s+kid|a\s+child|young|little|\d{1,2})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "age",
        # "I was ten, maybe eleven" — but never "I was 10x faster" / "I was 30% off".
        re.compile(
            r"\bI\s+was\s+(?:ten|eleven|twelve|thirteen|fourteen|fifteen|\d{1,2})\b"
            r"(?![\s-]*(?:%|percent|x\b|times\b))",
            re.IGNORECASE,
        ),
    ),
    ("childhood", re.compile(r"\bgrowing\s+up\b", re.IGNORECASE)),
)
_FIRST_PERSON_RE = re.compile(r"\b(?:I|my|me|we|our)\b", re.IGNORECASE)


def _sentence_around(content: str, start: int) -> str:
    """The whole sentence containing offset ``start``."""
    lo = 0
    for m in _SENTENCE_END_RE.finditer(content):
        if m.end() <= start:
            lo = m.end()
        else:
            break
    hi_m = _SENTENCE_END_RE.search(content, start)
    return content[lo : hi_m.start() if hi_m else len(content)]


def extract_biography_claims(content: str) -> list[tuple[str, str]]:
    """``(kind, sentence)`` for first-person biographical assertions, deduped.

    Every pattern additionally requires a first-person pronoun in the same
    sentence, so a third-person retelling of a source's personal story — the
    correct way to write about someone else's childhood — never fires.
    """
    claims: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, rx in _BIOGRAPHY_RES:
        for m in rx.finditer(content):
            sentence = " ".join(_sentence_around(content, m.start()).split())
            if not sentence or not _FIRST_PERSON_RE.search(sentence):
                continue
            key = sentence.lower()
            if key in seen:
                continue
            seen.add(key)
            claims.append((kind, sentence))
    return claims


def check_biography(claims: list[tuple[str, str]], founder_facts: str) -> list[str]:
    """Claims not supported by the operator's declared founder facts.

    With no declared facts (the default) every biographical claim is unsourced
    by construction — which is the finding, not a gap in the check.
    """
    facts = (founder_facts or "").lower()
    offenders: list[str] = []
    for kind, sentence in claims:
        if facts and _biography_supported(sentence, facts):
            continue
        excerpt = sentence if len(sentence) <= 120 else sentence[:117] + "..."
        offenders.append(
            f"unsourced first-person {kind} claim: \"{excerpt}\" — no "
            "qa_self_claim_founder_facts entry supports it (research_context "
            "cannot source a claim about the author)"
        )
    return offenders


_FACT_WORD_RE = re.compile(r"[a-z][a-z0-9'-]{3,}")
_FACT_STOPWORDS = frozenset(
    {
        "that", "this", "with", "from", "were", "been", "have", "what", "when",
        "which", "would", "could", "about", "there", "their", "them", "then",
        "into", "just", "like", "more", "most", "some", "such", "than", "they",
        "very", "will", "your", "still", "same", "over", "only", "also", "back",
    }
)


def _biography_supported(sentence: str, facts: str) -> bool:
    """True when the declared facts carry most of the sentence's content words.

    Deliberately strict: a single shared word ("dad") must not license an
    invented anecdote about one.
    """
    words = {
        w for w in _FACT_WORD_RE.findall(sentence.lower())
        if w not in _FACT_STOPWORDS
    }
    if not words:
        return False
    hits = sum(1 for w in words if w in facts)
    return hits >= max(2, int(0.6 * len(words)))


# Layer 8 — CONDUCTED-EXPERIMENT claims. "We ran the audit properly. Ollama
# came out about 18% faster than vLLM" reached the queue at Q97.8 twice — the
# reject-with-retry regenerated the same story — narrating an internal
# benchmark that never happened (poindexter#1050/#1052). It is the highest-trust
# sentence the site can print and the easiest one to fabricate.
#
# NEITHER neighbouring rail owns it, and that is structural rather than an
# oversight. ``qa.numeric_fidelity`` scores only numbers presented as SOURCED
# fact, and explicitly treats a claim about our own work as this rail's corpus;
# this rail did only record-resolution, and no record enumerates "experiments
# we ran". A claim each rail believes the other owns is how it shipped.
#
# **The scope is narrow ON PURPOSE, and three measurements set the boundary.**
# Checking first-person claims for groundedness in ``research_context`` was
# tried at three widths against the real corpus and failed every time, because
# that corpus is outside sources and can never witness our own work:
#
#   | detector                                   | fired on |
#   | ------------------------------------------ | -------- |
#   | first-person build/measure + proper-noun   | 24% of 207 posts, nearly all TRUE |
#   | first-person RESULT verb + number          | 38 of 41 sentences, all TRUE |
#   | ...scoped to canonical_blog only           | 4 of 7, all TRUE |
#
# What survives is the intersection that is actually checkable: the draft says
# WE CONDUCTED a comparison, and reports a FIGURE for its outcome. A figure we
# obtained from an experiment we ran is either in the corpus that experiment
# produced, or it was invented. Measured at **0 false positives across 89
# published posts** with 11/11 controls correct.
#
# It deliberately does NOT cover vague experiential prose ("we went through
# this exact decision"), which is uncheckable by construction — the published
# corpus contains "We went through this exact realization building our own
# content pipeline", which is fine, and no instrument separates those two.
_EXPERIMENT_NOUN_RE = re.compile(
    r"\b(?:we|our\s+team|i)\s+(?:\w+\s+){0,3}?"
    r"(?:ran|did|conducted|performed)\s+(?:a|an|the|our)?\s*"
    r"(?:audit|test|testing|benchmark\w*|experiment|comparison|bake-?off|"
    r"trial|evaluation|shoot-?out)\b",
    re.IGNORECASE,
)
# The weaker frame — any first-person measuring verb — only counts alongside a
# comparative outcome. On its own it matches ordinary dev-diary reporting
# ("we measured 10,240 MiB held ~6.5h"), which is true and must never fire.
_CONDUCTED_RE = re.compile(
    r"\b(?:we|our\s+team|i)\s+(?:\w+\s+){0,3}?"
    r"(?:ran|did|conducted|performed|benchmarked|measured|tested|profiled|"
    r"compared|timed|clocked|audited|evaluated)\b",
    re.IGNORECASE,
)
_COMPARATIVE_RE = re.compile(
    r"\b(?:faster|slower|cheaper|better|worse|outperform\w*|beat|ahead\s+of|"
    r"versus|vs\.?|compared\s+(?:to|with)|edge\s+over)\b",
    re.IGNORECASE,
)
# The comma-grouped alternative REQUIRES a comma group. With `*` it matched
# "202" out of "2026" and the year filter — which compares the captured text —
# then let the fragment through as a measurement.
_FIGURE_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d,])"
)
_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")

# The claim and its figure routinely sit in adjacent sentences — the draft that
# earned this layer said "We ran the audit properly." and put the 18% in the
# NEXT sentence, which carries no first-person marker at all.
_CLAIM_WINDOW_SENTENCES = 3

# Below this much corpus there is nothing to reconcile against, and every
# figure would read as invented. 42% of runs carry no research_context at all.
_MIN_CORPUS_CHARS = 200


def extract_figures(text: str) -> list[str]:
    """Numbers in ``text``, excluding bare years — a date is not a measurement."""
    out: list[str] = []
    for m in _FIGURE_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        if _YEAR_RE.match(raw) or raw in out:
            continue
        out.append(raw)
    return out


def figure_in_corpus(figure: str, corpus: str) -> bool:
    """Whether ``corpus`` carries this figure AT THE PRECISION WRITTEN.

    Rounding a source value for prose is normal writing, so "2.3" reconciles
    against 2.34 — the same rule ``qa.numeric_fidelity`` uses.
    """
    if figure in corpus:
        return True
    try:
        claimed = float(figure)
    except ValueError:
        return False
    decimals = len(figure.split(".")[1]) if "." in figure else 0
    for found in _FIGURE_RE.findall(corpus):
        try:
            value = float(found.replace(",", ""))
        except ValueError:
            continue
        if round(value, decimals) == round(claimed, decimals):
            return True
    return False


def extract_experiment_claims(content: str) -> list[tuple[str, list[str]]]:
    """``(passage, figures)`` for conducted-experiment claims carrying a figure.

    Fires on either an explicitly NAMED experiment ("we ran a bake-off") or a
    measuring verb paired with a comparative outcome ("we benchmarked both and
    X turned out 2.3x faster"). Both were measured at zero false positives; the
    weaker frame alone was not.
    """
    sentences = [
        " ".join(s.split()) for s in _SENTENCE_END_RE.split(content or "") if s.strip()
    ]
    claims: list[tuple[str, list[str]]] = []
    # Windows OVERLAP, so one fabrication sits inside several of them. Reporting
    # each is not just noisy — every duplicate costs another penalty, so a
    # single invented benchmark could zero the score on its own.
    #
    # De-duplicate on the FIGURES rather than on position: a window whose
    # numbers are already covered is the same claim seen again, while a later
    # passage with genuinely different figures is a genuinely different claim.
    # Positional skipping was tried first and merged two distinct adjacent
    # experiments into one.
    reported: list[set[str]] = []
    for i in range(len(sentences)):
        window = " ".join(sentences[i : i + _CLAIM_WINDOW_SENTENCES])
        named = bool(_EXPERIMENT_NOUN_RE.search(window))
        compared = bool(_CONDUCTED_RE.search(window)) and bool(
            _COMPARATIVE_RE.search(window)
        )
        if not (named or compared):
            continue
        figures = extract_figures(window)
        if not figures:
            continue
        found = set(figures)
        if any(found <= seen for seen in reported):
            continue
        reported.append(found)
        claims.append((window, figures))
    return claims


def check_experiment_claims(
    claims: list[tuple[str, list[str]]], corpus: str,
) -> list[str]:
    """Claims reporting a figure that appears nowhere in the corpus."""
    offenders: list[str] = []
    for passage, figures in claims:
        missing = [f for f in figures if not figure_in_corpus(f, corpus)]
        if not missing:
            continue
        excerpt = passage if len(passage) <= 140 else passage[:137] + "..."
        offenders.append(
            f"reports conducting an experiment whose figure(s) "
            f"{', '.join(missing[:4])} appear nowhere in the research context: "
            f'"{excerpt}"'
        )
    return offenders


def extract_capability_claims(content: str) -> list[str]:
    """Capitalised names the draft says WE run/use/rely on, in order, deduped."""
    names: list[str] = []
    for rx in (_CAP_RE, _OUR_TOOL_RE):
        for m in rx.finditer(content):
            name = m.group(1).strip().rstrip(".,;:")
            if not name:
                continue
            if name.split()[0].lower() in _CAP_STOPWORDS:
                continue
            if name not in names:
                names.append(name)
    return names


def check_capabilities(names: list[str], record: Any) -> list[str]:
    from poindexter.services.operating_record import name_is_known

    return [
        f'names "{n}" as something we run or use — not in the operating record '
        "(qa_self_claim_known_components, plugin.* settings, cost_logs models)"
        for n in names if not name_is_known(n, record)
    ]


def _sentence_is_ours(content: str, start: int, markers: list[str]) -> bool:
    """Same-sentence version of ``_window_is_ours``: a spec counts as OURS only
    when the sentence that states it names our system or speaks as "we/our".
    The 140-char window bled a neighbouring "Our Poindexter…" sentence into
    "The reviewer's RTX 4090…" during the 2026-09-15 probe."""
    lo = 0
    for m in _SENTENCE_END_RE.finditer(content):
        if m.end() <= start:
            lo = m.end()
        else:
            break
    hi_m = _SENTENCE_END_RE.search(content, start)
    hi = hi_m.start() if hi_m else len(content)
    sentence = content[lo:hi].lower()
    if any(mk in sentence for mk in markers):
        return True
    return bool(re.search(r"\b(?:we|our)\b", sentence))


def extract_install_specs(content: str, markers: list[str]) -> list[tuple[str, str]]:
    """``(kind, value)`` for RAM / VRAM / GPU-model claims made in a sentence
    about our own system."""
    specs: list[tuple[str, str]] = []
    for kind, rx in (("ram_gb", _RAM_RE), ("vram_gb", _VRAM_RE), ("gpu", _GPU_CLAIM_RE)):
        for m in rx.finditer(content):
            if not _sentence_is_ours(content, m.start(), markers):
                continue
            specs.append((kind, m.group(1)))
    return specs


def _norm_gpu(name: str) -> str:
    n = re.sub(r"\s+", " ", name.strip().lower()).replace("geforce ", "")
    return re.sub(r"^(rtx|gtx)(\d)", r"\1 \2", n)


def check_install_specs(specs: list[tuple[str, str]], record: Any) -> tuple[list[str], bool]:
    """``(offenders, checked_any)`` — a spec the record cannot judge is skipped."""
    offenders: list[str] = []
    checked = False
    per_gpu = [g.vram_gb for g in getattr(record, "gpus", ()) if getattr(g, "vram_gb", None)]
    gpu_names = [g.name for g in getattr(record, "gpus", ())]
    for kind, value in specs:
        if kind == "ram_gb" and getattr(record, "ram_gb", None):
            checked = True
            claimed = float(value)
            if not (0.85 * record.ram_gb <= claimed <= 1.3 * record.ram_gb):
                offenders.append(
                    f"claims {value}GB of system RAM — this host reports ~{round(record.ram_gb)}GB"
                )
        elif kind == "vram_gb" and per_gpu:
            checked = True
            claimed = float(value)
            candidates = per_gpu + [sum(per_gpu)]
            if not any(abs(claimed - v) <= 0.15 * v for v in candidates):
                offenders.append(
                    f"claims {value}GB of VRAM — the cards here have "
                    + ", ".join(f"{int(v)}GB" for v in per_gpu)
                )
        elif kind == "gpu" and gpu_names:
            checked = True
            if _norm_gpu(value) not in gpu_names:
                offenders.append(
                    f"names a {value} — the GPUs here are " + ", ".join(gpu_names).upper()
                )
    return offenders, checked


def _is_enabled(site_config: Any) -> bool:
    try:
        raw = site_config.get("qa_self_claim_enabled", "true")
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional master switch — default the advisory rail ON (it
        # only scores, never vetoes) when a config-read blip occurs.
        return True
    return str(raw).lower() in ("true", "1", "yes")


def _biography_mode(site_config: Any) -> str:
    """``off`` | ``advisory`` (default) | ``enforcing``.

    Advisory at birth per poindexter#1055 even though the layer measured 0
    false positives on 207 published posts: the rail as a whole is
    ``required_to_pass`` since 2026-09-15, so an offender here would veto, and
    a niche that deliberately publishes personal essay should be able to opt
    out without a deploy.
    """
    try:
        raw = str(site_config.get("qa_self_claim_biography_mode", "advisory") or "advisory")
    except Exception:  # noqa: BLE001 — stubbed site_config
        # silent-ok: an unreadable switch falls back to the seeded default,
        # which only scores and never vetoes.
        return "advisory"
    mode = raw.strip().lower()
    return mode if mode in ("off", "advisory", "enforcing") else "advisory"


def _experiment_mode(site_config: Any) -> str:
    """``off`` | ``advisory`` (default) | ``enforcing``.

    Advisory at birth despite 0 false positives across 89 published posts: the
    rail is ``required_to_pass``, and the positive controls are the fabricated
    sentences as QUOTED IN THE ISSUE rather than the original draft blobs,
    which were edited before publish and pruned from pipeline_versions. The
    negative evidence is strong; the positive evidence is reconstructed, and
    that asymmetry is a reason to score rather than veto.
    """
    try:
        raw = str(site_config.get("qa_self_claim_experiment_mode", "advisory") or "advisory")
    except Exception:  # noqa: BLE001 — stubbed site_config
        # silent-ok: an unreadable switch falls back to the seeded default,
        # which only scores and never vetoes.
        return "advisory"
    mode = raw.strip().lower()
    return mode if mode in ("off", "advisory", "enforcing") else "advisory"


def _founder_facts(site_config: Any) -> str:
    try:
        return str(site_config.get("qa_self_claim_founder_facts", "") or "")
    except Exception:  # noqa: BLE001 — stubbed site_config
        # silent-ok: no declared facts is the default state, and it makes the
        # layer stricter (nothing grounds), never laxer.
        return ""


def _product_markers(site_config: Any) -> list[str]:
    """Lowercased markers that make a draft 'about our system'."""
    markers: list[str] = []
    try:
        raw = site_config.get("qa_self_claim_product_names", "poindexter") or ""
    except Exception:  # noqa: BLE001 — stubbed site_config
        raw = "poindexter"
    markers.extend(m.strip().lower() for m in raw.split(",") if m.strip())
    try:
        site_name = (site_config.get("site_name", "") or "").strip().lower()
    except Exception:  # noqa: BLE001 — stubbed site_config
        site_name = ""
    if site_name:
        markers.append(site_name)
    return markers or ["poindexter"]


_SELF_PROSE_RE = re.compile(
    r"\b(?:our|we)\b.{0,50}\b(?:pipeline|rail|atom|graph|worker|scheduler|"
    r"codebase|repo|release|queue|dashboard)\b",
    re.IGNORECASE | re.DOTALL,
)


def is_self_referential(content: str, topic: str, markers: list[str]) -> bool:
    haystack = f"{topic}\n{content}".lower()
    if any(m in haystack for m in markers):
        return True
    return bool(_SELF_PROSE_RE.search(content))


def _package_root() -> Path:
    # …/poindexter/modules/content/atoms/qa_self_claim.py → src/cofounder_agent,
    # whose pyproject.toml is the one distribution manifest (poindexter#1046
    # step 5) and carries the release version. parents[3] is poindexter/.
    return Path(__file__).resolve().parents[4]


def current_package_version(root: Path | None = None) -> str | None:
    """The running package version from pyproject.toml, or None."""
    root = root or _package_root()
    try:
        import tomllib

        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        # silent-ok: missing/unreadable pyproject just skips the version
        # layer (None = "no claim checked"), it never fakes a verdict — the
        # rail's other three layers still run.
        return None
    return (
        data.get("tool", {}).get("poetry", {}).get("version")
        or data.get("project", {}).get("version")
    )


def _window_is_ours(content: str, start: int, end: int, markers: list[str]) -> bool:
    lo = max(0, start - _CONTEXT_WINDOW)
    window = content[lo : end + _CONTEXT_WINDOW].lower()
    if any(m in window for m in markers):
        return True
    return bool(re.search(r"\b(?:we|our)\b", window, re.IGNORECASE))


def extract_our_version_claims(content: str, markers: list[str]) -> list[str]:
    """Version strings claimed in an our-system context, in order."""
    return [
        m.group(1)
        for m in _VERSION_RE.finditer(content)
        if _window_is_ours(content, m.start(), m.end(), markers)
    ]


def extract_qscore_claims(content: str) -> list[int]:
    claims = []
    for m in _QSCORE_RE.finditer(content):
        raw = m.group(1) or m.group(2)
        if raw:
            claims.append(int(raw))
    return claims


def check_qscores_against(claims: list[int], real_scores: set[int]) -> list[str]:
    """Claims not within ±1 of any real score are invented numbers."""
    if not real_scores:
        return []
    offenders = []
    for claimed in claims:
        if not any(abs(claimed - real) <= 1 for real in real_scores):
            offenders.append(
                f"quality-score claim Q{claimed} — no such score exists in "
                f"pipeline_tasks (feedback_no_dummy_data)"
            )
    return offenders


def extract_settings_tokens(content: str) -> list[str]:
    return [
        t for t in dict.fromkeys(_SETTINGS_TOKEN_RE.findall(content))
        if t.endswith(_SETTINGS_SUFFIXES)
    ]


def extract_paths(content: str) -> list[str]:
    return list(dict.fromkeys(_PATH_RE.findall(content)))


def check_paths(paths: list[str], root: Path | None = None) -> list[str]:
    root = root or _package_root()
    return [
        f"file path `{p}` does not exist in the repo"
        for p in paths
        # A path spelled the pre-#1046 flat way (`services/x.py`) still names a
        # real file -- it lives under poindexter/ now, exactly as the import alias
        # maps `services.x` onto `poindexter.services.x`. Both spellings pass.
        if not ((root / p).exists() or (root / "poindexter" / p).exists())
    ]


def _na(reason: str) -> dict[str, Any]:
    """The rail ran and found nothing falsifiable — an honest pass.

    ``self_claim`` is ``required_to_pass`` and speaks on ~2% of drafts (4 of
    171 passes over 45 days), so a bare ``{}`` reads to ``qa.aggregate`` as an
    ABSENT required gate and hard-vetoes every post that simply does not talk
    about us (poindexter#1060).
    """
    return {"qa_rail_reviews": [not_applicable_review(
        reviewer="self_claim", provider="programmatic", feedback=reason,
    )]}


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        # Genuinely COULD NOT run. Fail closed: a required gate with no review
        # is a veto, and that is the guard working as intended here.
        return {}
    if not _is_enabled(site_config):
        # A disabled rail must not be able to veto. qa_self_claim_enabled says
        # whether the rail RUNS; qa_gates.self_claim.required_to_pass says
        # whether it GATES. Returning {} makes the off switch hard-reject
        # every post, so the two levers would fight.
        return _na("Self-claim rail disabled (qa_self_claim_enabled=false).")

    topic = str(state.get("topic") or "")
    markers = _product_markers(site_config)

    # Layer 7 runs OUTSIDE the self-reference gate: an invented childhood is
    # ungrounded whether or not the draft is about our own stack, and the
    # essay that earned this layer barely mentioned the product.
    bio_mode = _biography_mode(site_config)
    bio_offenders: list[str] = []
    bio_checked = False
    if bio_mode != "off":
        bio_claims = extract_biography_claims(content)
        if bio_claims:
            bio_checked = True
            bio_offenders = check_biography(bio_claims, _founder_facts(site_config))

    # Layer 8 also runs outside the self-reference gate: "we ran a benchmark
    # and X came out 18% faster" is a claim about work we did, whether or not
    # the post is about our stack.
    exp_mode = _experiment_mode(site_config)
    exp_offenders: list[str] = []
    exp_checked = False
    if exp_mode != "off":
        corpus = str(state.get("research_context") or "")
        exp_claims = extract_experiment_claims(content)
        if exp_claims and len(corpus) >= _MIN_CORPUS_CHARS:
            # Without a corpus every figure reads as invented, so a thin
            # research_context (42% of runs carry none) is "nothing to judge",
            # never "all fabricated".
            exp_checked = True
            exp_offenders = check_experiment_claims(exp_claims, corpus)
        elif exp_claims:
            logger.info(
                "[qa.self_claim] %d conducted-experiment claim(s) not judged — "
                "research_context is %d chars (floor %d)",
                len(exp_claims), len(corpus), _MIN_CORPUS_CHARS,
            )

    if not is_self_referential(content, topic, markers) and not (
        bio_checked or exp_checked
    ):
        return _na(
            "Draft makes no claims about this system — nothing to check "
            "against the repo, the settings table or the operating record."
        )

    self_referential = is_self_referential(content, topic, markers)

    real_version = current_package_version() if self_referential else None
    version_claims = (
        extract_our_version_claims(content, markers) if self_referential else []
    )
    paths = extract_paths(content) if self_referential else []
    qscore_claims = extract_qscore_claims(content) if self_referential else []
    settings_tokens = extract_settings_tokens(content) if self_referential else []

    offenders: list[str] = []
    versions_checked = bool(version_claims and real_version)
    if versions_checked:
        offenders += [
            f"version claim v{v} — the running release is v{real_version}"
            for v in version_claims if v != real_version
        ]
    offenders += check_paths(paths)

    pool = resolve_pool(state, atom="qa.self_claim")
    # Layers a failed dependency prevented from running. The rail stays
    # fail-open (reduced coverage, never a fake verdict), but the N/A review
    # must SAY so rather than read as "the draft asserted nothing".
    coverage_gaps: list[str] = []
    checked_db_layers = False
    if pool is not None and (qscore_claims or settings_tokens):
        try:
            async with pool.acquire() as conn:
                if qscore_claims:
                    rows = await conn.fetch(
                        "SELECT DISTINCT ROUND(quality_score)::int AS q "
                        "FROM pipeline_tasks WHERE quality_score > 0"
                    )
                    offenders += check_qscores_against(
                        qscore_claims, {r["q"] for r in rows},
                    )
                if settings_tokens:
                    rows = await conn.fetch(
                        "SELECT key FROM app_settings WHERE key = ANY($1::text[])",
                        settings_tokens,
                    )
                    present = {r["key"] for r in rows}
                    offenders += [
                        f"settings key `{t}` does not exist in app_settings"
                        for t in settings_tokens if t not in present
                    ]
            checked_db_layers = True
        except Exception as e:  # noqa: BLE001
            coverage_gaps.append("quality-score/settings layers (DB unavailable)")
            logger.warning(
                "[qa.self_claim] DB layers skipped (reduced coverage, "
                "never a fake verdict): %s", e,
            )

    # Layers 5-6: named capabilities + install specs vs the operating record.
    capability_names = extract_capability_claims(content) if self_referential else []
    install_specs = (
        extract_install_specs(content, markers) if self_referential else []
    )
    capabilities_checked = specs_checked = False
    if capability_names or install_specs:
        try:
            from poindexter.services.operating_record import load_operating_record

            record = await load_operating_record(site_config, pool)
        except Exception as e:  # noqa: BLE001
            coverage_gaps.append("capability/install-spec layers (operating record unavailable)")
            logger.warning(
                "[qa.self_claim] operating record unavailable — capability/spec "
                "layers skipped (reduced coverage, never a fake verdict): %s", e,
            )
            record = None
        if record is not None:
            if capability_names:
                capabilities_checked = True
                offenders += check_capabilities(capability_names, record)
            if install_specs:
                spec_offenders, specs_checked = check_install_specs(install_specs, record)
                offenders += spec_offenders
    # Nothing falsifiable EXTRACTED → a scoreless not_applicable pass, never
    # silence. Prose ABOUT the pipeline that asserts nothing checkable must
    # not fire (issue acceptance) and a vacuous 100 must not skew the all-rail
    # average — not_applicable gives both, while still satisfying the required
    # gate honestly instead of by absence (poindexter#1060).
    checked_anything = (
        versions_checked
        or bool(paths)
        or (checked_db_layers and bool(qscore_claims or settings_tokens))
        or capabilities_checked
        or specs_checked
        or bio_checked
        or exp_checked
    )
    # An ENFORCING biography layer vetoes with the rest; an advisory one only
    # scores and names the claim, so graduating it is a settings change.
    advisory_offenders: list[str] = []
    for mode, found in ((bio_mode, bio_offenders), (exp_mode, exp_offenders)):
        if mode == "enforcing":
            offenders += found
        else:
            advisory_offenders += found
    if not offenders and not advisory_offenders and not checked_anything:
        if coverage_gaps:
            return _na(
                "No verdict — reduced coverage: " + "; ".join(coverage_gaps)
                + ". Nothing was resolved, so nothing is claimed either way."
            )
        return _na(
            "Draft discusses this system but asserts nothing falsifiable — "
            "no version, path, settings key, quality score, named capability, "
            "install spec, biography or conducted-experiment claim to resolve."
        )

    from poindexter.modules.content.multi_model_qa import MultiModelQA, ReviewerResult

    penalty = _DEFAULT_PENALTY
    try:
        penalty = float(
            site_config.get("qa_self_claim_offender_penalty", _DEFAULT_PENALTY)
            or _DEFAULT_PENALTY
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        penalty = _DEFAULT_PENALTY

    # Advisory offenders move the score and the operator's read, never the gate.
    score = max(0.0, 100.0 - penalty * (len(offenders) + len(advisory_offenders)))
    parts = []
    if offenders:
        parts.append("False self-claims: " + "; ".join(offenders[:5]))
    if advisory_offenders:
        parts.append(
            "Unsourced first-person claims (advisory): "
            + "; ".join(advisory_offenders[:5])
        )
    feedback = " ".join(parts) or "Self-claims verified against the running system."
    review = ReviewerResult(
        reviewer="self_claim",
        approved=not offenders,
        score=score,
        feedback=feedback,
        provider="programmatic",
    )
    qa = MultiModelQA(
        pool=pool,
        settings_service=state.get("settings_service"),
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "self_claim")
    if offenders:
        logger.info(
            "[qa.self_claim] %d false self-claim(s): %s",
            len(offenders), "; ".join(offenders[:3]),
        )
    if advisory_offenders:
        logger.info(
            "[qa.self_claim] %d unsourced first-person claim(s), advisory "
            "(biography_mode=%s experiment_mode=%s): %s",
            len(advisory_offenders), bio_mode, exp_mode,
            "; ".join(advisory_offenders[:3]),
        )
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
