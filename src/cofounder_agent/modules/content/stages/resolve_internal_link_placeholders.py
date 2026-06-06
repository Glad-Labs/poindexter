"""ResolveInternalLinkPlaceholdersStage — convert ``[posts/<slug>]``
placeholders to real markdown links (or strip them) before the
programmatic_validator runs.

## Why this stage exists

Captured 2026-05-15 (~14% canonical_blog pass rate investigation): the
writer LLM emits placeholders like ``[posts/some-slug]`` when it wants
to hint at an internal cross-link, but NO code anywhere in the pipeline
ever resolved them into real markdown links. They survived all the way
to ``content_validator`` which, since migration
``20260512_213806_seed_unresolved_placeholder_validator_rule.py``,
detects the pattern and emits a ``critical`` severity issue. The
critical issue vetoes the multi_model_qa gate -> auto_curator marks
``rejected_final`` -> the task is lost.

Twelve days × multiple writes per day × 100% leak rate = the headline
~95% canonical_blog rejection rate (see ``pipeline_gate_history`` rows
with ``feedback LIKE '%Unresolved internal-link placeholder%'``).

This stage closes the gap.

## What it does

For each ``[posts/<slug>]`` (or ``[posts/<uuid>]``) match in the
content:

1. Look up the slug (or id) in the ``posts`` table. If found and the
   post is published, replace the placeholder with a proper markdown
   link ``[<title>](/posts/<slug>)``.
2. If not found (LLM hallucinated a slug for a post that doesn't
   exist), strip the placeholder entirely. Better to ship a sentence
   without an internal link than to ship a broken bracket.

Idempotent: if the same content is run through twice the second pass is
a no-op because all placeholders are already resolved.

## Context reads

- ``content`` (str) — the draft text
- ``database_service`` (with ``acquire_connection`` / pool access)
- ``pool`` (optional) — used directly if database_service unavailable

## Context writes

- ``content`` (rewritten with resolved / stripped placeholders)
- ``internal_link_placeholders_resolved`` (int) — count of links rewritten
- ``internal_link_placeholders_stripped`` (int) — count of unknown
  slugs that got stripped instead of linked
- ``stages["resolve_internal_link_placeholders"]`` (bool) — true on
  successful execution

## Placement

Runs in the canonical_blog template AFTER ``generate_content`` and
``writer_self_review`` (so any review-pass rewrites also get resolved)
but BEFORE ``cross_model_qa`` and ``finalize_task`` (so validators see
clean text).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# The validator's PLACEHOLDER_MARKER_PATTERNS catches more shapes than
# this stage attempts to resolve. We focus on the productive shape —
# ``[posts/<identifier>]`` not followed by ``(``. The validator also
# flags ``[posts/<uuid>]`` and ``[posts/{slug}]``; those resolve through
# the same path (the captured group is the identifier).
#
# Regex grammar:
#   - ``\[posts/`` literal prefix
#   - ``([a-zA-Z0-9_-]+)`` — slug or hyphenated id (no slashes, no spaces)
#   - ``\]`` close bracket
#   - negative lookahead ``(?!\()`` — must NOT be followed by ``(``, to
#     avoid touching real markdown links like ``[posts/foo](/posts/foo)``.
_PLACEHOLDER_RE = re.compile(r"\[posts/([a-zA-Z0-9_-]+)\](?!\()")


def scrub_unresolved_placeholders(content: str) -> tuple[str, int]:
    """Strip ``[posts/<identifier>]`` placeholders without a DB lookup.

    Returns ``(new_content, stripped_count)``. Idempotent.

    Designed as the **safety net** for callers that can't afford the
    DB roundtrip (or already missed the resolver stage). The primary
    resolution path is still ``ResolveInternalLinkPlaceholdersStage``,
    which preserves legitimate internal links by looking up the
    identifier in ``posts`` first. Use this helper where preserving a
    cross-link matters less than avoiding a downstream
    ``unresolved_placeholder`` critical — most notably after a
    QA-rewriter LLM call, where re-introduced placeholders would
    otherwise loop the rewrite cycle until ``qa_max_rewrites`` burns
    out.

    Why the regex matches the resolver stage above: any change to one
    must change the other in lockstep, otherwise the safety net and
    the primary path drift and the validator's
    ``unresolved_placeholder`` rule starts firing on shapes only one
    side knows about.
    """
    new_content, n = _PLACEHOLDER_RE.subn("", content)
    return new_content, n


@dataclass
class _Resolution:
    """The decision for one placeholder match: either link or strip."""
    matched: str            # the raw ``[posts/<identifier>]`` text
    replacement: str        # either ``[Title](/posts/<slug>)`` or ``""``
    was_resolved: bool      # True if linked, False if stripped


async def _lookup_posts(
    pool: Any, identifiers: list[str],
) -> dict[str, dict[str, Any]]:
    """Fetch published posts matching any of ``identifiers``.

    Returns a dict mapping BOTH the slug AND the id (text form) to the
    row, so callers can look up either shape and get a hit.

    Uses ``status='published'`` because shipping a link to a draft / rejected
    post would be embarrassing — better to strip the bracket than to surface
    a 404 or a half-finished post.
    """
    if not identifiers:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text AS id_text, slug, title
            FROM posts
            WHERE status = 'published'
              AND (slug = ANY($1::text[]) OR id::text = ANY($1::text[]))
            """,
            identifiers,
        )
    by_identifier: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = dict(row)
        by_identifier[d["slug"]] = d
        by_identifier[d["id_text"]] = d
    return by_identifier


