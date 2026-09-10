"""Internal-link placeholder shapes shared by the resolver stage and QA rescue.

Single home for the ``[posts/<identifier>]`` grammar so the two consumers can
never drift:

- ``modules/content/stages/resolve_internal_link_placeholders.py`` — the
  primary path: resolves placeholders into real ``[Title](/posts/<slug>)``
  links via a ``posts`` lookup (writer block, canonical_blog).
- ``modules/content/atoms/qa_rewrite.py`` — the safety net: the reviser LLM
  can re-emit placeholders the resolver already cleaned, and the rescue cycle
  (qa_aggregate → qa_rewrite → qa_programmatic) never re-enters the resolver,
  so unscrubbed placeholders re-fail validation every pass until the attempt
  budget burns out (poindexter#1023; originally fixed 2026-05-25 in the
  deleted cross_model_qa stage and lost in the atom-cutover #355).

This sits in ``services/`` because an atom must not import a stage (the
atom-independence rule) — same reasoning as ``services/image_prompt_sanitizer.py``.
"""

from __future__ import annotations

import re

# The validator's PLACEHOLDER_MARKER_PATTERNS catches more shapes than the
# resolver attempts to resolve. This focuses on the productive shape —
# ``[posts/<identifier>]`` not followed by ``(``. The validator also flags
# ``[posts/<uuid>]`` and ``[posts/{slug}]``; those resolve through the same
# path (the captured group is the identifier).
#
# Regex grammar:
#   - ``\[posts/`` literal prefix
#   - ``([a-zA-Z0-9_-]+)`` — slug or hyphenated id (no slashes, no spaces)
#   - ``\]`` close bracket
#   - negative lookahead ``(?!\()`` — must NOT be followed by ``(``, to
#     avoid touching real markdown links like ``[posts/foo](/posts/foo)``.
PLACEHOLDER_RE = re.compile(r"\[posts/([a-zA-Z0-9_-]+)\](?!\()")


def scrub_unresolved_placeholders(content: str) -> tuple[str, int]:
    """Strip ``[posts/<identifier>]`` placeholders without a DB lookup.

    Returns ``(new_content, stripped_count)``. Idempotent.

    Designed as the **safety net** for callers that can't afford the DB
    roundtrip (or already missed the resolver stage). The primary resolution
    path is still ``ResolveInternalLinkPlaceholdersStage``, which preserves
    legitimate internal links by looking up the identifier in ``posts``
    first. Use this helper where preserving a cross-link matters less than
    avoiding a downstream ``unresolved_placeholder`` critical — most notably
    after a QA-rewriter LLM call, where re-introduced placeholders would
    otherwise loop the rewrite cycle until the attempt budget burns out.
    """
    new_content, n = PLACEHOLDER_RE.subn("", content)
    return new_content, n


__all__ = ["PLACEHOLDER_RE", "scrub_unresolved_placeholders"]
