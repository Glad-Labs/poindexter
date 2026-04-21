"""
Excerpt generator — produces 1-2 sentence article summaries for index
pages, RSS feeds, and social previews.

Runs inline in the writer/finalize stage (no extra LLM call). The prior
frontend fallback of ``content[:N]`` was broken because our posts open
with a markdown "What You'll Learn" bullet list — which reads badly as
a social-card snippet. This module strips markdown structure, skips the
"What You'll Learn" / outline section, and lifts the first real prose
sentences up to a configurable target length.

Tunables live in app_settings via site_config:

    excerpt_target_length   — target characters, default 200 (GH-86)
    excerpt_min_length      — min characters, default 140
    excerpt_max_length      — hard max, default 240

The generator is deterministic — same input always produces the same
excerpt — which keeps QA stable across reruns.
"""

from __future__ import annotations

import re

# Section headers we refuse to use as excerpts. Matched case-insensitively
# against the line text (minus leading # marks / bullet markers). The
# "outline" style openings add no reader value as a teaser.
_OUTLINE_HEADERS = (
    "what you'll learn",
    "what you will learn",
    "what you'll get",
    "what you will get",
    "in this article",
    "in this post",
    "in this guide",
    "table of contents",
    "overview",
    "toc",
    "key takeaways",
    "tldr",
    "tl;dr",
)

# Minimum word count for a paragraph to count as "prose." Anything
# shorter is usually a heading, a bullet scrap, or a stray code label.
_MIN_PROSE_WORDS = 8


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting while preserving sentence structure."""
    if not text:
        return ""
    # Remove fenced code blocks entirely — they're useless as excerpts
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    # Remove inline code (keep contents without backticks)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove images: ![alt](url) -> ""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    # Replace links [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove bold/italic markers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Remove blockquote markers
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    # Remove HTML tags (alt text etc.)
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_outline_header(line: str) -> bool:
    """True if ``line`` is (or starts as) a section header we should skip.

    Matches when the cleaned line equals one of the outline phrases
    exactly, or when it's a short header-ish line that begins with one
    of them. Deliberately strict to avoid rejecting prose paragraphs
    that happen to contain phrases like "In this guide we..." mid-sentence.
    """
    first = (line.splitlines() or [""])[0]
    cleaned = first.strip().lstrip("#").strip()
    cleaned = cleaned.strip("*_").strip().rstrip(":").lower()
    if not cleaned:
        return False
    # Short lines that look like headers: exact match or starts-with.
    # A "header" is typically <= ~40 chars; anything longer is prose.
    if len(cleaned) > 40:
        return False
    for h in _OUTLINE_HEADERS:
        if cleaned == h or cleaned.startswith(h + " ") or cleaned.startswith(h + ":"):
            return True
    return False


def _split_into_sentences(text: str) -> list[str]:
    """Very small sentence tokenizer. Good enough for English prose."""
    if not text:
        return []
    # Split on sentence-ending punctuation followed by space + capital
    # letter. Avoids splitting "e.g." or "vs." mid-sentence.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    return [p.strip() for p in parts if p.strip()]


def _trim_to_length(
    text: str, target_length: int, max_length: int
) -> str:
    """Return ``text`` clipped to at most ``max_length`` characters.

    Prefers to end on a sentence boundary at or after ``target_length``.
    Falls back to a word boundary with an ellipsis if no sentence ending
    lands in range.
    """
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    # Try to end on a sentence boundary between target and max.
    window = text[: max_length + 1]
    # Look for the last sentence end at or after target_length
    best = -1
    for m in re.finditer(r"[.!?](?=\s|$)", window):
        if m.end() >= target_length:
            best = m.end()
            break
    if best > 0:
        return window[:best].rstrip()
    # No sentence end — fall back to last whitespace before max_length
    trimmed = text[:max_length]
    space = trimmed.rfind(" ")
    if space > target_length // 2:
        trimmed = trimmed[:space]
    return trimmed.rstrip().rstrip(",.;:-") + "..."


def generate_excerpt(
    title: str,
    content: str,
    *,
    target_length: int = 200,
    min_length: int = 140,
    max_length: int = 240,
) -> str:
    """Generate a reader-facing excerpt from article body text.

    Args:
        title: Article title. Used to reject excerpts that are just the
            title repeated — a common degenerate case when the opening
            paragraph is "# Title" with no body yet.
        content: Full article markdown. Safe to pass the whole draft;
            only the first N paragraphs are consulted.
        target_length: Desired length in characters.
        min_length: Floor — if the first prose paragraph is shorter
            than this we keep appending sentences from the next
            paragraph(s) until we cross the floor.
        max_length: Hard cap. The excerpt is trimmed to at most this
            many characters, preferring a sentence boundary.

    Returns:
        A non-title, non-empty excerpt, or an empty string when no
        prose content can be extracted (caller should fall back to SEO
        description or leave the field NULL — but the normal pipeline
        case always produces a real excerpt).
    """
    if not content or not content.strip():
        return ""
    title_norm = (title or "").strip().lower()

    # Walk the content paragraph by paragraph. Skip markdown headers,
    # outline-style section openers, bullet lists, and anything that
    # doesn't read like prose.
    paragraphs = re.split(r"\n\s*\n", content)
    collected: list[str] = []
    current_len = 0

    for raw in paragraphs:
        para = raw.strip()
        if not para:
            continue
        # Is this a markdown header line? Skip.
        if para.lstrip().startswith("#"):
            # But also skip the NEXT paragraph if the header was an
            # outline-style header (by peeking at the header text).
            if _is_outline_header(para):
                continue
            continue
        # Entire paragraph that looks like an outline header on its own
        if _is_outline_header(para):
            continue
        # Bulleted/numbered lists: skip. The first bullet line rarely
        # reads as a standalone teaser sentence.
        first_line = para.splitlines()[0].lstrip()
        if first_line.startswith(("- ", "* ", "+ ")) or re.match(
            r"^\d+[\.\)]\s", first_line
        ):
            continue
        # Code fence opening
        if para.startswith("```"):
            continue

        cleaned = _strip_markdown(para)
        if not cleaned:
            continue
        # Short scraps are likely captions, image alts, or table cells
        if len(cleaned.split()) < _MIN_PROSE_WORDS:
            continue
        # Reject paragraphs that are just the title repeated
        if cleaned.strip().lower() == title_norm:
            continue

        # Take sentences from this paragraph until we cross min_length.
        for sentence in _split_into_sentences(cleaned):
            if not sentence:
                continue
            if sentence.lower() == title_norm:
                continue
            collected.append(sentence)
            current_len += len(sentence) + 1
            if current_len >= min_length:
                break

        if current_len >= min_length:
            break
        # Else loop into next paragraph — we need more text.

    if not collected:
        # Last-ditch: strip the whole content and drop any substring
        # identical to the title before trimming. A title-only excerpt
        # is worse than empty because it passes NOT NULL checks while
        # still reading like garbage on index pages.
        fallback = _strip_markdown(content)
        if title_norm:
            # Remove the title (case-insensitive) if it's all we have
            pattern = re.escape(title or "")
            fallback = re.sub(pattern, "", fallback, flags=re.IGNORECASE).strip()
        if not fallback:
            return ""
        return _trim_to_length(fallback, target_length, max_length)

    joined = " ".join(collected).strip()
    trimmed = _trim_to_length(joined, target_length, max_length)
    if trimmed.strip().lower() == title_norm:
        return ""
    return trimmed
