"""Pre-enqueue semantic dedup guard for manually-injected topics.

The topic-discovery pipeline already dedups AUTO-discovered topics
(:mod:`services.topic_dedup` / :mod:`services.topic_dedup_semantic`,
Glad-Labs/poindexter#1561). But a topic injected directly via the
``create_post`` MCP tool → ``POST /api/tasks`` bypasses discovery
entirely, so a near-duplicate of an already-published post can sail
straight into the pipeline, pass every QA rail, and reach
``awaiting_approval``. (Real incident: a manually-seeded "Quantization
and VRAM: how to fit a large language model" generated a post ~75%
identical to the already-published "The VRAM Currency Problem" — same
intro, same examples, same numbers — and only a human caught it.)

This guard closes that gap. It runs the same pgvector similarity search
the ``find_similar_posts`` tool uses (over ``source_table='posts'``) and
refuses to enqueue when the closest published post is at/above
``app_settings.create_post_dedup_threshold`` (default 0.75), unless the
caller explicitly passes ``force=True``.

Design notes
------------

* **Fail loud with remediation** (no-silent-defaults): on a near-duplicate
  the guard raises :class:`DuplicateTopicError`, whose message names the
  colliding post, the cosine score, and exactly how to override.
* **Fail open on infra error**: if the similarity search itself fails
  (pgvector down, embed model missing), the guard logs a warning and
  *allows* creation. A broken dedup index is no reason to refuse the
  operator's explicit request — and "search errored" is not the same
  signal as "similarity exceeds threshold".
* **DB-first config**: the threshold is a tunable
  (``create_post_dedup_threshold``), seeded in ``settings_defaults.py``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# app_settings key + fallback. The default is seeded in
# ``services.settings_defaults.DEFAULTS`` so production reads the DB value;
# this constant is the last-resort fallback when a SiteConfig has no row.
SETTING_KEY = "create_post_dedup_threshold"
DEFAULT_THRESHOLD = 0.75


class DuplicateTopicError(Exception):
    """Raised when a candidate topic is too close to a published post.

    Carries the colliding match so callers (e.g. the HTTP route) can build
    an informative response. ``str(self)`` is a ready-to-surface message.
    """

    def __init__(self, *, topic: str, match: Any, threshold: float) -> None:
        self.topic = topic
        self.match = match
        self.threshold = float(threshold)
        self.similarity = float(getattr(match, "similarity", 0.0) or 0.0)
        meta = getattr(match, "metadata", None) or {}
        self.match_title = meta.get("title") or "(untitled)"
        self.match_post_id = getattr(match, "source_id", "") or ""
        super().__init__(self._message())

    def _message(self) -> str:
        return (
            f"Topic {self.topic!r} is too similar to already-published post "
            f"{self.match_title!r} ({self.match_post_id}) — cosine "
            f"{self.similarity:.2f} ≥ threshold {self.threshold:.2f}. "
            "Refusing to enqueue a near-duplicate. If this is intentional, "
            'retry with force=true (create_post) / "force": true '
            "(POST /api/tasks), or lower app_settings."
            f"{SETTING_KEY}."
        )


def _resolve_threshold(site_config: Any) -> float:
    """Read the dedup threshold from app_settings, defaulting safely.

    ``SiteConfig.get_float`` already coerces a malformed DB value back to the
    supplied default, so no extra error handling is needed here.
    """
    if site_config is None:
        return DEFAULT_THRESHOLD
    return site_config.get_float(SETTING_KEY, DEFAULT_THRESHOLD)


async def _closest_published_post(
    topic: str,
    *,
    threshold: float,
    memory_client: Any | None = None,
) -> Any | None:
    """Return the closest published post at/above ``threshold``, or None.

    ``memory_client`` is injectable for tests; in production it is built
    on demand. Any search failure is swallowed (fail open) and logged.
    """
    try:
        if memory_client is not None:
            hits = await memory_client.find_similar_posts(
                topic, limit=1, min_similarity=threshold
            )
        else:
            from poindexter.memory import MemoryClient

            async with MemoryClient() as mem:
                hits = await mem.find_similar_posts(
                    topic, limit=1, min_similarity=threshold
                )
    except Exception as exc:
        # Fail open: a broken dedup index must not block creation.
        logger.warning(
            "[create_post_dedup] similarity search failed for %r — allowing "
            "creation (fail-open): %s",
            topic[:80],
            exc,
        )
        return None

    if not hits:
        return None
    best = hits[0]
    # ``find_similar_posts`` already filters by min_similarity, but
    # re-check defensively so a lenient backend can't slip a low hit past.
    if float(getattr(best, "similarity", 0.0) or 0.0) >= threshold:
        return best
    return None


async def assert_topic_not_duplicate(
    topic: str,
    *,
    site_config: Any,
    memory_client: Any | None = None,
    force: bool = False,
) -> None:
    """Refuse a topic that near-duplicates an already-published post.

    Args:
        topic: The candidate topic / working title.
        site_config: SiteConfig (DI) — supplies the threshold.
        memory_client: Optional pre-built memory client (tests inject a
            fake; production builds one on demand).
        force: When True, skip the check entirely (operator override).

    Raises:
        DuplicateTopicError: when the closest published post scores at/above
            ``create_post_dedup_threshold``.
    """
    topic = (topic or "").strip()
    if not topic:
        # Empty topics are rejected upstream (route 422 / schema); nothing to
        # compare against here.
        return

    if force:
        logger.info(
            "[create_post_dedup] force=true — skipping dedup check for %r",
            topic[:80],
        )
        return

    threshold = _resolve_threshold(site_config)
    match = await _closest_published_post(
        topic, threshold=threshold, memory_client=memory_client
    )
    if match is not None:
        err = DuplicateTopicError(topic=topic, match=match, threshold=threshold)
        logger.warning("[create_post_dedup] %s", err)
        raise err


__all__ = [
    "DuplicateTopicError",
    "assert_topic_not_duplicate",
    "SETTING_KEY",
    "DEFAULT_THRESHOLD",
]
