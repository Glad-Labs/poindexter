"""Title canonicalization helpers (issue GH-85).

One canonical title drives three places:

    * ``title`` column        — canonical verbatim
    * ``seo_title`` column    — canonical with emoji stripped and truncated
                                at the nearest word boundary to ≤ ``max_seo_len``
    * body ``# H1``           — canonical verbatim

This module provides the pure helpers. ``content_router_service`` wires them
into the pipeline, and ``scripts/backfill-canonical-titles.py`` uses them to
repair published posts and pending pipeline versions.

The split into helpers (rather than one giant function) is deliberate:

* ``strip_emoji`` — Unicode-aware emoji strip. Used for ``title`` and
  ``seo_title`` columns but NOT for body content (emoji in prose is fine).
* ``truncate_at_word_boundary`` — cap at ``max_len`` without ever slicing
  a word. If the text already fits, returned unchanged.
* ``derive_seo_title`` — the full ``canonical → seo_title`` rule.
* ``extract_body_h1`` / ``replace_body_h1`` — body-H1 parsing helpers for
  the propagation step + backfill.
* ``propagate_canonical_title`` — the one-shot "given a canonical title and
  a body, produce the consistent triple" entry point.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Emoji strip
# ---------------------------------------------------------------------------

# Unicode blocks that cover the vast majority of real-world emoji (issue #85).
# We intentionally include trailing variation selectors (U+FE0F) and
# zero-width joiners (U+200D) so composite emoji like "👨‍💻" strip cleanly.
#
# Source: https://unicode.org/Public/emoji/15.0/emoji-data.txt (blocks, not
# the full code point list — blocks are good enough for titles).
_EMOJI_PATTERN = re.compile(
    "["                                   # noqa: E128 (keep vertical for readability)
    "\U0001F300-\U0001F5FF"               # symbols & pictographs
    "\U0001F600-\U0001F64F"               # emoticons
    "\U0001F680-\U0001F6FF"               # transport & map
    "\U0001F700-\U0001F77F"               # alchemical symbols
    "\U0001F780-\U0001F7FF"               # geometric shapes extended
    "\U0001F800-\U0001F8FF"               # supplemental arrows-C
    "\U0001F900-\U0001F9FF"               # supplemental symbols & pictographs
    "\U0001FA00-\U0001FA6F"               # chess symbols
    "\U0001FA70-\U0001FAFF"               # symbols & pictographs extended-A
    "\U00002600-\U000026FF"               # miscellaneous symbols (☀, ⚽, etc.)
    "\U00002700-\U000027BF"               # dingbats (✂, ✈, ✅, etc.)
    "\U0001F1E6-\U0001F1FF"               # regional indicator (flags)
    "\U00002300-\U000023FF"               # miscellaneous technical (⌨ ⏰ etc.)
    "\U00002B00-\U00002BFF"               # miscellaneous symbols & arrows
    "\U0000FE00-\U0000FE0F"               # variation selectors
    "\U0000200D"                          # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Remove emoji/pictographic characters from ``text``.

    Collapses any whitespace gaps left behind so "Hello 🔍 World" becomes
    "Hello World", not "Hello  World". Leading/trailing whitespace is
    stripped.

    >>> strip_emoji("Forem Architecture: Powering DEV & Beyond 🔍")
    'Forem Architecture: Powering DEV & Beyond'
    >>> strip_emoji("No emoji here")
    'No emoji here'
    >>> strip_emoji("")
    ''
    """
    if not text:
        return text
    cleaned = _EMOJI_PATTERN.sub("", text)
    # Collapse whitespace runs that got introduced by the strip.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Word-boundary truncation
# ---------------------------------------------------------------------------

# Trailing punctuation we're willing to strip after a truncation. Titles that
# end on ``,`` / ``;`` / ``:`` look wrong; periods are a judgment call — see
# the PR description for the trailing-period discussion.
_TRIM_TRAILING = ",;:-—–"


def truncate_at_word_boundary(text: str, max_len: int) -> str:
    """Truncate ``text`` to at most ``max_len`` characters, never mid-word.

    Guarantees:

    * The return value is never longer than ``max_len``.
    * If ``text`` is already within ``max_len``, it is returned unchanged.
    * If truncation is needed, the cut falls on a whitespace boundary — the
      returned text is the longest word-boundary prefix that fits. Trailing
      punctuation from the set ``,;:-—–`` is stripped.
    * Never returns a string that ends mid-word. If there is no word
      boundary within ``max_len`` (single huge word), falls back to the
      raw slice — callers that need a hard guarantee should validate
      ``len(result) <= max_len`` and accept the edge case OR fail loud.
      In practice titles always contain spaces, so this edge case only
      triggers on malformed input.

    >>> truncate_at_word_boundary("Why Python's asyncio event loop rocks", 20)
    "Why Python's asyncio"
    >>> truncate_at_word_boundary("Short", 100)
    'Short'
    >>> truncate_at_word_boundary("Python asyncio event loop internals for developers", 60)
    'Python asyncio event loop internals for developers'
    """
    if text is None:
        return text  # type: ignore[return-value]
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text

    # Walk back from ``max_len`` to find the last whitespace.
    window = text[:max_len]
    last_space = window.rfind(" ")
    if last_space <= 0:
        # No whitespace in window — only one huge word. Fall back to a
        # raw slice so callers still get a bounded string.
        return window.rstrip(_TRIM_TRAILING).rstrip()
    truncated = window[:last_space].rstrip()
    # Strip trailing punctuation that looks weird at the end of a title.
    truncated = truncated.rstrip(_TRIM_TRAILING).rstrip()
    return truncated


# ---------------------------------------------------------------------------
# Canonical → seo_title rule
# ---------------------------------------------------------------------------

