"""Leaked planning-scaffold stripping — shared atom library (#1963 + #2762).

Two producer paths can prefix a draft with non-article scaffolding:

- The WRITER (notably gemma-4-31B) intermittently emits its planning/outline
  scaffold — bulleted meta-notes plus echoed prompt instructions — before the
  article (#1963, prod task 0f70f736: "* Topic:", "* Key elements from
  sources:", "Vary sentence length.", "No placeholder brackets.## Heading").
- The QA-rescue REVISER (``qa.rewrite``) can echo its revision briefing the
  same way (prod task 342a26b7, 2026-07-23: the body opened with "*   Task:
  Revise a draft article based on specific fixes.", "*   Constraints:
  Preserve structure…", "*   Fix 1: …" ×4, "Return Markdown body only") —
  and because the rescue loop re-enters the graph at ``qa.programmatic``, the
  revised draft never passes back through ``content.normalize_draft``, so the
  strip must ALSO run at the rewrite boundary.
- The reviser briefing echo has a LABEL-FREE dialect (prod task ece2f516,
  2026-07-24, poindexter#897): a bare ``Revise a draft article about "…"``
  opener with no "Task:" label, deep-indented unlabeled ``*`` bullets
  restating the constraints ("Preserve structure, headings, length, links,
  citations, and voice.", "No new sections/removals unless required by
  fixes."), first-person deliberation ("*Wait*, the fix list also asks…",
  "as an AI with broad knowledge…"), and the briefing fused onto the
  article's first heading with no blank line ("…Verify Markdown
  structure.## The startup's…"). Only one tell (the opener) matched the
  342a26b7 vocabulary, so the >=2 bar kept the strip silent and the echo
  reached awaiting_approval (caught in the wild by qa.title_coherence).

This module is a ``_``-prefixed library (the atom registry skips it), so both
``content.normalize_draft`` and ``qa.rewrite`` may import it without violating
atom independence. The detection twin lives in
``content_validator.LEAKED_PLANNING_SCAFFOLD_RE`` (strip here, detect there,
per the reasoning-leak precedent).
"""

from __future__ import annotations

import re

# Tells = echoed-instruction phrases + bulleted planning/briefing labels that
# never appear in finished prose. Writer-scaffold tells (#1963) plus the
# qa.rewrite revision-briefing vocabulary (2026-07-23) plus its label-free
# dialect — constraint echoes and first-person deliberation markers
# (2026-07-24, task ece2f516 / poindexter#897). Each tell alone is allowed in
# real prose (a post about editing may say "revise the draft"); only >=2
# co-occurring fire the strip/gate. Mirrored by
# content_validator.LEAKED_PLANNING_SCAFFOLD_RE — keep the two in sync
# (test_scaffold_tell_regexes_stay_in_sync enforces it).
SCAFFOLD_TELL_RE = re.compile(
    r"(?im)(?:"
    r"key\s+elements?\s+from\s+sources"
    r"|models?\s+used\s*/?\s*tested"
    r"|vary\s+sentence\s+length"
    r"|no\s+placeholder\s+brackets"
    r"|avoid\s+[\"'“]?delve"
    r"|concluding\s+paragraph"
    r"|revis(?:e|ed|ing)\s+(?:a|the|this)\s+draft(?:\s+article)?\b"
    r"|return\s+markdown\s+body\s+only"
    r"|preserve\s+(?:the\s+article['’]s\s+)?structure,?\s+headings"
    r"|(?:no|do\s+not\s+add)\s+new\s+sections?\b"
    r"|links,\s+citations,\s+and\s+voice"
    r"|verify\s+markdown\s+structure"
    r"|\*wait[,.!]?\*"
    r"|as\s+an\s+ai,?\s+with\b"
    r"|the\s+fix(?:es)?\s+list\b"
    r"|\*\s*(?:voice|citations?|structure)\s*:\s*\*"
    r"|^[ \t]*[*+\-][ \t]+\*?(?:topic|voice|citations?|structure|tone|audience"
    r"|outline|writer\s+model|reviser|vision\s+qa|key\s+elements?"
    r"|task|constraints|fix\s+\d+)\b[ \t]*:"
    r")"
)

# First Markdown heading (H1-H6), whether at line start OR glued onto preceding
# text — the writer sometimes emits "...brackets.## Heading" with no newline,
# so a plain ``^#`` anchor would miss it.
FIRST_HEADING_RE = re.compile(r"(?:^|(?<=[^\n#]))(#{1,6}[ \t]+\S)", re.MULTILINE)


def strip_leaked_planning_scaffold(content: str) -> str:
    """Remove a leaked planning/outline/briefing scaffold preceding the article.

    Only acts when the text before the first Markdown heading carries >= 2
    scaffold tells (echoed prompt instructions / planning or briefing labels) —
    a normal intro paragraph has none, so legitimate content is never touched.
    Returns the article from its first heading onward, re-anchoring a heading
    the model glued mid-line. Leaves content unchanged when no heading anchors
    the article (the content_validator ``leaked_planning_scaffold`` rule is the
    safety net for that residual case). Pure + idempotent.
    """
    if not content:
        return content
    heading = FIRST_HEADING_RE.search(content)
    if not heading:
        return content
    preamble = content[: heading.start(1)]
    if len(SCAFFOLD_TELL_RE.findall(preamble)) < 2:
        return content
    return content[heading.start(1) :].lstrip()