def _resolve_one(
    matched_text: str,
    identifier: str,
    posts_by_id: dict[str, dict[str, Any]],
) -> _Resolution:
    """Decide what to do with one placeholder. Pure function — testable
    without a DB."""
    post = posts_by_id.get(identifier)
    if post is None:
        # Unknown identifier — strip the bracket entirely. The surrounding
        # prose still reads, and the validator won't fire.
        return _Resolution(matched=matched_text, replacement="", was_resolved=False)
    title = (post.get("title") or "").strip() or post["slug"]
    slug = post["slug"]
    replacement = f"[{title}](/posts/{slug})"
    return _Resolution(
        matched=matched_text, replacement=replacement, was_resolved=True,
    )


async def _resolve_all_placeholders(
    content: str, pool: Any,
) -> tuple[str, int, int]:
    """Find every placeholder in ``content`` and replace it. Returns
    the rewritten content and counts of resolved / stripped placeholders."""
    matches = list(_PLACEHOLDER_RE.finditer(content))
    if not matches:
        return content, 0, 0

    identifiers = [m.group(1) for m in matches]
    posts_by_id = await _lookup_posts(pool, list(set(identifiers)))

    # Apply resolutions in reverse order so earlier offsets stay valid.
    new_content = content
    resolved_count = 0
    stripped_count = 0
    for m in reversed(matches):
        decision = _resolve_one(m.group(0), m.group(1), posts_by_id)
        if decision.was_resolved:
            resolved_count += 1
        else:
            stripped_count += 1
        new_content = (
            new_content[:m.start()] + decision.replacement + new_content[m.end():]
        )
    return new_content, resolved_count, stripped_count


# Empty-bracket scrubber — strips stray ``[]`` that the writer LLM leaves
# at the end of sentences when it wanted to drop a citation but didn't
# have one. Matt flagged these 2026-05-19 in stress-test post c4e62c7c
# (3 occurrences, all at sentence ends like "...user devices []."). The
# writer-prompt update (in the same PR) tells the model not to emit
# these in the first place; this scrubber is the deterministic safety
# net for drafts that slip through.
#
# Anchors:
#   - Single literal ``[]`` (no characters between the brackets).
#   - Allow optional leading whitespace and an optional trailing
#     sentence-ending punctuation so the whitespace before/after the
#     bracket also gets cleaned up. "...devices []." → "...devices."
#   - Skip empty brackets that look like markdown link images or
#     legitimate ``![](image.png)`` — those are caught by a different
#     stage (replace_inline_images).
#   - Skip empty brackets inside fenced code blocks (``` ... ```) and
#     inline-code spans (`` ` ``); Python lists like ``arr = []`` must
#     survive.
#
# Idempotent — a re-run on cleaned content matches nothing.
_FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# Match ``[]`` with optional leading space and optional trailing
# punctuation. Capture the trailing punctuation so we preserve it.
_EMPTY_BRACKET_RE = re.compile(r"[ \t]*\[\]([.,;:!?])?")