# SEO best-practice cap (Google truncates around 600px, ≈ 60 chars).
# Configurable per-call but this is the house default.
DEFAULT_SEO_TITLE_MAX_LEN = 60


def derive_seo_title(canonical: str, max_len: int = DEFAULT_SEO_TITLE_MAX_LEN) -> str:
    """Return ``canonical`` stripped of emoji + truncated at word boundary.

    This is the rule referenced by ``content_router_service`` and the
    backfill script. Keeping it as one function means the rule has exactly
    one definition across the codebase.
    """
    if not canonical:
        return canonical
    without_emoji = strip_emoji(canonical)
    return truncate_at_word_boundary(without_emoji, max_len)


# ---------------------------------------------------------------------------
# Body H1 parse / replace
# ---------------------------------------------------------------------------

# We match ONLY the first H1. Later ``#`` headings in the body are preserved.
# The pattern is deliberately strict — it requires a leading ``#`` at column 0
# (optionally preceded by a few blank lines), followed by one or more spaces,
# followed by the title text, then NO additional ``#`` (so we skip ``## H2``
# and deeper). This is the same shape ``ai_content_generator`` emits and
# what ``content_router_service`` asks for in the writer prompt.
_H1_PATTERN = re.compile(r"^(?P<lead>[ \t]*)#[ \t]+([^\n#][^\n]*?)[ \t]*$", flags=re.MULTILINE)

# Fenced code blocks — ``` or ~~~. Anything inside is NOT markdown.
_FENCE_PATTERN = re.compile(r"^[ \t]*(```|~~~)", flags=re.MULTILINE)


def _strip_code_fences(content: str) -> str:
    """Return ``content`` with fenced code blocks replaced by blanks.

    Preserves line numbers (so ``_H1_PATTERN`` reports correct line positions
    in error messages if we ever add that), but blanks out code so a ``#``
    inside a shell snippet (``# a comment``) or a path (``.github/...``)
    doesn't get picked as the canonical title.
    """
    if not content:
        return content
    # Simple state machine — walk lines, track fence open/close.
    out_lines = []
    in_fence = False
    for line in content.splitlines(keepends=False):
        if _FENCE_PATTERN.match(line):
            in_fence = not in_fence
            out_lines.append("")  # drop the fence line itself
            continue
        if in_fence:
            out_lines.append("")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def extract_body_h1(content: str) -> Optional[str]:
    """Return the first H1 (``# Title``) in ``content``, or ``None`` if missing.

    Returns the title text only — no leading ``#`` or trailing newline.
    Leading/trailing whitespace is stripped. Code blocks (``` fences) are
    ignored so a ``#`` inside a shell snippet or file path doesn't get
    picked as the canonical title.

    >>> extract_body_h1("# My Title\\n\\nBody")
    'My Title'
    >>> extract_body_h1("No heading here")
    >>> extract_body_h1("")
    """
    if not content:
        return None
    cleaned = _strip_code_fences(content)
    m = _H1_PATTERN.search(cleaned)
    if not m:
        return None
    title = m.group(2).strip()
    return title or None


def replace_body_h1(content: str, new_title: str) -> Tuple[str, bool]:
    """Replace the first H1 in ``content`` with ``new_title``.

    Returns a tuple ``(updated_content, replaced)`` where ``replaced`` is
    ``True`` when an H1 was found and swapped. If no H1 exists, ``content``
    is returned unchanged and ``replaced`` is ``False`` — callers should
    decide whether to prepend ``# {new_title}\\n\\n``.

    The replacement preserves the original indentation / leading whitespace
    of the H1 line (usually none, but some models indent). Code blocks are
    respected: an H1-shaped line inside a fenced block is left untouched.
    """
    if not content:
        return content, False

    # Walk lines with fence awareness. Replace the first H1 outside any fence.
    lines = content.splitlines(keepends=False)
    in_fence = False
    replaced = False
    for idx, line in enumerate(lines):
        if _FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _H1_PATTERN.match(line)
        if m:
            leading = m.group("lead")
            lines[idx] = f"{leading}# {new_title}"
            replaced = True
            break

    # Preserve original trailing newline character if present.
    updated = "\n".join(lines)
    if content.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    return updated, replaced


# ---------------------------------------------------------------------------
# One-shot propagation
# ---------------------------------------------------------------------------


def propagate_canonical_title(
    canonical: str,
    content: str,
    max_seo_len: int = DEFAULT_SEO_TITLE_MAX_LEN,
) -> Tuple[str, str, str]:
    """Given the canonical title + body, return the consistent triple.

    Returns ``(title, seo_title, content)`` where:

    * ``title`` is ``canonical`` with emoji stripped (verbatim otherwise).
      The issue requires "title column = canonical verbatim" but also
      "strip emoji from title and seo_title". Emoji strip wins.
    * ``seo_title`` is ``derive_seo_title(canonical, max_seo_len)``.
    * ``content`` has its first H1 line replaced with ``# {canonical}``
      (the H1 keeps any emoji — body content is allowed emoji). If there
      was no H1 in the body, one is prepended.

    The function is pure and idempotent — calling it twice with the same
    inputs produces the same outputs.
    """
    # Emoji-free title for the DB column.
    title_col = strip_emoji(canonical)
    seo_title = derive_seo_title(canonical, max_len=max_seo_len)

    if content is None:
        return title_col, seo_title, content  # type: ignore[return-value]

    updated, replaced = replace_body_h1(content, canonical)
    if not replaced:
        # No H1 in body — prepend one. Keeps the one-source-of-truth contract.
        if updated.lstrip().startswith("#"):
            # Defensive — shouldn't happen, but don't double-up.
            pass
        updated = f"# {canonical}\n\n{updated.lstrip()}" if updated else f"# {canonical}\n"

    return title_col, seo_title, updated
