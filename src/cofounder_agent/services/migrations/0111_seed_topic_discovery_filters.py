"""Migration 0111: Seed empty defaults for topic_discovery filter constants.

Pairs with ``services/topic_discovery.py`` (gh#218). The dispatcher used
to bake two niche-coloured filter constants directly into Python:

- ``_NEWS_PATTERNS`` — regex list that flags titles as "news/junk" and
  rejects them. Encodes Glad Labs's evergreen-tech-editorial voice. A
  real-estate niche customer would *want* the lawsuit/court patterns to
  pass through; a fashion niche would *want* merch/sticker/sale matches.
- ``CATEGORY_SEARCHES`` — category -> keyword-query map used by
  ``_classify_category``. Defaults every miss to ``"technology"``, so a
  non-tech niche gets every topic mis-classified.

After this migration both move to ``app_settings`` with **empty defaults**
(``[]`` and ``{}``) — Poindexter ships niche-agnostic out of the box.
The hardcoded constants stay in code as a permissive fallback for the
existing Glad Labs deployment, so behaviour doesn't change unless an
operator explicitly opts in.

Storage format:

- ``topic_discovery_news_patterns`` — JSON array of regex strings
  (case-insensitive at runtime). Example::

      ["\\b(?:lawsuit|sued|court)\\b", "\\b(?:shirt|merch|sticker)\\b"]

- ``topic_discovery_category_searches`` — JSON object mapping category
  name -> list of keyword search strings. Example::

      {
          "technology": ["latest AI tools 2026", "new frameworks"],
          "gardening":  ["heirloom tomato varieties", "compost tea"]
      }

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — a customer who has
already seeded their own niche values keeps them through migration
re-runs.

Related: #216 (brand_keywords), #217 (industry-specific seed scripts).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "topic_discovery_news_patterns",
        "[]",
        "JSON array of regex strings (case-insensitive). When non-empty, "
        "TopicDiscovery uses these patterns to reject titles as "
        "news/junk/merch/etc. instead of the hardcoded Glad Labs set "
        "(lawsuit/court, election/senator, shirt/merch, etc.). Empty "
        "array falls back to the hardcoded set for backwards "
        "compatibility. Customers running Poindexter for a niche where "
        "those topics are on-brand (real estate, fashion, politics) "
        "should set this to their own array — including '[]' to disable "
        "regex-based junk filtering entirely. Only the regex-based "
        "filter is overridden; the too-short-title guard still applies "
        "unconditionally.",
    ),
    (
        "topic_discovery_category_searches",
        "{}",
        "JSON object mapping category name -> list of keyword search "
        "strings. Used by TopicDiscovery._classify_category to bucket a "
        "scraped title into a category, and by web-search topic sources "
        "to issue DuckDuckGo queries per category. Empty object falls "
        "back to the hardcoded Glad Labs categories (technology / "
        "startup / security / engineering / insights / business / "
        "hardware / gaming) for backwards compatibility. Customers in a "
        "different niche should set this to their own category map so "
        "classification doesn't default everything to 'technology'.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, 'content', $3, FALSE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "0111: seeded %d/%d topic_discovery filter settings "
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
        logger.info("0111: removed %d topic_discovery filter seeds", len(_SEEDS))
