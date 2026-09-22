"""What makes a short-form hook good, in one place.

A Short's narration opens with one sentence, and since glad-labs-stack#3944
that sentence is published verbatim as the video's YouTube title. So it has
two jobs at once: it is the first thing a viewer hears, and it is the ~40
characters a Shorts feed shows them before they decide to keep watching.

This module holds the RULES — pure, deterministic, no LLM, no I/O — so the
script stage (which can repair a bad hook while the words are still text) and
the YouTube payload (which can only strip what it is handed) judge a hook the
same way.

Everything here was measured, not imagined. Over the ten most recent
published posts on 2026-09-22, with the production model (phi4:14b):

* a verbatim example sentence in the prompt was copied onto unrelated
  articles 4 times in 10 — which is why :func:`hook_defects` never takes a
  prompt's word for anything;
* with the example gone, 4 in 10 still opened "Discover how …" and one still
  used a run-up, both of which that prompt bans explicitly;
* 10 in 10 exceeded the ~40 characters the feed shows, median 57.

Instructions alone do not hold this line. The defects below are the line.
"""

from __future__ import annotations

import re
from typing import Any

# The feed shows roughly this much of a Shorts title before truncating. It is
# the budget that matters — the 100-char YouTube API cap is a different, far
# looser limit that the payload's suffix arithmetic already respects.
HOOK_MAX_CHARS_DEFAULT = 42
HOOK_MAX_WORDS_DEFAULT = 9
# Below this a "sentence" is a fragment, not a claim. Also the floor that
# stops a strip from eating the sentence it was meant to trim.
HOOK_MIN_WORDS = 3

# NOTE: both regexes accept a CURLY apostrophe as well as a straight one. The
# writer emits U+2019 ("In today’s digital age, ..."), so a class of only '
# let the exact shape these exist to catch walk straight through — 1 of 10 in
# the 2026-09-22 sweep.
#
# Scene-setting run-ups. Each alternative must be followed by a COMMA: that is
# what makes it a throat-clearing clause rather than the sentence's own
# subject ("Let's talk about zero-click content" has no comma, and cutting it
# would leave a noun phrase, not a claim).
_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"in today['’]s [\w'’ -]{2,30}"
    r"|in (?:the|this|an?) (?:world|age|era|day and age|modern era) of [\w'’ -]{2,40}"
    r"|in (?:the|this) (?:world|age|era|day and age|modern era)"
    r"|in this (?:article|video|post|short)"
    r"|these days|nowadays|as we all know|it['’]s no secret"
    r"|as (?:you|we) (?:probably )?(?:know|might know)"
    r")\s*,\s*",
    re.I,
)

# Openers that DESCRIBE the article instead of making its point. Unlike the
# run-ups these need no comma — they are a prefix, not a clause — and cutting
# one leaves the claim the sentence was already making.
_DESCRIBES_RE = re.compile(
    r"^(?:"
    r"(?:discover|learn|find out|see|explore|uncover)\s+(?:how|why|what|that)"
    r"|this (?:article|post|video|short)\s+(?:reveals|explains|shows|covers|looks at|explores)"
    r"\s*(?:how|why|what|that)?"
    r"|here['’]s (?:how|why|what)"
    r")\s+",
    re.I,
)

_QUESTION_RE = re.compile(
    r"^(?:ever\b|have you\b|what if\b|imagine\b|did you\b|why do\b|"
    r"are you\b|do you\b|can you\b)", re.I,
)

# Loose finite-verb probe: a claim almost always carries one of these, or a
# verb inflection. Deliberately permissive — it is here to catch a bare noun
# phrase ("Zero-click content and the open web"), not to grade grammar.
_VERBISH_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|has|have|had|does|do|did|will|can|could|"
    r"should|must|\w+ed|\w+ing|\w+s)\b", re.I,
)

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    """``text`` split on sentence ends, blanks dropped."""
    clean = (text or "").strip()
    return [p.strip() for p in _SENTENCE_END_RE.split(clean) if p.strip()] if clean else []


def first_sentence(text: str) -> str:
    parts = sentences(text)
    return parts[0] if parts else ""


