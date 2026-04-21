"""Alt-text sanitation and budget-aware summarization.

Fixes GitHub issue Glad-Labs/poindexter#84: pipeline stage tokens
(``||sdxl:*||``, ``||pexels:*||``) were leaking into the ``alt`` attribute
of inline ``<img>`` tags, and alt strings were being mid-word-truncated
via ``.slice(0, N)``.

This module is the single source of truth for:

1. :func:`strip_pipeline_tokens` — remove ``||provider:hint||`` markers.
2. :func:`sanitize_alt_text`     — produce a budgeted, complete-sentence
   alt string without mid-word truncation.
3. :func:`strip_tokens_from_img_tags` — scrub every ``alt="..."`` in a
   rendered block of HTML/Markdown (used post image-stage finalization
   and by the one-shot backfill script).
4. :func:`assert_alt_text_clean` — loud-fail assertion for the pipeline;
   shipping a broken alt is worse than failing the task.

Budget default is ``alt_text_budget`` in ``app_settings`` (default 120
chars) — read via ``site_config`` per project convention. No env-var
reads, no silent fallbacks that mask misconfiguration.
"""

from __future__ import annotations

import re

# Matches ``||<lowercase-provider>:<anything-without-pipe>||``
# Examples: ``||sdxl:blueprint||``, ``||pexels:screens with code||``
# The provider segment is lowercase a-z; content between ``:`` and the
# next ``||`` can include spaces and punctuation but never a pipe.
_PIPELINE_TOKEN_RE = re.compile(r"\|\|[a-z]+:[^|]+\|\|")

# Matches an ``<img ... alt="..." ...>`` tag and captures the alt contents.
# We handle only double-quoted alt values because the pipeline never
# emits single-quoted HTML attrs.
_IMG_ALT_RE = re.compile(r'(<img\b[^>]*?\balt=")([^"]*)(")', re.IGNORECASE)

# A word-char at the tail is fine only when followed by ending
# punctuation (``. ! ?``) — otherwise we suspect a mid-word chop.
_WORD_CHAR_RE = re.compile(r"\w")


def strip_pipeline_tokens(text: str) -> str:
    """Remove ``||provider:hint||`` stage markers from a string.

    Works regardless of which provider emitted the token — both
    ``||sdxl:*||`` and ``||pexels:*||`` are stripped. Collapses the
    whitespace the token leaves behind so ``foo ||x:y|| bar`` becomes
    ``foo bar`` (not ``foo  bar``), and a trailing token like
    ``foo. ||x:y||`` becomes ``foo.`` (no dangling space).
    """
    if not text:
        return text
    out = _PIPELINE_TOKEN_RE.sub("", text)
    # Collapse runs of whitespace created by the removal.
    out = re.sub(r"[ \t]{2,}", " ", out)
    # Strip space-before-punctuation artefacts: "foo ." -> "foo."
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    return out.strip()


def _looks_mid_word_truncated(alt: str, budget: int) -> bool:
    """True when *alt* appears cut mid-word at the budget boundary.

    A clean, complete alt either ends with sentence punctuation or
    isn't at the budget ceiling. If the string is exactly at budget
    AND the last char is a word character, we treat that as a chop.
    """
    if not alt:
        return False
    if len(alt) < budget:
        return False
    last = alt[-1]
    if last in ".!?":
        return False
    return bool(_WORD_CHAR_RE.match(last))


def _ends_with_pipe(alt: str) -> bool:
    return bool(alt) and alt.rstrip().endswith("|")


