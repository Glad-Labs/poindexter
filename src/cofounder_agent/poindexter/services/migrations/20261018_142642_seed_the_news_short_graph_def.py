"""Migration 20261018_142642: seed the news_short graph_def.

A YouTube Short made from one news story, with no blog post behind it
(``services/news_shorts.py``, ``services/news_short_spec.py``,
``docs/architecture/news-shorts.md``). Inserts the ``news_short`` row into
``pipeline_templates`` (``ON CONFLICT DO NOTHING``, so a re-run or an operator
edit is left alone), then writes the in-tree spec through
``graph_def_reseed.apply_reseeds`` so the row is declared at the graph
signature the reseed gate checks (``test_graph_def_reseed_gate.py``) and is
stamped here when the registry imports, else at the worker's next boot.

Nothing runs it until a task with ``template_slug='news_short'`` is queued,
and the job that queues them is off by default (``news_shorts_enabled``).
"""

from __future__ import annotations

import json
import logging

from poindexter.services.graph_def_reseed import apply_reseeds
from poindexter.services.news_short_spec import NEWS_SHORT_GRAPH_DEF

logger = logging.getLogger(__name__)

# (slug, new_version, spec module, spec attr, graph signature the reseed brings the row to)
_RESEEDS = (
    (
        "news_short",
        1,
        "poindexter.services.news_short_spec",
        "NEWS_SHORT_GRAPH_DEF",
        "7e3cc3782914",
    ),
)

_INSERT_SQL = """
    INSERT INTO pipeline_templates (slug, name, description, version, active, graph_def, created_by)
    VALUES ('news_short', 'News Short', $1, 1, true, $2::jsonb, 'migration')
    ON CONFLICT (slug) DO NOTHING
"""


async def up(pool) -> None:
    """Insert the news_short template row, then write it at its declared signature."""
    async with pool.acquire() as conn:
        tag = await conn.execute(
            _INSERT_SQL, NEWS_SHORT_GRAPH_DEF["description"], json.dumps(NEWS_SHORT_GRAPH_DEF),
        )
    logger.info("seed_news_short_graph_def up: insert %s", tag)
    await apply_reseeds(pool, _RESEEDS, log_prefix="seed_news_short_graph_def")


async def down(pool) -> None:
    """Remove the row (no task can run without it, so nothing else needs undoing)."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pipeline_templates WHERE slug = 'news_short'")
    logger.info("seed_news_short_graph_def down: removed the news_short row")
