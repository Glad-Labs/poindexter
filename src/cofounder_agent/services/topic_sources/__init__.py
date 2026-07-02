"""services.topic_sources — TopicSource plugin implementations.

Each module in this package is one data source for blog-post topic
ideation. Sources conform to ``plugins.topic_source.TopicSource`` and
register via entry_points under ``poindexter.topic_sources``.

Each niche-bound ``external_taps`` row dispatches one source via the
``tap.builtin_topic_source`` handler, which dedups the candidates and
deposits them into ``topic_pool`` for the batch orchestrator to rank
(``services.topic_sources.runner`` remains as a standalone all-sources
dispatcher surface).

Phase F migration (GitHub #70) — split ``services/topic_discovery.py``
into one file per source. First landed: HackerNews + Dev.to. Follow-up
commits migrate the pgvector-knowledge source, the web-search source,
and the codebase-scan source.
"""
