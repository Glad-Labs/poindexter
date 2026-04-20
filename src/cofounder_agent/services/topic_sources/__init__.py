"""services.topic_sources — TopicSource plugin implementations.

Each module in this package is one data source for blog-post topic
ideation. Sources conform to ``plugins.topic_source.TopicSource`` and
register via entry_points under ``poindexter.topic_sources``.

The dispatcher (``services.topic_sources.runner``) iterates every
enabled source, aggregates candidates, and hands them off to
``services.topic_discovery.TopicDiscovery`` for dedup + ranking +
insertion into the content_tasks queue.

Phase F migration (GitHub #70) — split ``services/topic_discovery.py``
into one file per source. First landed: HackerNews + Dev.to. Follow-up
commits migrate the pgvector-knowledge source, the web-search source,
and the codebase-scan source.
"""
