"""Internal-link recommendation coherence gate.

Two-part filter that sits between the candidate-collection step of the
internal-link recommender (both the ILIKE-based path in ``ResearchService``
and the pgvector-based path in ``content_router_service._build_rag_context``)
and the point where candidates are rendered into the writer prompt.

Problem (GH-88): an embedding neighbourhood that happens to include the
CadQuery post for every "engineering fundamentals" topic caused the
recommender to pin CadQuery as a "related" suggestion on asyncio and
AI-engineering posts. The writer dutifully turned the suggestion into a
"Consider exploring CadQuery" call-to-action. Tag-coherence was never
checked, and the same target could be re-suggested on an unbounded number
of posts.

This module adds the two guards that were missing:

  1. **Tag-coherence gate.** The source and target must share at least one
     topic tag. Source tags come from the content task (slugs, names, or
     categorical label). Target tags come from the ``post_tags`` junction
     table.

  2. **Single-target cap.** No single post may be the "related"
     recommendation on more than N other posts (N configurable via
     ``app_settings.internal_link_single_target_cap``; default 3). Cap is
     enforced by scanning existing published content for internal links
     pointing at the candidate slug.

Both guards are best-effort: if the DB is unreachable, we log and return
the full candidate list unfiltered, because a half-broken recommender is
still better than no recommender.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings keys (read via services.site_config)
# ---------------------------------------------------------------------------

# Tunables. All reads funnel through ``_get_setting`` so we read from
# app_settings via site_config rather than env vars.
_SETTING_TAG_COHERENCE_REQUIRED = "internal_link_tag_coherence_required"
_SETTING_SINGLE_TARGET_CAP = "internal_link_single_target_cap"
_SETTING_CAP_ENABLED = "internal_link_single_target_cap_enabled"

# Defaults chosen so the gate is strict out of the box. Matt can loosen by
# setting the app_settings keys.
_DEFAULT_TAG_COHERENCE_REQUIRED = True
_DEFAULT_SINGLE_TARGET_CAP = 3
_DEFAULT_CAP_ENABLED = True


def _get_bool_setting(key: str, default: bool) -> bool:
    """Read a bool-ish setting from site_config, never raising."""
    try:
        from services.site_config import site_config

        raw = site_config.get(key, "")
        if raw == "" or raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return default


def _get_int_setting(key: str, default: int) -> int:
    """Read an int-ish setting from site_config, never raising."""
    try:
        from services.site_config import site_config

        raw = site_config.get(key, "")
        if raw == "" or raw is None:
            return default
        return int(str(raw).strip())
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Candidate shape
# ---------------------------------------------------------------------------


@dataclass
class LinkCandidate:
    """One candidate internal-link recommendation.

    ``post_id`` is optional — the ILIKE path in ``ResearchService`` only has
    title+slug, so tag lookup falls back to slug when needed.
    """

    slug: str
    title: str
    post_id: str | None = None
    similarity: float | None = None
    tag_slugs: set[str] | None = None
    inbound_count: int | None = None
    rejection_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def get_tag_slugs_for_post(
    pool, *, post_id: str | None = None, slug: str | None = None
) -> set[str]:
    """Return the set of tag slugs attached to a post.

    Accepts either ``post_id`` (UUID string) or ``slug``. Returns an empty
    set when the DB pool is None, the post has no tags, or the query
    raises.
    """
    if pool is None:
        return set()
    if not post_id and not slug:
        return set()

    try:
        if post_id:
            rows = await pool.fetch(
                """
                SELECT t.slug
                FROM post_tags pt
                JOIN tags t ON t.id = pt.tag_id
                WHERE pt.post_id::text = $1
                """,
                str(post_id).removeprefix("post/"),
            )
        else:
            rows = await pool.fetch(
                """
                SELECT t.slug
                FROM posts p
                JOIN post_tags pt ON pt.post_id = p.id
                JOIN tags t ON t.id = pt.tag_id
                WHERE p.slug = $1
                """,
                slug,
            )
        return {r["slug"] for r in rows if r.get("slug")}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[LINK_COHERENCE] tag lookup failed: %s", exc)
        return set()


async def count_inbound_links_to_slug(pool, slug: str) -> int:
    """Count how many published posts contain an internal link to ``slug``.

    Used by the single-target cap: if a slug already has N inbound links we
    stop recommending it. Matches both the bare path form ``/posts/<slug>``
    and the fully-qualified ``https://<site>/posts/<slug>`` form (the writer
    emits both depending on which hint list it follows).
    """
    if pool is None or not slug:
        return 0

    try:
        # Two literal-substring checks. We match the slug boundary via a
        # trailing character ($, /, ", ), ', ], whitespace). Rather than
        # regex in SQL (which is expensive on large posts), we rely on
        # ILIKE for the common /posts/<slug> shape and post-filter in
        # Python. This over-counts if `slug` is a prefix of another slug,
        # but the canonical slug format (kebab + -481 suffix) makes that
        # extremely unlikely; if it happens the cap just triggers slightly
        # sooner which is the safe direction.
        rows = await pool.fetch(
            """
            SELECT id, content
            FROM posts
            WHERE status = 'published'
              AND content ILIKE $1
            """,
            f"%/posts/{slug}%",
        )
        # Confirm the slug isn't a partial match (e.g. 'foo' inside 'foo-bar').
        pattern = re.compile(
            r"/posts/" + re.escape(slug) + r"(?:[/?#\"')\]\s]|$)", re.IGNORECASE
        )
        count = sum(1 for r in rows if pattern.search(r.get("content") or ""))
        return count
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[LINK_COHERENCE] inbound count failed for %s: %s", slug, exc)
        return 0


# ---------------------------------------------------------------------------
# The coherence filter
# ---------------------------------------------------------------------------


def _slugify_token(token: str) -> str:
    token = token.strip().lower()
    token = re.sub(r"[^a-z0-9]+", "-", token)
    return token.strip("-")


def normalize_tag_set(tags: Iterable[str] | None) -> set[str]:
    """Lower-case + slugify a heterogeneous list of tag names/slugs.

    Source "tags" come from a few places: the content_task ``tags`` JSONB
    (names like "Python" or slugs like "python-tips"), the ``category``
    column ("technology", "3d-modeling"), or a free-form list from the
    caller. We fold them all to the ``tags.slug`` format so set-overlap
    works.
    """
    if not tags:
        return set()
    out: set[str] = set()
    for t in tags:
        if not t:
            continue
        if isinstance(t, dict):
            t = t.get("slug") or t.get("name") or ""
        s = _slugify_token(str(t))
        if s:
            out.add(s)
    return out


class InternalLinkCoherenceFilter:
    """Gatekeeper sitting between candidate collection and prompt injection.

    Apply-order per candidate:

      1. Tag-coherence: if enabled, the candidate's tag slugs must share at
         least one member with the source's tag slugs. If the candidate
         has no tags in the DB (e.g. legacy posts created before the
         ``post_tags`` migration), we reject when the gate is strict —
         untagged targets can't be proven topically relevant.

      2. Single-target cap: if enabled, the candidate's inbound-link count
         must be < cap. Counts are fetched lazily so we don't query until
         a candidate has cleared the tag check.

    Rejection reasons are recorded on each ``LinkCandidate`` so the audit
    pass and debug logs can explain why a candidate was filtered out.
    """

    def __init__(
        self,
        *,
        pool=None,
        tag_coherence_required: bool | None = None,
        single_target_cap: int | None = None,
        cap_enabled: bool | None = None,
    ) -> None:
        self.pool = pool
        self.tag_coherence_required = (
            _get_bool_setting(
                _SETTING_TAG_COHERENCE_REQUIRED, _DEFAULT_TAG_COHERENCE_REQUIRED
            )
            if tag_coherence_required is None
            else tag_coherence_required
        )
        self.single_target_cap = (
            _get_int_setting(_SETTING_SINGLE_TARGET_CAP, _DEFAULT_SINGLE_TARGET_CAP)
            if single_target_cap is None
            else single_target_cap
        )
        self.cap_enabled = (
            _get_bool_setting(_SETTING_CAP_ENABLED, _DEFAULT_CAP_ENABLED)
            if cap_enabled is None
            else cap_enabled
        )

    # --- primitives ------------------------------------------------------

    @staticmethod
    def tags_overlap(source: Iterable[str], target: Iterable[str]) -> bool:
        """Return True iff source/target share at least one normalized tag."""
        s = normalize_tag_set(source)
        t = normalize_tag_set(target)
        if not s or not t:
            return False
        return not s.isdisjoint(t)

    def under_cap(self, inbound_count: int) -> bool:
        """Return True iff ``inbound_count`` is strictly below the cap."""
        if not self.cap_enabled:
            return True
        return inbound_count < self.single_target_cap

    # --- main entry point -----------------------------------------------

    async def filter_candidates(
        self,
        *,
        source_tags: Iterable[str],
        candidates: list[LinkCandidate],
    ) -> list[LinkCandidate]:
        """Filter ``candidates`` in place-style, returning the survivors.

        Rejected candidates are NOT returned; their ``rejection_reason`` is
        set on the original object so callers that retain the full list can
        still introspect.
        """
        survivors: list[LinkCandidate] = []
        normalized_source = normalize_tag_set(source_tags)

        for cand in candidates:
            # Hydrate tag slugs if the caller didn't provide them.
            if cand.tag_slugs is None:
                cand.tag_slugs = await get_tag_slugs_for_post(
                    self.pool, post_id=cand.post_id, slug=cand.slug
                )

            # 1. Tag coherence.
            if self.tag_coherence_required:
                if not normalized_source:
                    # Without source tags we can't prove coherence. Rather
                    # than silently passing everything, reject — matches
                    # the project-wide "no silent defaults" stance.
                    cand.rejection_reason = "source_has_no_tags"
                    logger.debug(
                        "[LINK_COHERENCE] reject %s: source has no tags", cand.slug
                    )
                    continue

                if not cand.tag_slugs:
                    cand.rejection_reason = "target_has_no_tags"
                    logger.debug(
                        "[LINK_COHERENCE] reject %s: target has no tags", cand.slug
                    )
                    continue

                if normalized_source.isdisjoint(cand.tag_slugs):
                    cand.rejection_reason = "no_tag_overlap"
                    logger.debug(
                        "[LINK_COHERENCE] reject %s: source=%s target=%s",
                        cand.slug,
                        sorted(normalized_source),
                        sorted(cand.tag_slugs),
                    )
                    continue

            # 2. Single-target cap.
            if self.cap_enabled:
                if cand.inbound_count is None:
                    cand.inbound_count = await count_inbound_links_to_slug(
                        self.pool, cand.slug
                    )
                if not self.under_cap(cand.inbound_count):
                    cand.rejection_reason = "single_target_cap"
                    logger.info(
                        "[LINK_COHERENCE] cap reached for %s: %d inbound (cap=%d)",
                        cand.slug,
                        cand.inbound_count,
                        self.single_target_cap,
                    )
                    continue

            survivors.append(cand)

        if len(survivors) < len(candidates):
            logger.info(
                "[LINK_COHERENCE] filtered %d -> %d candidates (source_tags=%s)",
                len(candidates),
                len(survivors),
                sorted(normalized_source) or ["<none>"],
            )

        return survivors
