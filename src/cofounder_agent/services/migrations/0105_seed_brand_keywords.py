"""Migration 0105: Seed an empty default for ``brand_keywords``.

Pairs with ``services/topic_discovery.py`` (gh#216). The dispatcher now
reads ``app_settings.brand_keywords`` (comma-separated string) before
falling back to the hardcoded ``_BRAND_KEYWORDS`` set baked to Glad
Labs's own AI / PC-hardware / gaming niche.

Default value is an **empty string** — NOT a copy of the Glad Labs
niche keywords. Per the issue's "default seed should be empty/permissive
for new installs": a fresh Poindexter install should not ship with
another company's niche bias baked in. An empty value triggers the
fallback path (hardcoded set) for existing Glad Labs deployments that
haven't customized this row, which keeps current behaviour unchanged
while making the override the documented path going forward.

Seeding the row (even empty) makes the setting discoverable in the
``app_settings`` table so operators can see it exists and tune it via
``poindexter set brand_keywords=...`` or the MCP ``set_setting`` tool
without having to first know it's a valid key.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so a customer who has
already seeded their own niche keywords (gardening / dentistry / etc.)
keeps that value through migration re-runs.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "brand_keywords",
        "",
        "Comma-separated brand-relevance keywords used by topic_discovery "
        "to filter discovered topics to the site's niche. Empty value "
        "falls back to the hardcoded Glad Labs niche set (AI / PC "
        "hardware / gaming) for backwards compatibility. Customers "
        "running Poindexter for a different niche (e.g. gardening, "
        "dentistry) should set this to their own comma-separated list "
        "of niche keywords — single-word entries are matched at word "
        "boundaries, multi-word / hyphenated entries match as substrings. "
        "Case-insensitive. Example: 'gardening,compost,heirloom tomato'.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "0105: seeded %d/%d brand_keywords settings "
            "(remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info("0105: removed %d brand_keywords seeds", len(_SEEDS))
