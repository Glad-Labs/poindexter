"""Title-variety avoidance block shared by every title-producing path.

Both title paths used to fight repetition by pasting the last N published
titles into the prompt under an "AVOID SIMILARITY" banner. Measured against
the live corpus (151 posts, 120 days) that did not work:

- ``canonical_blog`` (62 posts): 50% opened with a leading ``"The "``, 38%
  carried a colon subtitle — and it *had* the avoid-list.
- ``dev_diary`` (82 posts): 36% joined two ideas with ``"and"``, 17% opened
  on a gerund (Hunting / Chasing / Fighting / Locking) — and it had no
  avoidance mechanism at all.

The raw dump is the problem, not the fix. Handing a model twenty titles that
are themselves half ``"The …"`` is few-shot priming toward the habit; one
sentence asking for something "DISTINCTLY DIFFERENT" does not outweigh twenty
worked examples of the pattern. So this module *describes the habits* instead
of *showing the titles*: it profiles the recent window, names the structural
and lexical patterns that are over-represented, and asks for a title outside
them.

Verbatim titles still appear in one case — a confirmed near-duplicate found by
:func:`services.title_generation.check_title_originality`. Those are specific
collisions to dodge, not a corpus to imitate, so naming them is correct.

Everything is DB-tunable (``title_avoidance_*`` in ``app_settings``), including
an escape hatch back to the legacy raw-dump behaviour via
``title_avoidance_mode='titles'``.

Issue: Glad-Labs/poindexter#1043.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Recent-title window size. Was a hardcoded ``LIMIT 20`` duplicated across
# three call sites; an operator tuning title variety should not need a deploy.
DEFAULT_RECENT_COUNT: int = 20

# Share of the window a structural pattern must cover before it is named as a
# habit. 0.20 is calibrated against the live corpus: it catches the leading
# "The" (29%), the "and" compound (23%), and the gerund opener (17% within
# dev_diary) without firing on incidental shapes.
DEFAULT_PATTERN_THRESHOLD: float = 0.20

# How many times a content word must recur in the window before it is called
# out as over-used vocabulary.
DEFAULT_LEXICAL_MIN_COUNT: int = 3

# Cap on how many over-used words are named. Beyond a handful the instruction
# stops reading as guidance and starts reading as a banned-word list, which
# pushes the model into contortions to avoid accurate terminology.
_MAX_LEXICAL_TERMS: int = 6

# Structural function words, plus the site's own unavoidable subject-matter
# nouns. Excluded from the lexical over-use scan: telling the model to stop
# saying "AI" on an AI blog is not useful guidance.
_LEXICAL_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
        "how", "in", "is", "it", "its", "not", "of", "on", "or", "our", "that",
        "the", "this", "to", "vs", "was", "we", "what", "when", "why", "with",
        "you", "your",
    }
)


# ---------------------------------------------------------------------------
# Structural pattern detectors
# ---------------------------------------------------------------------------

_QUESTION_OPENERS = (
    "why", "how", "what", "when", "where", "should", "is", "are", "can", "do",
    "does",
)

_LISTICLE_RE = re.compile(
    r"\b\d+\s+(ways|reasons|things|patterns|tips|steps|lessons|rules|mistakes"
    r"|habits|ideas|tools|tricks)\b",
    re.IGNORECASE,
)

_AND_COMPOUND_RE = re.compile(r"\s+and\s+", re.IGNORECASE)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _starts_with_the(title: str) -> bool:
    return title.lower().startswith("the ")


def _has_colon_subtitle(title: str) -> bool:
    # A colon splitting the title into headline + subtitle. Trailing or
    # leading colons are punctuation accidents, not the pattern.
    body = title.strip()
    idx = body.find(":")
    return 0 < idx < len(body) - 1


def _has_and_compound(title: str) -> bool:
    return bool(_AND_COMPOUND_RE.search(title))


def _opens_on_gerund(title: str) -> bool:
    match = _WORD_RE.match(title.strip())
    if match is None:
        return False
    word = match.group(0).lower()
    # 4-char floor keeps "King"/"Ring"-shaped nouns out; they are not the
    # "Hunting Ghosts in the Middleware" opener this targets.
    return len(word) > 4 and word.endswith("ing")


def _opens_on_question(title: str) -> bool:
    match = _WORD_RE.match(title.strip())
    return match is not None and match.group(0).lower() in _QUESTION_OPENERS


def _is_listicle(title: str) -> bool:
    return bool(_LISTICLE_RE.search(title)) or bool(
        re.match(r"^\s*\d+\b", title)
    )


@dataclass(frozen=True)
class _Pattern:
    """One structural habit: how to spot it, how to describe it."""

    key: str
    # Rendered into the profile as "<share> of recent titles <description>".
    description: str
    detector: Callable[[str], bool]


_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern("leading_the", 'open with a leading "The"', _starts_with_the),
    _Pattern(
        "colon_subtitle",
        "split into a headline and a colon subtitle",
        _has_colon_subtitle,
    ),
    _Pattern("and_compound", 'join two ideas with "and"', _has_and_compound),
    _Pattern(
        "gerund_open",
        'open on an "-ing" verb (Hunting, Chasing, Locking)',
        _opens_on_gerund,
    ),
    _Pattern("question_open", "open as a question", _opens_on_question),
    _Pattern("listicle", "lead with a number", _is_listicle),
)


# ---------------------------------------------------------------------------
# Corpus profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TitleCorpusProfile:
    """What the recent title window has settled into.

    ``sample_size`` is the number of titles profiled — 0 means the window was
    empty and no guidance can be derived (an honest "no data", distinct from
    "no habits found").
    """

    sample_size: int
    # [(description, share)] for patterns at or above the threshold, strongest
    # first. Share is 0.0..1.0 of the window.
    patterns: list[tuple[str, float]] = field(default_factory=list)
    # Over-used content words, most frequent first.
    overused_terms: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.patterns or self.overused_terms)


def analyze_title_patterns(
    titles: Sequence[str],
    *,
    pattern_threshold: float = DEFAULT_PATTERN_THRESHOLD,
    lexical_min_count: int = DEFAULT_LEXICAL_MIN_COUNT,
) -> TitleCorpusProfile:
    """Profile a window of titles into named structural + lexical habits.

    Pure and deterministic — no LLM, no IO. A pattern is reported when it
    covers ``pattern_threshold`` or more of the window; a word is reported
    when it recurs ``lexical_min_count`` or more times.
    """
    cleaned = [t.strip() for t in titles if t and t.strip()]
    if not cleaned:
        return TitleCorpusProfile(sample_size=0)

    total = len(cleaned)
    found: list[tuple[str, float]] = []
    for pattern in _PATTERNS:
        hits = sum(1 for title in cleaned if pattern.detector(title))
        share = hits / total
        if share >= pattern_threshold:
            found.append((pattern.description, share))
    found.sort(key=lambda pair: pair[1], reverse=True)

    counts: Counter[str] = Counter()
    for title in cleaned:
        # Count each word once per title: a title repeating a word does not
        # make that word a *corpus-wide* habit.
        for word in {w.lower() for w in _WORD_RE.findall(title)}:
            if word not in _LEXICAL_STOPWORDS and len(word) > 2:
                counts[word] += 1
    overused = [
        word
        for word, count in counts.most_common()
        if count >= lexical_min_count
    ][:_MAX_LEXICAL_TERMS]

    return TitleCorpusProfile(
        sample_size=total, patterns=found, overused_terms=overused
    )


def _describe_share(share: float) -> str:
    """Plain-language share, so the prompt reads as guidance not telemetry."""
    if share >= 0.66:
        return "Most"
    if share >= 0.45:
        return "About half"
    if share >= 0.38:
        return "Well over a third"
    if share >= 0.28:
        return "Roughly a third"
    return "Several"


# ---------------------------------------------------------------------------
# Prompt block
# ---------------------------------------------------------------------------


def render_avoidance_block(
    profile: TitleCorpusProfile,
    *,
    near_duplicates: Sequence[str] = (),
    legacy_titles: Sequence[str] = (),
) -> str:
    """Render the prompt block. Returns ``""`` when there is nothing to say.

    ``legacy_titles`` renders the pre-2026-08 raw dump and is only populated
    under ``title_avoidance_mode`` of ``titles``/``both``. ``near_duplicates``
    are confirmed collisions and always render verbatim — those are specific
    titles to dodge, not a corpus to imitate.
    """
    sections: list[str] = []

    if profile.has_findings:
        lines = [
            "TITLE VARIETY — recent titles on this site have settled into "
            "these habits:"
        ]
        for description, share in profile.patterns:
            lines.append(f"- {_describe_share(share)} {description}.")
        if profile.overused_terms:
            terms = ", ".join(f'"{t}"' for t in profile.overused_terms)
            lines.append(
                f"- These words are already carrying several recent titles: "
                f"{terms}."
            )
        lines.append(
            "\nWrite a title that reads as though it came from outside that "
            "run — reach for a structure and vocabulary none of the above "
            "describes. Accuracy outranks variety: a title the article does "
            "not support is worse than a familiar one."
        )
        sections.append("\n".join(lines))

    if legacy_titles:
        listed = "\n".join(f"- {t}" for t in legacy_titles if t)
        if listed:
            sections.append(
                f"AVOID SIMILARITY to these recent titles:\n{listed}\n\n"
                "Your title must be distinctly different in structure and "
                "wording."
            )

    if near_duplicates:
        listed = "\n".join(f"- {t}" for t in near_duplicates if t)
        if listed:
            sections.append(
                "These titles are already taken — a near-match to any of them "
                f"is a duplicate, so restate none of them:\n{listed}"
            )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Settings-aware entry points
# ---------------------------------------------------------------------------


def _resolve_setting(site_config: Any, getter: str, key: str, default: Any) -> Any:
    """Read one app_settings value, tolerating a stubbed/absent site_config.

    Test doubles and bootstrap paths pass a partial ``site_config``; falling
    back to the module default keeps the avoidance block working rather than
    dropping it (a dropped block is silent — the exact failure this module
    exists to end).
    """
    if site_config is None:
        return default
    try:
        return getattr(site_config, getter)(key, default)
    except Exception as exc:  # noqa: BLE001 — stubbed site_config in tests
        logger.debug(
            "[title_avoidance] %s unreadable (%s) — using default %r",
            key, type(exc).__name__, default,
        )
        return default


def get_recent_count(site_config: Any) -> int:
    """Window size for the recent-title lookup."""
    value = _resolve_setting(
        site_config, "get_int", "title_avoidance_recent_count",
        DEFAULT_RECENT_COUNT,
    )
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_RECENT_COUNT


def get_mode(site_config: Any) -> str:
    """Avoidance strategy: ``patterns`` | ``titles`` | ``both`` | ``off``."""
    raw = _resolve_setting(
        site_config, "get", "title_avoidance_mode", "patterns"
    )
    mode = str(raw or "patterns").strip().lower()
    if mode not in {"patterns", "titles", "both", "off"}:
        logger.warning(
            "[title_avoidance] unknown title_avoidance_mode=%r — falling back "
            "to 'patterns'", raw,
        )
        return "patterns"
    return mode


def build_avoidance_block(
    titles: Sequence[str],
    *,
    site_config: Any = None,
    near_duplicates: Sequence[str] = (),
) -> str:
    """Recent titles → the prompt block, honouring ``title_avoidance_mode``.

    The single entry point every title-producing path calls. Returns ``""``
    when the mode is ``off`` or the window yields nothing worth saying — with
    the exception of ``near_duplicates``, which always render because a
    confirmed collision outranks the variety strategy.
    """
    mode = get_mode(site_config)
    if mode == "off":
        return render_avoidance_block(
            TitleCorpusProfile(sample_size=0), near_duplicates=near_duplicates
        )

    threshold = _resolve_setting(
        site_config, "get_float", "title_avoidance_pattern_threshold",
        DEFAULT_PATTERN_THRESHOLD,
    )
    lexical_min = _resolve_setting(
        site_config, "get_int", "title_avoidance_lexical_min_count",
        DEFAULT_LEXICAL_MIN_COUNT,
    )
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = DEFAULT_PATTERN_THRESHOLD
    try:
        lexical_min = int(lexical_min)
    except (TypeError, ValueError):
        lexical_min = DEFAULT_LEXICAL_MIN_COUNT

    profile = (
        analyze_title_patterns(
            titles, pattern_threshold=threshold, lexical_min_count=lexical_min
        )
        if mode in {"patterns", "both"}
        else TitleCorpusProfile(sample_size=0)
    )
    legacy = tuple(titles) if mode in {"titles", "both"} else ()
    return render_avoidance_block(
        profile, near_duplicates=near_duplicates, legacy_titles=legacy
    )


# ---------------------------------------------------------------------------
# Recent-title lookup
# ---------------------------------------------------------------------------

# ``content_tasks`` is the view over pipeline_tasks + pipeline_versions; the
# title column comes from the version row, so this reads the title the post
# actually shipped with. Niche-blind by design (matching the behaviour it
# replaces) — a cross-niche variety push is the desired signal.
_RECENT_TITLES_SQL = (
    "SELECT title FROM content_tasks WHERE status = 'published' "
    "ORDER BY created_at DESC LIMIT $1"
)


async def fetch_recent_titles(
    pool: Any, *, site_config: Any = None, source: str = "title_avoidance"
) -> list[str]:
    """Recent published titles, newest first. ``[]`` when unavailable.

    A failure here means the title step runs with no variety guidance and
    nothing says so, so the empty return is paired with a finding — same
    contract as the two call sites this consolidates.
    """
    if pool is None:
        return []
    limit = get_recent_count(site_config)
    if limit <= 0:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_RECENT_TITLES_SQL, limit)
        return [r["title"] for r in rows if r["title"]]
    except Exception as exc:  # noqa: BLE001 — degraded, never fatal
        from utils.findings import emit_finding

        logger.warning(
            "[title_avoidance] recent-title lookup failed (%s: %s) — the "
            "title prompt gets no variety guidance this run",
            type(exc).__name__, exc,
        )
        emit_finding(
            source=source,
            kind="title_history_lookup_failed",
            title="Recent-title lookup failed — title variety guidance skipped",
            body=(
                f"Reading recent published titles raised {type(exc).__name__}: "
                f"{exc}. The title prompt carries no variety guidance, so this "
                f"post's title was chosen without the diversity check."
            ),
            severity="info",
            dedup_key="title_history_lookup_failed",
            extra={"error_type": type(exc).__name__, "source": source},
        )
        return []


async def build_avoidance_block_for_pool(
    pool: Any,
    *,
    site_config: Any = None,
    near_duplicates: Sequence[str] = (),
    source: str = "title_avoidance",
) -> str:
    """``fetch_recent_titles`` + ``build_avoidance_block`` in one call."""
    titles = await fetch_recent_titles(
        pool, site_config=site_config, source=source
    )
    return build_avoidance_block(
        titles, site_config=site_config, near_duplicates=near_duplicates
    )


__all__ = [
    "DEFAULT_LEXICAL_MIN_COUNT",
    "DEFAULT_PATTERN_THRESHOLD",
    "DEFAULT_RECENT_COUNT",
    "TitleCorpusProfile",
    "analyze_title_patterns",
    "build_avoidance_block",
    "build_avoidance_block_for_pool",
    "fetch_recent_titles",
    "get_mode",
    "get_recent_count",
    "render_avoidance_block",
]
