"""Deterministic topic-sanity gate — blocks contentless topics before they
become ``pipeline_tasks`` rows.

Incident (2026-06-30): the dev.to tap surfaced a post titled
``". .. . ... . .... . .... . ... ."`` — dots only. Every downstream layer
assumed something upstream had validated content: embedding pre-rank didn't
sink it, the LLM final-scorer ranked it TOP of its batch (65 vs 40-48 for
real headlines), ``topic_auto_resolve`` promoted it, and a full
canonical_blog run burned GPU time writing an "article" seeded from garbage
(which pulled internal alert vocabulary into draft content) before the QA
rails finally rejected it at the last gate.

This module is the deterministic pre-task guard for that class. Per
``feedback_calculated_vs_generated``: an LLM scores whatever text it is
handed, so contentless-ness must be *calculated*. The rules are
intentionally minimal — sanity, not quality (thin-but-real topics stay the
QA rails' job):

1. Empty / whitespace-only topics are never valid.
2. Topics with zero alphabetic characters (punctuation / digits only) are
   never valid — unconditionally, regardless of configuration.
3. Topics with fewer than ``topic_sanity_min_alpha_words`` alphabetic words
   (default 2; an "alphabetic word" is a maximal run of >=2 unicode letters)
   are rejected. Operators tune via app_settings; ``0`` disables this rule
   (rules 1-2 still apply). Prod history backs the default: across 1,867
   pipeline_tasks, every topic under 2 alphabetic words ended
   rejected/cancelled — none ever published.
4. Whole-topic failure sentinels ("No topic found", "Untitled", "N/A", …)
   are never valid — LLM distillers emit their failure state as the topic
   string, and "No topic found" reached awaiting_approval on 2026-07-02
   (task 4b470976). Whole-topic match only: a real headline that merely
   contains the words passes.
5. Titles ending in an article/preposition/conjunction ("What to Learn to
   Be a", task 115646d1) are deterministically incomplete — a truncated
   distillation, not a topic. Skipped when the title ends with terminal
   punctuation (an intentional "…For?" style title is complete).

Call sites (every seam where a topic becomes a task):

- ``TopicBatchService._handoff_to_pipeline`` — batch winner → pipeline_tasks
  (raises :class:`TopicSanityError` + emits a ``topic_sanity_rejected``
  finding).
- ``TopicBatchService.run_sweep`` — candidate intake filter, so garbage
  never occupies a batch slot in the first place.
- ``topic_proposal_service.propose_topic`` — manual CLI path (raises; the
  operator is present to rephrase).
- ``services.jobs.topic_auto_resolve`` — catches :class:`TopicSanityError`
  and expires the batch so a garbage winner can't wedge the niche
  (per ``feedback_self_heal_not_suppress``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

# Default minimum count of alphabetic words for a topic to be considered
# sane. Operator-tunable via ``topic_sanity_min_alpha_words`` (seeded in
# ``settings_defaults.py``). CJK note: scripts without word spacing tokenise
# as one long letter-run — operators of non-spaced-script sites should set
# the key to 1.
DEFAULT_MIN_ALPHA_WORDS = 2
MIN_ALPHA_WORDS_KEY = "topic_sanity_min_alpha_words"

# A maximal run of >=2 unicode letters. ``[^\W\d_]`` is "\w minus digits and
# underscore" — i.e. letters in any script. Mirrors the SQL heuristic used
# to size the problem: ``regexp_matches(topic, '[[:alpha:]]{2,}', 'g')``.
_ALPHA_WORD_RE = re.compile(r"[^\W\d_]{2,}")

REASON_EMPTY = "empty"
REASON_NO_ALPHA = "no_alphabetic_content"
REASON_TOO_FEW = "too_few_alpha_words"
REASON_SENTINEL = "failure_sentinel"
REASON_TRUNCATED = "truncated_title"

# Whole-topic failure sentinels — the bounded set of strings LLM distillers
# emit INSTEAD of a topic when they fail. Matched against the normalised
# (lowercased, whitespace-collapsed, trailing-punctuation-stripped) full
# topic only, never as a substring.
_FAILURE_SENTINELS = frozenset({
    "no topic found",
    "no clear topic",
    "not found",
    "untitled",
    "n/a",
    "none",
    "unknown",
    "tbd",
    "insufficient information",
    "error",
})

# A title whose last word is one of these is a clause cut mid-phrase
# ("What to Learn to Be a"). Articles, common prepositions, conjunctions,
# and bare copulas — words that grammatically require a continuation.
_TRAILING_STOPWORDS = frozenset({
    "a", "an", "the",
    "to", "of", "for", "in", "on", "at", "by", "with", "from",
    "and", "or", "but",
    "is", "are", "was", "were", "be",
})

# Titles ending with terminal punctuation are treated as intentionally
# complete — "...For?" is a deliberate title shape, not a truncation.
_TERMINAL_PUNCT = ".!?\"'’”)…"

_TRAILING_NONWORD_RE = re.compile(r"[\W_]+$")


@dataclass(frozen=True)
class TopicSanityResult:
    """Verdict of :func:`evaluate_topic_sanity` — machine-routable + human-readable."""

    ok: bool
    # None | REASON_EMPTY | REASON_NO_ALPHA | REASON_TOO_FEW |
    # REASON_SENTINEL | REASON_TRUNCATED
    reason: str | None
    alpha_word_count: int
    detail: str


class TopicSanityError(ValueError):
    """A topic failed the sanity gate at a task-creation seam.

    ``ValueError`` subclass so existing adapter contracts hold unchanged:
    the resolve/propose HTTP routes map ``ValueError`` → 400 and the CLI
    prints it as a friendly error. ``topic_auto_resolve`` catches this
    type specifically to expire the offending batch instead of retrying
    it every cycle.
    """

    def __init__(self, topic: Any, result: TopicSanityResult) -> None:
        self.topic = topic
        self.result = result
        shown = repr(topic)
        if len(shown) > 120:
            shown = shown[:117] + "..."
        super().__init__(
            f"topic failed sanity gate ({result.reason}): {result.detail} "
            f"— topic={shown}"
        )


def count_alpha_words(topic: str | None) -> int:
    """Number of alphabetic words (maximal letter-runs of >=2 chars) in ``topic``."""
    return len(_ALPHA_WORD_RE.findall(topic or ""))


def evaluate_topic_sanity(
    topic: str | None,
    *,
    min_alpha_words: int = DEFAULT_MIN_ALPHA_WORDS,
) -> TopicSanityResult:
    """Deterministically judge whether ``topic`` has enough content to write about.

    Pure function — no DB, no LLM, no clock. See the module docstring for
    the three rules and their rationale.
    """
    stripped = (topic or "").strip()
    if not stripped:
        return TopicSanityResult(
            ok=False,
            reason=REASON_EMPTY,
            alpha_word_count=0,
            detail="topic is empty or whitespace-only",
        )

    normalized = _TRAILING_NONWORD_RE.sub("", " ".join(stripped.lower().split()))
    if normalized in _FAILURE_SENTINELS:
        return TopicSanityResult(
            ok=False,
            reason=REASON_SENTINEL,
            alpha_word_count=count_alpha_words(stripped),
            detail=(
                "topic is a distiller failure sentinel, not a topic "
                f"(matched {normalized!r})"
            ),
        )

    words = count_alpha_words(stripped)
    if not any(ch.isalpha() for ch in stripped):
        return TopicSanityResult(
            ok=False,
            reason=REASON_NO_ALPHA,
            alpha_word_count=words,
            detail=(
                "topic contains no alphabetic characters "
                "(punctuation/digits/whitespace only)"
            ),
        )

    if min_alpha_words > 0 and words < min_alpha_words:
        return TopicSanityResult(
            ok=False,
            reason=REASON_TOO_FEW,
            alpha_word_count=words,
            detail=(
                f"topic has {words} alphabetic word(s); at least "
                f"{min_alpha_words} required ({MIN_ALPHA_WORDS_KEY})"
            ),
        )

    if stripped[-1] not in _TERMINAL_PUNCT:
        last_token = _TRAILING_NONWORD_RE.sub("", stripped.split()[-1]).lower()
        if last_token in _TRAILING_STOPWORDS:
            return TopicSanityResult(
                ok=False,
                reason=REASON_TRUNCATED,
                alpha_word_count=words,
                detail=(
                    f"topic ends mid-phrase on {last_token!r} — a truncated "
                    "distillation, not a complete title"
                ),
            )

    return TopicSanityResult(
        ok=True, reason=None, alpha_word_count=words, detail="ok",
    )


def resolve_min_alpha_words(site_config: Any) -> int:
    """Read ``topic_sanity_min_alpha_words`` off ``site_config``, defaulting safely.

    Mirrors the tolerance of ``topic_proposal_service.resolve_max_pending``:
    accepts a full ``SiteConfig`` (``get_int``), a dict-style stub
    (``get``), or ``None`` — a misconfigured value falls back to the
    default with a warning rather than letting the gate fail open/closed
    unpredictably.
    """
    if site_config is None:
        return DEFAULT_MIN_ALPHA_WORDS
    if hasattr(site_config, "get_int"):
        try:
            return int(
                site_config.get_int(MIN_ALPHA_WORDS_KEY, DEFAULT_MIN_ALPHA_WORDS)
            )
        except Exception:
            logger.warning(
                "[topic_sanity] %s coerce failed via get_int; using default %d",
                MIN_ALPHA_WORDS_KEY, DEFAULT_MIN_ALPHA_WORDS,
            )
            return DEFAULT_MIN_ALPHA_WORDS
    raw = site_config.get(MIN_ALPHA_WORDS_KEY, DEFAULT_MIN_ALPHA_WORDS)
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning(
            "[topic_sanity] %s=%r not an int; using default %d",
            MIN_ALPHA_WORDS_KEY, raw, DEFAULT_MIN_ALPHA_WORDS,
        )
        return DEFAULT_MIN_ALPHA_WORDS


__all__ = [
    "DEFAULT_MIN_ALPHA_WORDS",
    "MIN_ALPHA_WORDS_KEY",
    "REASON_EMPTY",
    "REASON_NO_ALPHA",
    "REASON_SENTINEL",
    "REASON_TOO_FEW",
    "REASON_TRUNCATED",
    "TopicSanityError",
    "TopicSanityResult",
    "count_alpha_words",
    "evaluate_topic_sanity",
    "resolve_min_alpha_words",
]
