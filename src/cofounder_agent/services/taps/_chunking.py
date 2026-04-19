"""Shared chunking + dedup helpers for Taps.

Extracted from ``scripts/auto-embed.py`` so every Tap — native and
Singer-wrapped — uses the same chunking rules, content_hash format,
and file-type taxonomy. No behavior change from the pre-refactor
pipeline; this is a pure move.

## Chunking

``chunk_text`` splits oversize text on markdown heading boundaries,
falling back to paragraph boundaries, then hard slice as last resort.
The target is Ollama's ~8k token context for ``nomic-embed-text``;
the default ``max_chars=6000`` stays under that.

Files under the limit yield a single chunk unchanged — the pipeline
treats single-chunk and multi-chunk cases identically.

## Dedup

``content_hash`` is SHA-256 of the full file contents. Runners compare
the hash of incoming Documents against the existing row in
``embeddings`` (keyed by ``(source_table, source_id, chunk_index=0,
embedding_model)``) and skip re-embedding when they match.

## Classification

``classify_file`` maps a filename to a coarse type tag used in
Document metadata. Downstream queries can filter by this type
(``handoff``, ``feedback``, ``decision``, ``identity``, etc.).
"""

from __future__ import annotations

import hashlib
import re
from typing import List


# nomic-embed-text has an 8192 token context window; ~6k chars stays
# comfortably under. Overridable per-call for tests or special cases.
MAX_CHARS = 6000


def content_hash(text: str) -> str:
    """SHA-256 hash of content for dedup. Hex-encoded."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_file(filename: str) -> str:
    """Infer a coarse type tag from a memory-file name.

    Returns one of: ``handoff``, ``feedback``, ``decision``, ``identity``,
    ``project``, ``audit``, ``state``, ``issues``, ``index``,
    ``knowledge`` (default).
    """
    name = filename.lower().replace(".md", "")
    if "handoff" in name or "session" in name:
        return "handoff"
    if "feedback" in name or "preference" in name:
        return "feedback"
    if "decision" in name:
        return "decision"
    if "identity" in name or "profile" in name or "career" in name or "voice" in name:
        return "identity"
    if "project" in name or "strategy" in name or "vision" in name:
        return "project"
    if "audit" in name or "report" in name:
        return "audit"
    if "state" in name or "current" in name:
        return "state"
    if "issue" in name:
        return "issues"
    if "memory" in name or "shared" in name:
        return "index"
    return "knowledge"


def chunk_text(text: str, max_chars: int = MAX_CHARS) -> List[str]:
    """Split text into chunks at most ``max_chars`` each.

    Splitting preference (strongest → weakest):

    1. Markdown heading boundaries (``#``, ``##``, ``###``) — headings are
       the most semantically stable break points in our memory files.
    2. Paragraph boundaries (``\\n\\n``) within oversized sections.
    3. Hard character slice — last resort for a single paragraph larger
       than ``max_chars``. Bad for retrieval but beats dropping content.

    Files already under the limit yield a single chunk unchanged.
    """
    if len(text) <= max_chars:
        return [text]

    # Split on lines that start with #, ##, or ### (ATX headings). The
    # lookahead keeps the heading attached to its following section.
    sections = re.split(r"(?=^#{1,3} )", text, flags=re.MULTILINE)
    chunks: List[str] = []
    buf = ""
    for section in sections:
        if not section:
            continue
        if len(section) > max_chars:
            # Flush accumulator before diving into the oversized section.
            if buf:
                chunks.append(buf)
                buf = ""
            # Paragraph-split inside the section.
            paras = section.split("\n\n")
            pbuf = ""
            for p in paras:
                candidate = (pbuf + "\n\n" + p) if pbuf else p
                if len(candidate) <= max_chars:
                    pbuf = candidate
                else:
                    if pbuf:
                        chunks.append(pbuf)
                        pbuf = ""
                    if len(p) <= max_chars:
                        pbuf = p
                    else:
                        # Last-resort hard slice.
                        for start in range(0, len(p), max_chars):
                            chunks.append(p[start:start + max_chars])
            if pbuf:
                buf = pbuf
            continue
        candidate = (buf + section) if buf else section
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            buf = section
    if buf:
        chunks.append(buf)
    return chunks
