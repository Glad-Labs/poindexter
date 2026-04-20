"""TopicSource — data-ingestion Protocol for content-topic discovery.

A TopicSource yields :class:`DiscoveredTopic` candidates that the
content pipeline can turn into blog-post tasks. The runner (see
``services/topic_sources/runner.py``) iterates every registered source
on its configured interval and hands the results off to
:meth:`TopicDiscovery.queue_topics` for dedup + ranking + insertion.

Replaces the god-file ``services/topic_discovery.py`` (952 lines, all
``_scrape_*`` methods mushed together). After Phase F each source
becomes its own file under ``services/topic_sources/`` — HackerNews,
Dev.to, pgvector knowledge, web-search-by-category, codebase-scan, etc.

Register a TopicSource via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.topic_sources"]
    hackernews = "cofounder_agent.services.topic_sources.hackernews:HackerNewsSource"

Per-install config lives in ``app_settings.plugin.topic_source.<name>``
with the same schema that taps + jobs use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class DiscoveredTopic:
    """A topic candidate, as yielded by ``TopicSource.extract()``.

    Fields match ``services.topic_discovery.DiscoveredTopic`` for
    binary compatibility during the Phase F migration. The
    ``TopicDiscovery`` dispatcher accepts either shape.
    """

    title: str
    category: str
    source: str
    source_url: str = ""
    relevance_score: float = 0.0
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    # Whether the dedup pass found a recent duplicate. Set by the
    # dispatcher, not the source itself.
    is_duplicate: bool = False


@runtime_checkable
class TopicSource(Protocol):
    """Topic ingestion plugin contract.

    Attributes:
        name: Unique plugin name (matches the entry_point key + the
            ``source`` label attached to each DiscoveredTopic).
    """

    name: str

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool — sources that need DB access (e.g.
                   # the knowledge source querying pgvector) use it
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        """Return a list of candidate topics from this source.

        Implementations are async methods. The runner awaits each
        registered source in parallel (with per-source isolation —
        one source crashing doesn't kill the others) and aggregates
        results for dedup.

        Args:
            pool: asyncpg connection pool for sources that query the DB.
            config: Per-install config loaded from
                ``app_settings.plugin.topic_source.<name>`` — includes
                any ``enabled`` flag, fetch limits, API keys, etc.

        Returns:
            Zero or more DiscoveredTopic instances. The runner handles
            dedup + classification; sources should focus on raw fetch
            + format conversion.
        """
        ...