def strip_preamble(sentence: str) -> str:
    """Drop a leading run-up or describes-the-article prefix, re-capitalising.

    * a run-up is a CLAUSE ending at a comma —
      ``"In today's digital age, zero-click content is the new standard."``
      -> ``"Zero-click content is the new standard."``
    * a describes-the-article opener is a bare PREFIX —
      ``"Discover how a GPU lock bug was wrecking our RAG sweep"`` -> ``"A GPU
      lock bug was wrecking our RAG sweep"``.

    Unchanged when nothing matches, or when fewer than
    :data:`HOOK_MIN_WORDS` would survive (a strip that leaves two words has
    cut the sentence, not its run-up).
    """
    clean = (sentence or "").strip()
    stripped = _PREAMBLE_RE.sub("", clean, count=1).strip()
    if stripped == clean:
        stripped = _DESCRIBES_RE.sub("", clean, count=1).strip()
    if stripped == clean or len(stripped.split()) < HOOK_MIN_WORDS:
        return clean
    return stripped[:1].upper() + stripped[1:]


def _title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) > 3}


def hook_defects(
    sentence: str,
    *,
    max_chars: int = HOOK_MAX_CHARS_DEFAULT,
    max_words: int = HOOK_MAX_WORDS_DEFAULT,
    article_title: str = "",
) -> tuple[str, ...]:
    """Every reason ``sentence`` is a poor Short hook, most structural first.

    Judged AFTER :func:`strip_preamble`, because the payload strips before
    publishing — a sentence the strip rescues is not a defect worth an LLM
    call. Returns ``()`` for a hook that needs no repair.

    Deliberately NOT a score: a caller repairing a hook wants to know which
    rule to state, and an operator reading a finding wants the same.
    """
    clean = strip_preamble(sentence)
    if not clean:
        return ("empty",)
    words = clean.split()
    out: list[str] = []
    if _QUESTION_RE.match(clean) or clean.rstrip().endswith("?"):
        out.append("question")
    if _DESCRIBES_RE.match(clean):
        out.append("describes_article")   # survived the strip (too short to cut)
    if _PREAMBLE_RE.match(clean):
        out.append("run_up")
    if len(words) < HOOK_MIN_WORDS:
        out.append("fragment")
    elif not _VERBISH_RE.search(clean):
        out.append("not_a_claim")
    if len(words) > max_words:
        out.append("too_many_words")
    if len(clean.rstrip(".").strip()) > max_chars:
        out.append("too_long")
    if article_title:
        # A hook that is just the article's title restates rather than hooks;
        # 3 of 10 did this in the sweep. Compared on content words so a shared
        # subject alone is fine.
        hw, tw = _title_words(clean), _title_words(article_title)
        if hw and tw and hw <= tw:
            out.append("restates_title")
    return tuple(out)


# Length is a SHORTENING problem, not a generation problem. Measured
# 2026-09-22: asked for <=42 chars, phi4:14b returned 59-92 and
# gemma-4-31B restated the prompt — but phi4's sentences were good claims,
# just long. So an LLM repair fires only for a CONTENT defect; the title
# builder shortens at a word boundary, and the narration keeps the full
# sentence (the viewer reads a prefix of what they hear).
CONTENT_DEFECTS = frozenset({
    "empty", "fragment", "question", "describes_article", "run_up",
    "not_a_claim", "restates_title",
})


def content_defects(sentence: str, **kw: Any) -> tuple[str, ...]:
    """Only the defects another generation could fix."""
    return tuple(d for d in hook_defects(sentence, **kw) if d in CONTENT_DEFECTS)


def is_acceptable_hook(sentence: str, **kw: Any) -> bool:
    return not hook_defects(sentence, **kw)


def hook_limits(site_config: Any) -> tuple[int, int]:
    """``(max_chars, max_words)`` for this install."""
    def _int(key: str, default: int) -> int:
        if site_config is None:
            return default
        try:
            return max(1, int(str(site_config.get(key, default)).strip()))
        except (TypeError, ValueError):
            return default
    return (
        _int("media.short_hook.max_chars", HOOK_MAX_CHARS_DEFAULT),
        _int("media.short_hook.max_words", HOOK_MAX_WORDS_DEFAULT),
    )


__all__ = [
    "CONTENT_DEFECTS",
    "HOOK_MAX_CHARS_DEFAULT",
    "HOOK_MAX_WORDS_DEFAULT",
    "HOOK_MIN_WORDS",
    "first_sentence",
    "content_defects",
    "hook_defects",
    "hook_limits",
    "is_acceptable_hook",
    "sentences",
    "strip_preamble",
]
