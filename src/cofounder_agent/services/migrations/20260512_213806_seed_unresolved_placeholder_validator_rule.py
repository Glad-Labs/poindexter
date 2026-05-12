"""Seed content_validator_rules row for the unresolved-placeholder rule.

ISSUE: Glad-Labs/poindexter#489

Why: Matt found bare ``[posts/<uuid>]`` template tokens leaking into a
published post body (score 90). None of the existing programmatic rules
caught it — image_placeholder only matches ``[IMAGE: ...]`` shapes, and
hallucinated_link only matches prose-style "our guide on…" phrases.

The new ``unresolved_placeholder`` rule in
``services/content_validator.py`` catches every variant of
``[posts/<uuid>]`` / ``[posts/{slug}]`` / ``[posts/abc-123]`` /
``[POST_ID: x]``, and is wired as a *critical* check. Without a DB row
it still runs (validator_config fails open) — but seeding the row makes
the rule visible to the operator CRUD surface so it can be tuned /
niche-scoped / temporarily disabled the same way every other validator
can.

Idempotent: ON CONFLICT DO NOTHING; an operator who's already
customized severity / niches / threshold won't see their tweaks wiped.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_RULE_DESCRIPTION = (
    "Detects unresolved internal-link placeholders left in post bodies — "
    "[posts/<uuid>], [posts/{slug}], bare [posts/abc-123] without a "
    "(/posts/abc-123) URL, and [POST_ID: ...] variants. These leak when "
    "the writer hints at the link shape but internal_link_coherence's "
    "resolution step never converts them to real Markdown links. "
    "Critical — readers see broken brackets in the rendered post."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.content_validator_rules
                (name, enabled, severity, threshold, applies_to_niches,
                 description)
            VALUES
                ($1, true, 'error', '{}'::jsonb, NULL, $2)
            ON CONFLICT (name) DO NOTHING
            """,
            "unresolved_placeholder",
            _RULE_DESCRIPTION,
        )
    logger.info(
        "Migration 20260512_213806: unresolved_placeholder validator rule "
        "seeded (or already present).",
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM public.content_validator_rules
             WHERE name = 'unresolved_placeholder'
            """
        )
    logger.info(
        "Migration 20260512_213806 down: unresolved_placeholder rule "
        "removed from content_validator_rules.",
    )
