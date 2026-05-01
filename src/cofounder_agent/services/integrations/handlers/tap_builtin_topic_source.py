"""Handler: ``tap.builtin_topic_source``.

Adapter that turns the existing in-repo ``plugins/topic_source``
entry-point plugins (hackernews, devto, web_search, etc.) into the
declarative ``external_taps`` model.

Under the hood it delegates to
:func:`services.topic_sources.runner.run_all` filtered to a single
source name (taken from ``row.tap_type``), then returns the record
count. The existing topic_sources.runner already dedup-and-stores;
this handler is purely a shape adapter so operators can manage all
data ingestion through one table.

Future taps that write to other target tables won't use this adapter —
they'll use ``singer_subprocess`` or a purpose-built handler.
"""

from __future__ import annotations

import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


@register_handler("tap", "builtin_topic_source")
async def builtin_topic_source(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Invoke the existing topic_source plugin named in ``row.tap_type``.

    Returns ``{"records": N}`` so the runner can tally totals. The actual
    topic persistence happens inside the existing plugin (dedup,
    similarity scoring, content_task row creation) — this handler is
    just the declarative wrapper.
    """
    if pool is None:
        raise RuntimeError("tap.builtin_topic_source: pool unavailable")

    source_name = row.get("tap_type")
    if not source_name:
        raise ValueError(
            "tap.builtin_topic_source: row.tap_type must name a registered "
            "topic_source plugin (e.g. 'hackernews', 'devto')"
        )

    # Lazy import to avoid loading topic_sources until this handler fires.
    from services.topic_sources.runner import run_all as _topic_run_all

    # The runner's signature is ``run_all(pool)`` — site_config is unused
    # because each TopicSource plugin reads its own config from the
    # plugin registry / DB at construction time. Earlier versions of
    # this handler passed site_config as a second arg and crashed at
    # dispatch with TypeError; fixed 2026-05-01.
    summary = await _topic_run_all(pool)

    # Filter the per-source stats to our source_name. When the operator
    # has multiple taps each targeting a different topic_source, each
    # handler invocation runs the full topic_sources pipeline — accept
    # that duplication for now and revisit if it becomes painful.
    matching = [s for s in summary.per_source if s.name == source_name]
    records = sum(s.topics_returned for s in matching)
    errors = [s.error for s in matching if s.error]

    if errors:
        raise RuntimeError(
            f"tap.builtin_topic_source: source {source_name!r} errored: {errors[0]}"
        )

    logger.info(
        "[tap.builtin_topic_source] %s: %d topic(s) ingested",
        source_name, records,
    )
    return {"records": records, "source": source_name}
