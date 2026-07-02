"""Shared resolver for ``pipeline_tasks.template_slug`` at task-create time.

Why
---

``content_router_service`` fails loud on a missing template_slug per
``feedback_no_silent_defaults`` — that's correct, because we deleted
the legacy chunked StageRunner path in the Lane C cutover (Stage 4,
2026-05-16) and there is no implicit pipeline to run. The bug was
that several call sites that INSERT into ``pipeline_tasks`` never
populated the column, producing the failed-task tail we saw in
the 2026-05-19 jank audit (finding #3).

This module is the single resolution policy:

  1. explicit caller-supplied ``template_slug`` — operator override
  2. ``niches.default_template_slug`` for the row's niche — per-niche
     control, the structured DB seam per ``feedback_filter_on_seams_not_slugs``
  3. ``app_settings.default_template_slug`` — process-wide fallback
  4. raise ``TemplateSlugUnresolvable`` — fail loud per
     ``feedback_no_silent_defaults``

The resolver is sync-pool aware (asyncpg pool) and idempotent for
the empty / blank-string case — a value that's just whitespace is
treated as missing and falls through to the next tier.
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


class TemplateSlugUnresolvable(ValueError):
    """Raised when no caller / niche / app_setting source produces a
    non-empty ``template_slug`` for a task being inserted into
    ``pipeline_tasks``.

    Per ``feedback_no_silent_defaults``: missing required config is
    a hard error, not a "use the legacy path" fallback.
    """


def _is_blank(value: Any) -> bool:
    """Return True if ``value`` is ``None``, empty string, or whitespace-only.

    The various INSERT paths historically wrote both NULL and ``''``
    into ``pipeline_tasks.template_slug``; treat them the same.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


async def _read_niche_default(pool: Any, niche_slug: str) -> str | None:
    """Read ``niches.default_template_slug`` for a niche slug.

    Returns the trimmed slug, or ``None`` if the niche doesn't exist,
    has no default set, or the lookup fails. Best-effort — DB hiccups
    must not block task creation entirely; the resolver will keep
    walking the chain.
    """
    if not niche_slug:
        return None
    try:
        async with pool.acquire() as conn:
            raw = await conn.fetchval(
                """
                SELECT default_template_slug FROM niches
                 WHERE slug = $1 LIMIT 1
                """,
                niche_slug,
            )
    except Exception as exc:
        logger.warning(
            "[template_slug_resolver] niche lookup failed for slug=%r: %s",
            niche_slug, exc,
        )
        return None
    if _is_blank(raw):
        return None
    return str(raw).strip()


async def _read_app_setting_default(pool: Any) -> str | None:
    """Read ``app_settings.default_template_slug``.

    Mirrors the helper in ``services/tasks_db.py`` but is reproduced
    here so call sites that don't go through ``tasks_db.add_task`` —
    the topic-batch + topic-proposal + legacy topic-discovery paths
    — share the same resolution policy. Returns the trimmed slug or
    ``None``.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings "
                "WHERE key = 'default_template_slug' AND is_active = true "
                "LIMIT 1"
            )
    except Exception as exc:
        logger.warning(
            "[template_slug_resolver] app_settings lookup failed: %s", exc,
        )
        return None
    if row is None:
        return None
    raw = row["value"]
    if _is_blank(raw):
        return None
    return str(raw).strip()


async def resolve_template_slug(
    pool: Any,
    *,
    explicit: str | None = None,
    niche_slug: str | None = None,
) -> str:
    """Resolve the ``template_slug`` for a new ``pipeline_tasks`` row.

    Args:
        pool: asyncpg pool (or compatible) for the niche + app_settings
            lookups.
        explicit: Caller-supplied template_slug. Wins the chain when
            non-blank.
        niche_slug: ``pipeline_tasks.niche_slug`` value being written.
            ``None`` / empty is acceptable for niche-less paths;
            resolution will skip directly to the app_settings tier.

    Returns:
        The resolved, non-empty template_slug string.

    Raises:
        TemplateSlugUnresolvable: when no source in the chain
            produces a non-empty value. Includes ``niche_slug`` in
            the message so the operator can fix the offending niche
            row or the app_settings default.
    """
    if not _is_blank(explicit):
        return str(explicit).strip()

    if niche_slug:
        niche_default = await _read_niche_default(pool, niche_slug)
        if niche_default:
            return niche_default

    setting_default = await _read_app_setting_default(pool)
    if setting_default:
        return setting_default

    raise TemplateSlugUnresolvable(
        f"No template_slug resolvable for niche={niche_slug!r}. "
        f"Set niches.default_template_slug for that niche, or set "
        f"app_settings.default_template_slug for a process-wide "
        f"fallback. (No silent default per feedback_no_silent_defaults.)"
    )
