"""Markdown normalizer (gh#191).

Writer LLMs often produce markdown like:

    By the end of this guide, you will understand:
    *   Why X
    *   Why Y

with no blank line between the introductory line and the first bullet.
python-markdown (even with the ``sane_lists`` extension) requires a
blank line before a list to recognize it; without one, the bullets
render as literal asterisks inside a single ``<p>``.

This module rewrites such content so a blank line is inserted before
any bullet/numbered list that follows a non-blank, non-list line.
Idempotent — running on already-correct content is a no-op.

Lines inside fenced code blocks (``` or ~~~) are left untouched.

Public API: ``normalize_markdown(text)``.
"""

from __future__ import annotations

import re

# Bullet marker: line whose first non-whitespace token is *, -, or +
# followed by at least one space and at least one printable character.
# Excludes ``**bold**`` (no trailing space after the second *) and
# horizontal rules like ``---`` (no trailing content).
_BULLET_RE = re.compile(r"^[ \t]*[\*\-\+][ \t]+\S")

# Numbered list: line whose first non-whitespace token is "\d+." or
# "\d+)" followed by space + content.
_NUMBERED_RE = re.compile(r"^[ \t]*\d+[\.\)][ \t]+\S")

# Code fence open/close (matches both ``` and ~~~ optionally followed by
# a language tag).
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


def _is_list_item(line: str) -> bool:
    return bool(_BULLET_RE.match(line) or _NUMBERED_RE.match(line))


def _is_blank(line: str) -> bool:
    return not line.strip()


def normalize_markdown(text: str) -> str:
    """Insert a blank line before any bullet/numbered list that's missing one.

    Idempotent. Code-fence-safe. Leaves all other formatting untouched.
    """
    if not text:
        return text

    lines = text.splitlines(keepends=False)
    out: list[str] = []
    in_fence = False

    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue

        if in_fence:
            out.append(line)
            continue

        if _is_list_item(line):
            # Need a blank line above. Skip if previous emitted line is
            # blank, another list item, or this is the very first line.
            if out:
                prev = out[-1]
                if not _is_blank(prev) and not _is_list_item(prev):
                    out.append("")
        out.append(line)

    # Preserve trailing newline behavior of the input.
    result = "\n".join(out)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


__all__ = ["normalize_markdown"]
