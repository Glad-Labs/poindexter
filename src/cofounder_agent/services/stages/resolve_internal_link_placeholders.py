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
        context.setdefault("stages", {})["resolve_internal_link_placeholders"] = True

        return StageResult(
            ok=True,
            detail=f"resolved {resolved}, stripped {stripped}",
            continue_workflow=True,
        )