def assert_alt_text_clean(alt: str, budget: int) -> None:
    """Loud-fail if *alt* looks broken.

    Raises :class:`ValueError` when the alt attribute:

    * still contains a pipeline token (``||provider:hint||``),
    * ends with a literal ``|`` character,
    * looks mid-word-chopped at *budget* chars.

    Shipping a post with a broken alt (screen-reader junk, SEO damage)
    is worse than failing the task — this is the pipeline's last line
    of defence before publish.
    """
    if alt is None:
        return  # Empty alts are legal (decorative images).
    if _PIPELINE_TOKEN_RE.search(alt):
        raise ValueError(
            f"Alt text still contains pipeline token(s): {alt!r}. "
            f"strip_pipeline_tokens() should have run before this point."
        )
    if _ends_with_pipe(alt):
        raise ValueError(
            f"Alt text ends with '|' — pipeline token was half-stripped: {alt!r}"
        )
    if _looks_mid_word_truncated(alt, budget):
        raise ValueError(
            f"Alt text appears mid-word-truncated at budget={budget}: {alt!r}. "
            f"Use sanitize_alt_text() to produce a complete sentence instead of "
            f"slicing a longer draft."
        )


def _fallback_alt(topic: str | None, budget: int) -> str:
    """Short, safe alt when the draft is unusable."""
    topic = (topic or "").strip() or "article illustration"
    # Keep it well under budget and always a complete phrase.
    base = f"Illustration for {topic}"
    if len(base) <= budget:
        return base
    # If topic itself is huge, trim on a word boundary and add ellipsis.
    head = base[: max(1, budget - 3)]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return f"{cut}..."


def sanitize_alt_text(
    draft: str | None,
    *,
    budget: int,
    topic: str | None = None,
) -> str:
    """Produce a clean alt string within *budget* characters.

    Steps (in order):

    1. Strip pipeline stage tokens.
    2. Strip leading ``IMAGE:`` / ``FIGURE:`` artefacts the LLM sometimes
       emits.
    3. Collapse whitespace + newlines.
    4. If ``len <= budget``: return as-is.
    5. Try to truncate on a **sentence** boundary within budget.
    6. Try to truncate on a **word** boundary within budget
       (with trailing ``...``).
    7. Fall back to a topic-based template — never return a mid-word chop.

    *budget* is the character budget for the final alt text. Caller is
    expected to pass ``site_config.get_int("alt_text_budget", 120)``.
    """
    if not draft or not draft.strip():
        return _fallback_alt(topic, budget)

    cleaned = strip_pipeline_tokens(draft)
    cleaned = re.sub(
        r"^(?:IMAGE|FIGURE|Image|Figure)\s*[-:]\s*", "", cleaned
    ).strip()
    cleaned = cleaned.replace("\n", " ").replace("[", "").replace("]", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return _fallback_alt(topic, budget)

    if len(cleaned) <= budget:
        return cleaned

    # Prefer a sentence boundary.
    head = cleaned[:budget]
    for punct in (". ", "! ", "? "):
        idx = head.rfind(punct)
        if idx > budget // 2:  # Don't chop down to a tiny fragment.
            return cleaned[: idx + 1].strip()

    # Fall back to a word boundary within budget.
    # Reserve 3 chars for "..." so we signal "see article for more".
    room = budget - 3
    if room > 0:
        word_cut = cleaned[:room]
        space_idx = word_cut.rfind(" ")
        if space_idx > room // 2:  # Skip if that'd be too aggressive.
            return word_cut[:space_idx].rstrip(" ,;:") + "..."

    # Last resort — topic-based fallback (never mid-word).
    return _fallback_alt(topic, budget)


def strip_tokens_from_img_tags(content: str) -> str:
    """Strip pipeline tokens from every ``<img ... alt="..." ...>`` tag.

    Idempotent — safe to run multiple times, safe to run over content
    that doesn't have any tokens.

    Only the ``alt`` attribute is touched; ``src`` and other attributes
    are left intact.
    """
    if not content:
        return content

    def _scrub(match: re.Match) -> str:
        prefix, alt_value, suffix = match.group(1), match.group(2), match.group(3)
        cleaned = strip_pipeline_tokens(alt_value)
        # Collapse any double-spaces introduced by the strip.
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return f"{prefix}{cleaned}{suffix}"

    return _IMG_ALT_RE.sub(_scrub, content)


def iter_img_alts(content: str):
    """Yield every ``alt`` attribute value from ``<img>`` tags in *content*.

    Helper for pipeline assertions and the backfill script's reporting.
    """
    if not content:
        return
    for match in _IMG_ALT_RE.finditer(content):
        yield match.group(2)