def _strip_empty_brackets(content: str) -> tuple[str, int]:
    """Strip empty ``[]`` markers from prose; preserve them inside code.

    Returns ``(new_content, strip_count)``.
    """
    # Carve out code-protected zones (fenced blocks first, then inline
    # code) by replacing them with sentinel placeholders we restore
    # afterwards. This is cheaper than building a code-aware lexer and
    # is good enough for the typical mid-prose ``[]`` shape.
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00PROTECTED{len(protected) - 1}\x00"

    masked = _FENCED_BLOCK_RE.sub(_protect, content)
    masked = _INLINE_CODE_RE.sub(_protect, masked)

    # Strip empty brackets from the (now code-free) prose. The trailing
    # punctuation, if captured, comes back attached to the preceding
    # word — so "devices []." becomes "devices.".
    count = 0

    def _strip(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        trailing = match.group(1) or ""
        return trailing

    masked = _EMPTY_BRACKET_RE.sub(_strip, masked)

    # Restore protected zones.
    def _restore(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return protected[idx]

    return re.sub(r"\x00PROTECTED(\d+)\x00", _restore, masked), count


class ResolveInternalLinkPlaceholdersStage:
    """Resolve / strip ``[posts/<slug>]`` placeholders before validation.

    Closes the headline ~95% canonical_blog rejection cause discovered
    on 2026-05-15.
    """

    name = "resolve_internal_link_placeholders"
    description = "Convert [posts/<slug>] placeholders to real markdown links or strip them"
    timeout_seconds = 30
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],  # noqa: ARG002 — stage signature contract
    ) -> Any:
        # Soft import the StageResult dataclass — keeps the module load
        # cycle-free if a future refactor moves it. Mirrors what the
        # other stages do (see e.g. ``replace_inline_images.py``).
        from plugins.stage import StageResult

        content = context.get("content") or ""
        if not content:
            return StageResult(
                ok=True,
                detail="no content to scan",
                continue_workflow=True,
            )

        pool = context.get("pool")
        if pool is None:
            database_service = context.get("database_service")
            pool = getattr(database_service, "pool", None) if database_service else None
        if pool is None:
            # Without a pool we can't look anything up — DON'T strip
            # placeholders blindly, that would silently lose every
            # legitimate internal link. Better to leave the content alone
            # and let the validator do its job; the operator will see the
            # rejection and investigate the missing pool wiring.
            logger.warning(
                "[resolve_internal_link_placeholders] no DB pool in context — "
                "skipping (validator will catch leaked placeholders if present)",
            )
            return StageResult(
                ok=True,
                detail="skipped — no pool available",
                continue_workflow=True,
            )

        try:
            new_content, resolved, stripped = await _resolve_all_placeholders(
                content, pool,
            )
        except Exception as exc:
            # Never halt the workflow — if the resolver fails the validator
            # will surface the placeholder leak, which is the next best
            # signal. Log loud so the operator can investigate.
            logger.exception(
                "[resolve_internal_link_placeholders] resolver crashed: %s", exc,
            )
            return StageResult(
                ok=False,
                detail=f"resolver crashed: {exc}",
                continue_workflow=True,
            )

        if resolved or stripped:
            context["content"] = new_content
            logger.info(
                "[resolve_internal_link_placeholders] resolved=%d stripped=%d",
                resolved, stripped,
            )
        context["internal_link_placeholders_resolved"] = resolved
        context["internal_link_placeholders_stripped"] = stripped

        # Second pass: scrub stray empty ``[]`` markers from prose.
        # See ``_strip_empty_brackets`` for rationale; the writer-prompt
        # update bans these but legacy / stress-test posts leak them.
        current_content = context.get("content") or new_content
        cleaned_content, empty_bracket_count = _strip_empty_brackets(current_content)
        if empty_bracket_count:
            context["content"] = cleaned_content
            logger.info(
                "[resolve_internal_link_placeholders] stripped %d empty bracket(s) from prose",
                empty_bracket_count,
            )
        context["empty_brackets_stripped"] = empty_bracket_count
        context.setdefault("stages", {})["resolve_internal_link_placeholders"] = True

        # Return the mutations via context_updates so they propagate on the
        # graph_def atom path. ``make_stage_node`` runs this stage on a COPY of
        # the LangGraph state and merges back ONLY ``StageResult.context_updates``
        # — a stage that merely mutates its local ``context`` has its changes
        # discarded. Without this the strip ran in a thrown-away copy and the
        # ``[posts/<id>]`` placeholders still reached qa.programmatic, re-opening
        # the ~95% canonical_blog rejection this stage exists to prevent (the
        # stage logged ``stripped=N`` but the cleaned content never propagated
        # after the #355 cutover). Every other content stage already returns
        # context_updates; this was the lone in-place outlier.
        return StageResult(
            ok=True,
            detail=f"resolved {resolved}, stripped {stripped}",
            continue_workflow=True,
            context_updates={
                "content": context.get("content", ""),
                "internal_link_placeholders_resolved": resolved,
                "internal_link_placeholders_stripped": stripped,
                "empty_brackets_stripped": empty_bracket_count,
            },
        )
